"""
Subscription Routes — Checkout, daftar paket, status subscription.
Support: Midtrans (IDR) & Stripe (Internasional/USD)
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.services.auth_service import get_current_user
from app.services.discount_service import calculate_loyalty_and_discount
from app.services.midtrans_service import (
    create_payment_link, SUBSCRIPTION_PACKAGES, get_package_info
)
from app.services.stripe_service import create_stripe_checkout

router = APIRouter()


# ============================================================
# Pydantic Schemas
# ============================================================

class CheckoutRequest(BaseModel):
    package_name: str            # "basic" | "pro" | "ultimate"
    currency: str = "IDR"        # "IDR" (Midtrans) atau "USD" (Stripe)
    discount_code: Optional[str] = None
    points_to_redeem: int = 0


class CheckoutResponse(BaseModel):
    message: str
    order_id: str
    gateway: str
    pricing_details: dict
    payment: dict


# ============================================================
# Routes
# ============================================================

@router.get("/packages")
def get_packages():
    """
    Daftar semua paket subscription yang tersedia.
    Tidak perlu login untuk melihat.
    """
    packages = []
    for key, pkg in SUBSCRIPTION_PACKAGES.items():
        packages.append({
            "id":          key,
            "name":        pkg["name"],
            "emoji":       pkg["emoji"],
            "price_idr":   pkg["price_idr"],
            "price_usd":   pkg["price_usd"],
            "duration_days": pkg["duration_days"],
            "features":    pkg["features"],
        })
    return {"packages": packages}


@router.post("/checkout", response_model=CheckoutResponse)
def checkout_payment(
    req: CheckoutRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Buat transaksi pembayaran.
    - currency=IDR  → Midtrans Snap (GoPay, QRIS, transfer bank, kartu kredit)
    - currency=USD  → Stripe (kartu kredit internasional)
    
    Diskon dihitung otomatis berdasarkan:
    1. Loyalty tier user
    2. Kode diskon (jika ada)
    3. Poin yang ingin diredeem
    """
    # Validasi paket
    package = get_package_info(req.package_name)
    if not package:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Paket '{req.package_name}' tidak ditemukan. Pilih: basic, pro, ultimate"
        )

    # Tentukan harga awal berdasarkan currency
    currency = req.currency.upper()
    if currency == "IDR":
        base_price = float(package["price_idr"])
    elif currency == "USD":
        base_price = package["price_usd"]
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Currency harus 'IDR' atau 'USD'"
        )

    # Hitung diskon
    pricing = calculate_loyalty_and_discount(
        loyalty_points=current_user.loyalty_points,
        package_price=base_price,
        loyalty_tier=current_user.loyalty_tier,
        discount_code=req.discount_code,
        points_to_redeem=req.points_to_redeem,
        db=db,
        currency=currency,
    )

    # Generate order ID unik
    order_id = f"VTUBER-{uuid.uuid4().hex[:12].upper()}"
    final_price = pricing["final_price"]

    # Buat transaksi di DB (status pending)
    transaction = models.Transaction(
        user_id=current_user.id,
        order_id=order_id,
        original_amount=base_price,
        discount_amount=pricing["total_discount_amount"],
        loyalty_discount=pricing["loyalty_discount_amount"],
        flash_discount=pricing["code_discount_amount"],
        final_amount=final_price,
        currency=currency,
        package=req.package_name,
        status=models.TransactionStatus.PENDING,
        points_used=pricing["points_used"],
    )

    if currency == "IDR":
        # --- Midtrans ---
        transaction.gateway = models.PaymentGateway.MIDTRANS
        db.add(transaction)
        db.flush()  # Dapatkan ID sebelum commit

        payment_res = create_payment_link(
            gross_amount=int(final_price),
            user_email=current_user.email,
            first_name=current_user.username,
            package_name=req.package_name,
            order_id=order_id,
        )

        if not payment_res:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Gagal membuat transaksi Midtrans. Coba lagi nanti."
            )

        transaction.snap_token = payment_res.get("token")
        db.commit()

        return CheckoutResponse(
            message="Link pembayaran Midtrans berhasil dibuat! 🎉",
            order_id=order_id,
            gateway="midtrans",
            pricing_details=pricing,
            payment=payment_res,
        )

    else:
        # --- Stripe ---
        transaction.gateway = models.PaymentGateway.STRIPE
        db.add(transaction)
        db.flush()

        # Stripe pakai cents (USD * 100)
        amount_cents = int(final_price * 100)

        payment_res = create_stripe_checkout(
            amount_usd_cents=amount_cents,
            user_email=current_user.email,
            package_name=req.package_name,
            order_id=order_id,
        )

        if not payment_res:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Gagal membuat transaksi Stripe. Coba lagi nanti."
            )

        transaction.stripe_session_id = payment_res.get("session_id")
        db.commit()

        return CheckoutResponse(
            message="Link pembayaran Stripe berhasil dibuat! 🌐",
            order_id=order_id,
            gateway="stripe",
            pricing_details=pricing,
            payment=payment_res,
        )


@router.get("/status")
def get_subscription_status(
    current_user: models.User = Depends(get_current_user),
):
    """Cek status subscription user yang sedang login."""
    now = datetime.now(timezone.utc)
    is_expired = (
        current_user.subscription_expires_at is not None
        and current_user.subscription_expires_at.replace(tzinfo=timezone.utc) < now
    )

    return {
        "user_id":               current_user.id,
        "username":              current_user.username,
        "subscription_package":  current_user.subscription_package,
        "is_active":             current_user.is_subscription_active and not is_expired,
        "expires_at":            current_user.subscription_expires_at,
        "is_expired":            is_expired,
        "days_remaining":        (
            max(0, (current_user.subscription_expires_at.replace(tzinfo=timezone.utc) - now).days)
            if current_user.subscription_expires_at else 0
        ),
    }


@router.get("/history")
def get_transaction_history(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Riwayat transaksi user."""
    transactions = (
        db.query(models.Transaction)
        .filter(models.Transaction.user_id == current_user.id)
        .order_by(models.Transaction.created_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "transactions": [
            {
                "order_id":      t.order_id,
                "package":       t.package,
                "original_amount": t.original_amount,
                "final_amount":  t.final_amount,
                "currency":      t.currency,
                "gateway":       t.gateway,
                "status":        t.status,
                "points_earned": t.points_earned,
                "created_at":    t.created_at,
                "paid_at":       t.paid_at,
            }
            for t in transactions
        ]
    }