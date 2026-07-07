"""
Stripe Payment Service — Pembayaran internasional (USD, EUR, dll).
Docs: https://stripe.com/docs
"""

import os
import logging
from typing import Optional

import stripe

logger = logging.getLogger(__name__)


def _init_stripe():
    """Inisialisasi Stripe dengan secret key dari env."""
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        raise ValueError("STRIPE_SECRET_KEY belum di-set di .env")
    stripe.api_key = key


# ============================================================
# Create Checkout Session
# ============================================================

def create_stripe_checkout(
    amount_usd_cents: int,
    user_email: str,
    package_name: str,
    order_id: str,
    success_url: Optional[str] = None,
    cancel_url: Optional[str] = None,
) -> Optional[dict]:
    """
    Buat Stripe Checkout Session untuk pembayaran internasional.
    
    Args:
        amount_usd_cents: Harga dalam USD cents (e.g. $7.99 = 799)
        user_email: Email customer
        package_name: Nama paket subscription
        order_id: Order ID unik dari sistem kita
        success_url: Redirect setelah pembayaran berhasil
        cancel_url: Redirect jika dibatalkan
    
    Returns:
        Dict dengan session_id dan url pembayaran Stripe
    """
    _init_stripe()

    success = success_url or os.getenv(
        "STRIPE_SUCCESS_URL", "http://localhost:3000/payment/success?session_id={CHECKOUT_SESSION_ID}"
    )
    cancel = cancel_url or os.getenv(
        "STRIPE_CANCEL_URL", "http://localhost:3000/payment/cancel"
    )

    package_label = package_name.capitalize()

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name":        f"AI VTuber {package_label} Plan",
                        "description": f"1 Month subscription - {package_label} tier",
                        "images":      [],  # Bisa tambah URL gambar produk
                    },
                    "unit_amount": amount_usd_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            customer_email=user_email,
            success_url=success,
            cancel_url=cancel,
            metadata={
                "order_id":    order_id,
                "package":     package_name,
                "user_email":  user_email,
            },
            # Expire session dalam 30 menit
            expires_at=int(__import__("time").time()) + 1800,
        )
        return {
            "session_id": session.id,
            "url":        session.url,
            "order_id":   order_id,
            "amount_usd": amount_usd_cents / 100,
        }
    except stripe.error.StripeError as e:
        logger.error(f"Stripe checkout error: {e.user_message}")
        return None
    except Exception as e:
        logger.error(f"Stripe unexpected error: {e}")
        return None


# ============================================================
# Webhook Verification
# ============================================================

def verify_stripe_webhook(
    payload: bytes,
    sig_header: str,
) -> Optional[stripe.Event]:
    """
    Verifikasi dan parse Stripe webhook event.
    
    Args:
        payload: Raw request body (bytes)
        sig_header: Stripe-Signature header value
    
    Returns:
        Stripe Event object jika valid, None jika tidak
    """
    _init_stripe()
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET tidak di-set!")
        return None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
        return event
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook signature verification gagal")
        return None
    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        return None


# ============================================================
# Get Session Info
# ============================================================

def get_stripe_session(session_id: str) -> Optional[dict]:
    """
    Ambil info Stripe Checkout Session berdasarkan ID.
    Berguna untuk verifikasi manual status pembayaran.
    """
    _init_stripe()
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        return {
            "session_id":    session.id,
            "payment_status": session.payment_status,  # paid / unpaid / no_payment_required
            "customer_email": session.customer_email,
            "amount_total":  session.amount_total,      # dalam cents
            "currency":      session.currency,
            "metadata":      dict(session.metadata),
        }
    except Exception as e:
        logger.error(f"Stripe get_session error: {e}")
        return None
