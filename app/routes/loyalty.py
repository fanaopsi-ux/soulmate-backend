"""
Loyalty Routes — Dashboard poin, flash discount, redeem, dan riwayat.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.services.auth_service import get_current_user
from app.services.discount_service import (
    generate_flash_discount,
    calculate_user_tier,
)

router = APIRouter()


# ============================================================
# Routes
# ============================================================

@router.get("/dashboard")
def get_loyalty_dashboard(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Dashboard loyalty user — poin, tier, progress, dan manfaat.
    """
    tier = current_user.loyalty_tier
    points = current_user.loyalty_points

    # Progress bar ke tier berikutnya
    if tier == models.LoyaltyTier.BRONZE:
        next_tier = "Silver"
        needed = 500
        progress_pct = round((points / 500) * 100, 1)
    elif tier == models.LoyaltyTier.SILVER:
        next_tier = "Gold"
        needed = 2000
        progress_pct = round(((points - 500) / 1500) * 100, 1)
    else:
        next_tier = "Maximum (Gold)"
        needed = 0
        progress_pct = 100.0

    # Hitung berapa diskon yang bisa diredeem dari poin
    redeemable_discount_pct = min(points / 100, 20.0)  # Max 20%

    return {
        "user_id":       current_user.id,
        "username":      current_user.username,
        "loyalty": {
            "points":              points,
            "tier":                tier,
            "tier_emoji":          _get_tier_emoji(tier),
            "tier_discount_pct":   current_user.tier_discount_percentage,
            "total_spent_idr":     current_user.total_spent,
            "next_tier":           next_tier,
            "points_to_next_tier": current_user.points_to_next_tier,
            "progress_percentage": min(progress_pct, 100.0),
        },
        "benefits": {
            "bronze": ["Akses fitur dasar", "Poin dari setiap pembelian"],
            "silver": ["5% diskon tier", "Flash discount lebih sering", "Prioritas support"],
            "gold":   ["10% diskon tier", "Flash discount eksklusif", "Akses fitur beta", "API access"],
        },
        "redemption": {
            "points_you_have":         points,
            "redeemable_discount_pct": redeemable_discount_pct,
            "points_per_1pct_discount": 100,
            "max_redemption_pct":       20.0,
        },
    }


@router.get("/flash-discount")
def get_flash_discount(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cek flash discount — ada kesempatan mendapat diskon random!
    
    Probabilitas per tier:
    - Bronze: 20%
    - Silver: 35%  
    - Gold:   50%
    
    Endpoint ini bisa dipanggil ulang tapi diskon yang aktif hanya 1.
    """
    result = generate_flash_discount(user=current_user, db=db)

    if result is None:
        return {
            "has_discount":     False,
            "message":          "Belum beruntung kali ini 😅 Coba lagi nanti!",
            "your_tier":        current_user.loyalty_tier,
            "win_probability":  f"{int(_get_tier_probability(current_user.loyalty_tier) * 100)}%",
        }

    return result


@router.post("/redeem")
def redeem_points(
    points_to_redeem: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Validasi berapa poin yang bisa diredeem.
    Poin tidak langsung dikurangi di sini — pengurangan terjadi saat checkout.
    
    Returns info tentang diskon yang bisa didapat.
    """
    if points_to_redeem <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Poin yang diredeem harus lebih dari 0"
        )

    if points_to_redeem > current_user.loyalty_points:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Poin tidak cukup. Kamu punya {current_user.loyalty_points} poin."
        )

    # Hitung diskon yang bisa didapat
    redeemable = min(points_to_redeem, current_user.loyalty_points)
    discount_pct = min(redeemable / 100, 20.0)

    return {
        "points_requested":   points_to_redeem,
        "points_redeemable":  redeemable,
        "discount_percentage": discount_pct,
        "message": (
            f"✅ {redeemable} poin = {discount_pct:.1f}% diskon! "
            f"Masukkan jumlah poin saat checkout."
        ),
    }


@router.get("/history")
def get_points_history(
    limit: int = 20,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Riwayat poin dari semua transaksi.
    """
    transactions = (
        db.query(models.Transaction)
        .filter(
            models.Transaction.user_id == current_user.id,
            models.Transaction.status == models.TransactionStatus.SUCCESS,
        )
        .order_by(models.Transaction.paid_at.desc())
        .limit(limit)
        .all()
    )

    history = []
    for t in transactions:
        history.append({
            "order_id":     t.order_id,
            "type":         "earned" if t.points_earned > 0 else "redeemed",
            "points_change": t.points_earned - t.points_used,
            "points_earned": t.points_earned,
            "points_used":   t.points_used,
            "package":      t.package,
            "amount":       t.final_amount,
            "currency":     t.currency,
            "date":         t.paid_at,
        })

    return {
        "current_points": current_user.loyalty_points,
        "current_tier":   current_user.loyalty_tier,
        "history":        history,
    }


# ============================================================
# Helpers
# ============================================================

def _get_tier_emoji(tier: models.LoyaltyTier) -> str:
    return {
        models.LoyaltyTier.BRONZE: "🥉",
        models.LoyaltyTier.SILVER: "🥈",
        models.LoyaltyTier.GOLD:   "🥇",
    }.get(tier, "🥉")


def _get_tier_probability(tier: models.LoyaltyTier) -> float:
    from app.services.discount_service import TIER_FLASH_PROBABILITY
    return TIER_FLASH_PROBABILITY.get(tier, 0.2)
