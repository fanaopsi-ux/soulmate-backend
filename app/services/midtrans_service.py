"""
Midtrans Payment Service — Snap API untuk pembayaran IDR/Indonesia.
Docs: https://docs.midtrans.com/
"""

import os
import uuid
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import midtransclient

logger = logging.getLogger(__name__)

# ============================================================
# Midtrans Client Setup
# ============================================================

def _get_snap_client() -> midtransclient.Snap:
    """Buat Midtrans Snap client dari env vars."""
    server_key    = os.getenv("MIDTRANS_SERVER_KEY", "")
    is_production = os.getenv("MIDTRANS_IS_PRODUCTION", "False").lower() == "true"

    if not server_key:
        raise ValueError("MIDTRANS_SERVER_KEY belum di-set di .env")

    return midtransclient.Snap(
        is_production=is_production,
        server_key=server_key,
    )


# ============================================================
# Paket Subscription
# ============================================================

SUBSCRIPTION_PACKAGES = {
    "basic": {
        "name": "Basic (Weekly)",
        "price_idr": 10_000,
        "price_usd": 0.50,
        "duration_days": 7,
        "features": [
            "AI Chat VTuber (Unlimited)",
            "Text-to-Speech",
            "Memori percakapan 7 hari",
        ],
        "emoji": "🌱",
    },
    "pro": {
        "name": "Pro (Monthly)",
        "price_idr": 49_900,
        "price_usd": 3.00,
        "duration_days": 30,
        "features": [
            "AI Chat VTuber (Unlimited)",
            "Text-to-Speech (ElevenLabs)",
            "Memori percakapan 30 hari",
            "Pilihan Karakter VTuber (Emily & Kai)",
            "Prioritas response",
        ],
        "emoji": "⭐",
    },
}


def get_package_info(package_name: str) -> Optional[dict]:
    """Ambil info paket berdasarkan nama."""
    return SUBSCRIPTION_PACKAGES.get(package_name.lower())


# ============================================================
# Create Payment
# ============================================================

def create_payment_link(
    gross_amount: int,
    user_email: str,
    first_name: str,
    package_name: str = "pro",
    order_id: Optional[str] = None,
    duration_minutes: int = 60,  # Link expired dalam 60 menit
) -> Optional[dict]:
    """
    Buat Midtrans Snap token untuk pembayaran.
    
    Args:
        gross_amount: Harga dalam IDR (integer, sudah dibulatkan)
        user_email: Email customer
        first_name: Nama depan customer
        package_name: Nama paket subscription
        order_id: Custom order ID, auto-generate jika None
        duration_minutes: Berapa menit link pembayaran berlaku
    
    Returns:
        Dict berisi token, redirect_url, dan order_id. None jika gagal.
    """
    if order_id is None:
        order_id = f"VTUBER-{uuid.uuid4().hex[:12].upper()}"

    package = get_package_info(package_name)
    package_label = package["name"] if package else package_name.capitalize()

    param = {
        "transaction_details": {
            "order_id":    order_id,
            "gross_amount": gross_amount,
        },
        "item_details": [{
            "id":       package_name,
            "price":    gross_amount,
            "quantity": 1,
            "name":     f"AI VTuber {package_label} Plan (1 Bulan)",
        }],
        "customer_details": {
            "email":      user_email,
            "first_name": first_name,
        },
        "expiry": {
            "unit":     "minute",
            "duration": duration_minutes,
        },
        "callbacks": {
            "finish":  os.getenv("MIDTRANS_FINISH_URL", "http://localhost:3000/payment/success"),
            "unfinish": os.getenv("MIDTRANS_UNFINISH_URL", "http://localhost:3000/payment/pending"),
            "error":   os.getenv("MIDTRANS_ERROR_URL", "http://localhost:3000/payment/error"),
        },
    }

    try:
        snap = _get_snap_client()
        transaction = snap.create_transaction(param)
        return {
            "order_id":     order_id,
            "token":        transaction.get("token"),
            "redirect_url": transaction.get("redirect_url"),
            "expires_at":   (
                datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
            ).isoformat(),
        }
    except Exception as e:
        logger.error(f"Midtrans create_transaction error: {e}")
        return None


# ============================================================
# Webhook Signature Verification
# ============================================================

def verify_midtrans_signature(
    order_id: str,
    status_code: str,
    gross_amount: str,
    server_key: str,
    received_signature: str,
) -> bool:
    """
    Verifikasi signature webhook dari Midtrans.
    Formula: SHA512(order_id + status_code + gross_amount + server_key)
    
    Returns:
        True jika signature valid, False jika tidak
    """
    raw = f"{order_id}{status_code}{gross_amount}{server_key}"
    expected = hashlib.sha512(raw.encode()).hexdigest()
    return expected == received_signature


# ============================================================
# Check Transaction Status
# ============================================================

def get_transaction_status(order_id: str) -> Optional[dict]:
    """
    Cek status transaksi via Midtrans API.
    
    Returns:
        Raw response dari Midtrans, None jika error
    """
    try:
        server_key    = os.getenv("MIDTRANS_SERVER_KEY", "")
        is_production = os.getenv("MIDTRANS_IS_PRODUCTION", "False").lower() == "true"
        core_api = midtransclient.CoreApi(
            is_production=is_production,
            server_key=server_key,
        )
        return core_api.transactions.status(order_id)
    except Exception as e:
        logger.error(f"Midtrans get_status error: {e}")
        return None
