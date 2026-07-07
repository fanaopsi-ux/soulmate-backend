"""
Discount & Loyalty Service — Sistem loyalitas, flash discount random, dan kalkulasi harga.

Tier System:
  Bronze:  0   - 499 poin → 0% discount tier
  Silver:  500 - 1999 poin → 5% discount tier
  Gold:    2000+ poin      → 10% discount tier

Flash Discount:
  - Random 5-25% dengan probabilitas berbeda per tier
  - Bronze: 20% chance | Silver: 35% chance | Gold: 50% chance
  - Berlaku 30 menit setelah di-generate
"""

import os
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app import models


# ============================================================
# Tier Management
# ============================================================

TIER_THRESHOLDS = {
    models.LoyaltyTier.BRONZE: 0,
    models.LoyaltyTier.SILVER: 500,
    models.LoyaltyTier.GOLD:   2000,
}

TIER_DISCOUNTS = {
    models.LoyaltyTier.BRONZE: 0.0,
    models.LoyaltyTier.SILVER: 5.0,
    models.LoyaltyTier.GOLD:   10.0,
}

# Probabilitas mendapat flash discount berdasarkan tier (0.0 - 1.0)
TIER_FLASH_PROBABILITY = {
    models.LoyaltyTier.BRONZE: 0.20,  # 20%
    models.LoyaltyTier.SILVER: 0.35,  # 35%
    models.LoyaltyTier.GOLD:   0.50,  # 50%
}

# Range flash discount per tier
TIER_FLASH_RANGE = {
    models.LoyaltyTier.BRONZE: (5, 15),   # 5% - 15%
    models.LoyaltyTier.SILVER: (10, 20),  # 10% - 20%
    models.LoyaltyTier.GOLD:   (15, 25),  # 15% - 25%
}

# Poin yang didapat per 1000 IDR yang dibayar
POINTS_PER_1000_IDR = 1

# Poin yang dibutuhkan untuk redeem 1% diskon
POINTS_FOR_1_PERCENT = 100


def calculate_user_tier(loyalty_points: int) -> models.LoyaltyTier:
    """
    Hitung tier user berdasarkan total poin.
    
    Args:
        loyalty_points: Total poin user
    
    Returns:
        LoyaltyTier enum
    """
    if loyalty_points >= 2000:
        return models.LoyaltyTier.GOLD
    elif loyalty_points >= 500:
        return models.LoyaltyTier.SILVER
    else:
        return models.LoyaltyTier.BRONZE


def update_user_tier(user: models.User, db: Session) -> bool:
    """
    Update tier user jika perlu naik tier.
    
    Returns:
        True jika tier berubah (naik), False jika tidak
    """
    new_tier = calculate_user_tier(user.loyalty_points)
    if new_tier != user.loyalty_tier:
        old_tier = user.loyalty_tier
        user.loyalty_tier = new_tier
        db.commit()
        db.refresh(user)
        print(f"🎉 User {user.email} naik dari {old_tier} ke {new_tier}!")
        return True
    return False


def calculate_points_earned(amount_idr: float) -> int:
    """
    Hitung poin yang didapat dari pembayaran.
    1 poin per 1000 IDR.
    
    Args:
        amount_idr: Jumlah yang dibayar dalam IDR
    
    Returns:
        Jumlah poin yang didapat
    """
    return int(amount_idr / 1000) * POINTS_PER_1000_IDR


def add_loyalty_points(
    user: models.User,
    points: int,
    db: Session
) -> dict:
    """
    Tambah poin ke user dan update tier jika perlu.
    
    Returns:
        Dict dengan info poin baru dan apakah tier naik
    """
    old_points = user.loyalty_points
    user.loyalty_points += points
    tier_changed = update_user_tier(user, db)
    db.commit()
    db.refresh(user)

    return {
        "points_added": points,
        "old_points": old_points,
        "new_points": user.loyalty_points,
        "current_tier": user.loyalty_tier,
        "tier_upgraded": tier_changed,
    }


# ============================================================
# Flash Discount System
# ============================================================

def _generate_flash_code() -> str:
    """Generate kode flash discount random (8 karakter)."""
    chars = string.ascii_uppercase + string.digits
    return "FLASH-" + "".join(random.choices(chars, k=8))


def generate_flash_discount(
    user: models.User,
    db: Session
) -> Optional[dict]:
    """
    Generate flash discount random untuk user.
    Probabilitas berbeda per tier.
    
    Returns:
        Dict dengan info diskon jika beruntung, None jika tidak.
    """
    tier = user.loyalty_tier
    probability = TIER_FLASH_PROBABILITY[tier]

    # Roll the dice 🎲
    if random.random() > probability:
        return None  # Tidak beruntung kali ini

    # Generate persentase diskon
    min_pct, max_pct = TIER_FLASH_RANGE[tier]
    discount_pct = random.randint(min_pct, max_pct)

    # Buat expiry 30 menit dari sekarang
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)

    # Simpan ke database
    code = _generate_flash_code()
    flash_discount = models.Discount(
        code=code,
        description=f"Flash discount {discount_pct}% eksklusif untuk {user.username}!",
        percentage=float(discount_pct),
        is_active=True,
        is_flash_discount=True,
        max_uses=1,  # Sekali pakai
        current_uses=0,
        min_loyalty_required=user.loyalty_points,
        expires_at=expires_at,
    )
    db.add(flash_discount)
    db.commit()
    db.refresh(flash_discount)

    return {
        "has_discount": True,
        "code": code,
        "percentage": discount_pct,
        "expires_at": expires_at.isoformat(),
        "expires_in_minutes": 30,
        "message": f"🎉 Kamu beruntung! Flash discount {discount_pct}% (berlaku 30 menit)",
    }


