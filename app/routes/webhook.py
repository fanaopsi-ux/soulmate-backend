"""
Webhook Routes — Handler notifikasi pembayaran dari Midtrans & Stripe.

PENTING: Endpoint ini TIDAK perlu autentikasi JWT karena dipanggil langsung
oleh Midtrans/Stripe server. Keamanan dijamin via signature verification.
"""

import os
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Header, status
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.services.midtrans_service import verify_midtrans_signature
from app.services.stripe_service import verify_stripe_webhook
from app.services.discount_service import add_loyalty_points

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Midtrans Webhook
# ============================================================

@router.post("/midtrans")
async def midtrans_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handler webhook notifikasi dari Midtrans.
    
    Dipanggil Midtrans saat ada update status transaksi.
    Tidak perlu autentikasi JWT — keamanan via signature hash.
    
    Flow setelah pembayaran sukses:
    1. Verifikasi signature
    2. Update status transaksi di DB
    3. Tambah loyalty points ke user
    4. Aktivasi subscription user
    """
    data = await request.json()

    order_id       = data.get("order_id", "")
    status_code    = data.get("status_code", "")
    gross_amount   = data.get("gross_amount", "")
    signature_key  = data.get("signature_key", "")
    transaction_status = data.get("transaction_status", "")
    payment_type   = data.get("payment_type", "")

    server_key = os.getenv("MIDTRANS_SERVER_KEY", "")

    # --- 1. Verifikasi Signature ---
    if not verify_midtrans_signature(
        order_id=order_id,
        status_code=status_code,
        gross_amount=gross_amount,
        server_key=server_key,
        received_signature=signature_key,
    ):
        logger.warning(f"Midtrans webhook signature INVALID untuk order {order_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid signature"
        )

    # --- 2. Cari transaksi di DB ---
    transaction = (
        db.query(models.Transaction)
        .filter(models.Transaction.order_id == order_id)
        .first()
    )

    if not transaction:
        logger.error(f"Transaksi {order_id} tidak ditemukan di DB")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not found"
        )

    # Hindari proses ulang jika sudah success
    if transaction.status == models.TransactionStatus.SUCCESS:
        logger.info(f"Transaksi {order_id} sudah diproses sebelumnya")
        return {"message": "Already processed"}

    logger.info(f"Midtrans webhook: {order_id} → {transaction_status}")

    # --- 3. Update status berdasarkan Midtrans status ---
    if transaction_status in ("capture", "settlement"):
        transaction.status = models.TransactionStatus.SUCCESS
        transaction.payment_method = payment_type
        transaction.gateway_transaction_id = data.get("transaction_id", "")
        transaction.paid_at = datetime.now(timezone.utc)

        # Aktivasi subscription
        user = db.query(models.User).filter(
            models.User.id == transaction.user_id
        ).first()

        if user:
            _activate_subscription(user, transaction, db)

            # Tambah loyalty points
            points_earned = _calculate_and_add_points(
                user=user,
                amount_idr=transaction.final_amount,
                points_used=transaction.points_used,
                db=db,
            )
            transaction.points_earned = points_earned

    elif transaction_status == "pending":
        transaction.status = models.TransactionStatus.PENDING

    elif transaction_status in ("deny", "cancel", "failure"):
        transaction.status = models.TransactionStatus.FAILED

    elif transaction_status == "expire":
        transaction.status = models.TransactionStatus.EXPIRED

    db.commit()
    logger.info(f"Transaksi {order_id} diupdate ke {transaction.status}")

    return {"message": "Webhook processed", "order_id": order_id}


# ============================================================
# Stripe Webhook
# ============================================================

@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
    db: Session = Depends(get_db),
):
    """
    Handler webhook event dari Stripe.
    
    Events yang ditangani:
    - checkout.session.completed → Aktivasi subscription
    - payment_intent.payment_failed → Log failure
    """
    payload = await request.body()

    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header"
        )

    # --- 1. Verifikasi Signature ---
    event = verify_stripe_webhook(
        payload=payload,
        sig_header=stripe_signature,
    )

    if not event:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Stripe signature"
        )

    event_type = event["type"]
    logger.info(f"Stripe webhook event: {event_type}")

    # --- 2. Handle Events ---
    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})
        order_id = metadata.get("order_id", "")

        if not order_id:
            logger.error("Stripe webhook: order_id tidak ada di metadata")
            return {"message": "No order_id in metadata"}

        transaction = (
            db.query(models.Transaction)
            .filter(models.Transaction.order_id == order_id)
            .first()
        )

        if not transaction:
            logger.error(f"Stripe: Transaksi {order_id} tidak ditemukan")
            return {"message": "Transaction not found"}

        if transaction.status == models.TransactionStatus.SUCCESS:
            return {"message": "Already processed"}

        # Update transaksi
        transaction.status = models.TransactionStatus.SUCCESS
        transaction.gateway_transaction_id = session.get("payment_intent", "")
        transaction.stripe_session_id = session.get("id", "")
        transaction.paid_at = datetime.now(timezone.utc)

        user = db.query(models.User).filter(
            models.User.id == transaction.user_id
        ).first()

        if user:
            _activate_subscription(user, transaction, db)

            # Konversi USD ke IDR untuk poin (pakai kurs estimasi)
            estimated_idr = transaction.final_amount * 16000
            points_earned = _calculate_and_add_points(
                user=user,
                amount_idr=estimated_idr,
                points_used=transaction.points_used,
                db=db,
            )
            transaction.points_earned = points_earned

        db.commit()
        logger.info(f"Stripe checkout {order_id} → SUCCESS")

    elif event_type == "payment_intent.payment_failed":
        payment_intent = event["data"]["object"]
        logger.warning(f"Stripe payment failed: {payment_intent.get('id')}")
        # Bisa tambah notifikasi email ke user di sini

    return {"message": "Webhook processed", "event": event_type}


# ============================================================
# Helpers
# ============================================================

def _activate_subscription(
    user: models.User,
    transaction: models.Transaction,
    db: Session,
) -> None:
    """Aktivasi subscription user setelah pembayaran sukses."""
    from app.services.midtrans_service import get_package_info
    package_info = get_package_info(transaction.package or "basic")
    duration_days = package_info["duration_days"] if package_info else 30

    # Set atau perpanjang subscription
    now = datetime.now(timezone.utc)
    current_expiry = user.subscription_expires_at

    if current_expiry and current_expiry.replace(tzinfo=timezone.utc) > now:
        # Perpanjang dari expiry yang ada
        new_expiry = current_expiry.replace(tzinfo=timezone.utc) + timedelta(days=duration_days)
    else:
        # Baru atau sudah expired
        new_expiry = now + timedelta(days=duration_days)

    user.subscription_package     = transaction.package
    user.subscription_expires_at  = new_expiry
    user.is_subscription_active   = True
    user.total_spent              += transaction.final_amount

    logger.info(
        f"Subscription activated: user={user.email}, "
        f"package={transaction.package}, expires={new_expiry.date()}"
    )


def _calculate_and_add_points(
    user: models.User,
    amount_idr: float,
    points_used: int,
    db: Session,
) -> int:
    """Hitung dan tambah poin, kurangi poin yang dipakai."""
    from app.services.discount_service import calculate_points_earned
    points_earned = calculate_points_earned(amount_idr)

    # Kurangi poin yang dipakai untuk redeem
    if points_used > 0:
        user.loyalty_points = max(0, user.loyalty_points - points_used)

    # Tambah poin baru
    result = add_loyalty_points(user=user, points=points_earned, db=db)

    if result.get("tier_upgraded"):
        logger.info(
            f"🎉 User {user.email} naik tier ke {user.loyalty_tier}!"
        )

    return points_earned