# ============================================================
# Price Calculator
# ============================================================

def calculate_loyalty_and_discount(
    loyalty_points: int,
    package_price: float,
    loyalty_tier: models.LoyaltyTier = models.LoyaltyTier.BRONZE,
    discount_code: Optional[str] = None,
    points_to_redeem: int = 0,
    db: Optional[Session] = None,
    currency: str = "IDR",
) -> dict:
    """
    Hitung harga akhir setelah semua diskon.
    
    Priority diskon (stacking):
    1. Tier discount (berdasarkan tier loyalty)
    2. Flash/promo discount code
    3. Points redemption (convert poin jadi diskon)
    
    Args:
        loyalty_points: Poin user saat ini
        package_price: Harga paket sebelum diskon
        loyalty_tier: Tier loyalty user
        discount_code: Kode diskon opsional
        points_to_redeem: Poin yang ingin ditukar jadi diskon
        db: Database session (diperlukan jika ada discount_code)
        currency: "IDR" atau "USD"
    
    Returns:
        Dict lengkap dengan breakdown harga
    """
    original_price = package_price
    current_price  = package_price

    breakdown = {
        "original_price":   original_price,
        "currency":         currency,
        "tier":             loyalty_tier,
        "discounts_applied": [],
        "loyalty_discount_amount":  0.0,
        "code_discount_amount":     0.0,
        "points_discount_amount":   0.0,
        "total_discount_amount":    0.0,
        "total_discount_percentage": 0.0,
        "final_price":      original_price,
        "points_used":      0,
        "points_earned":    0,
        "is_valid":         True,
        "error":            None,
    }

    # --- 1. Tier Discount ---
    tier_pct = TIER_DISCOUNTS.get(loyalty_tier, 0.0)
    if tier_pct > 0:
        tier_discount_amt = current_price * (tier_pct / 100)
        current_price -= tier_discount_amt
        breakdown["loyalty_discount_amount"] = tier_discount_amt
        breakdown["discounts_applied"].append({
            "type": "tier",
            "label": f"{loyalty_tier.value.capitalize()} Member Discount",
            "percentage": tier_pct,
            "amount": tier_discount_amt,
        })

    # --- 2. Discount Code ---
    if discount_code and db:
        discount = db.query(models.Discount).filter(
            models.Discount.code == discount_code.upper()
        ).first()

        if discount and discount.is_valid:
            if loyalty_points >= discount.min_loyalty_required:
                code_discount_amt = current_price * (discount.percentage / 100)
                current_price -= code_discount_amt
                breakdown["code_discount_amount"] = code_discount_amt
                breakdown["discounts_applied"].append({
                    "type": "code",
                    "label": f"Kode '{discount_code}'",
                    "percentage": discount.percentage,
                    "amount": code_discount_amt,
                })
                # Update usage count
                discount.current_uses += 1
                db.commit()
            else:
                breakdown["error"] = (
                    f"Kode diskon membutuhkan minimal {discount.min_loyalty_required} poin. "
                    f"Poin kamu: {loyalty_points}"
                )
        else:
            breakdown["error"] = "Kode diskon tidak valid atau sudah expired"

    # --- 3. Points Redemption ---
    if points_to_redeem > 0:
        # Clamp: tidak bisa redeem lebih dari yang dimiliki
        redeemable = min(points_to_redeem, loyalty_points)
        # Setiap 100 poin = 1% diskon
        redeem_pct = min(redeemable / POINTS_FOR_1_PERCENT, 20.0)  # Max 20% dari points
        points_discount_amt = current_price * (redeem_pct / 100)
        actual_points_used = int(redeem_pct * POINTS_FOR_1_PERCENT)

        current_price -= points_discount_amt
        breakdown["points_discount_amount"] = points_discount_amt
        breakdown["points_used"] = actual_points_used
        breakdown["discounts_applied"].append({
            "type": "points",
            "label": f"Redeem {actual_points_used} Poin",
            "percentage": redeem_pct,
            "amount": points_discount_amt,
        })

    # --- Final Calculations ---
    # Harga minimal 1 IDR (tidak boleh negatif atau 0)
    final_price = max(current_price, 1.0)
    total_discount = original_price - final_price
    total_pct = (total_discount / original_price * 100) if original_price > 0 else 0

    # Poin yang akan didapat dari transaksi ini
    points_earned = calculate_points_earned(final_price)

    breakdown.update({
        "final_price":              round(final_price, 2),
        "total_discount_amount":    round(total_discount, 2),
        "total_discount_percentage": round(total_pct, 1),
        "points_earned":            points_earned,
    })

    return breakdown
