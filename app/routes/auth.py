"""
Auth Routes — Register, Login, Profile, Loyalty Info.
"""

from datetime import datetime, timezone
import os
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, field_validator
import sqlalchemy.orm
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.services.auth_service import (
    hash_password, verify_password,
    create_access_token, get_current_user,
    decode_token_allow_expired
)
from app.services.discount_service import update_user_tier

from google.oauth2 import id_token
from google.auth.transport import requests

router = APIRouter()


# ============================================================
# Pydantic Schemas
# ============================================================

class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str
    full_name: str | None = None

    @field_validator("username")
    @classmethod
    def username_valid(cls, v):
        if len(v) < 3:
            raise ValueError("Username minimal 3 karakter")
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username hanya boleh huruf, angka, underscore, dan dash")
        return v.lower()

    @field_validator("password")
    @classmethod
    def password_strong(cls, v):
        if len(v) < 8:
            raise ValueError("Password minimal 8 karakter")
        return v


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    email: str
    username: str
    loyalty_tier: str
    loyalty_points: int


class GoogleLoginRequest(BaseModel):
    credential: str


class UserProfileResponse(BaseModel):
    id: int
    email: str
    username: str
    full_name: str | None
    loyalty_points: int
    loyalty_tier: str
    total_spent: float
    subscription_package: str | None
    is_subscription_active: bool
    subscription_expires_at: datetime | None
    points_to_next_tier: int
    tier_discount_percentage: float
    daily_chat_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ============================================================
# Routes
# ============================================================

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(req: RegisterRequest, db: Session = Depends(get_db)):
    """
    Daftar akun baru.
    Otomatis buat akun dengan tier Bronze dan 50 welcome points.
    """
    # Cek email sudah terdaftar
    if db.query(models.User).filter(models.User.email == req.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email sudah terdaftar"
        )
    # Cek username sudah terdaftar
    if db.query(models.User).filter(models.User.username == req.username).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username sudah dipakai"
        )

    user = models.User(
        email=req.email,
        username=req.username,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        loyalty_points=50,  # Welcome bonus 50 poin 🎁
        loyalty_tier=models.LoyaltyTier.BRONZE,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(data={"sub": user.email})

    return {
        "message": "Selamat datang di AI VTuber Assistant! 🎉 Kamu dapat 50 welcome points!",
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "email": user.email,
        "username": user.username,
        "loyalty_tier": user.loyalty_tier,
        "loyalty_points": user.loyalty_points,
    }


@router.post("/login", response_model=LoginResponse)
def login_user(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """
    Login dengan email + password. Return JWT token.
    Mendukung OAuth2PasswordRequestForm (username field = email).
    """
    # Cari user by email (field "username" di form = email)
    user = db.query(models.User).filter(
        models.User.email == form_data.username
    ).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email atau password salah",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun kamu tidak aktif. Hubungi support."
        )

    token = create_access_token(data={"sub": user.email})

    return LoginResponse(
        access_token=token,
        user_id=user.id,
        email=user.email,
        username=user.username,
        loyalty_tier=user.loyalty_tier,
        loyalty_points=user.loyalty_points,
    )


@router.get("/me", response_model=UserProfileResponse)
def get_my_profile(current_user: models.User = Depends(get_current_user)):
    """
    Ambil profil user yang sedang login.
    Butuh Bearer token di header.
    """
    return UserProfileResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        full_name=current_user.full_name,
        loyalty_points=current_user.loyalty_points,
        loyalty_tier=current_user.loyalty_tier,
        total_spent=current_user.total_spent,
        subscription_package=current_user.subscription_package,
        is_subscription_active=current_user.is_subscription_active,
        subscription_expires_at=current_user.subscription_expires_at,
        points_to_next_tier=current_user.points_to_next_tier,
        tier_discount_percentage=current_user.tier_discount_percentage,
        created_at=current_user.created_at,
    )


@router.put("/me/update")
def update_profile(
    full_name: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Update profil user (full name)."""
    if full_name is not None:
        current_user.full_name = full_name
        db.commit()
    return {"message": "Profil berhasil diupdate", "full_name": current_user.full_name}


@router.post("/refresh", response_model=RefreshResponse)
def refresh_token(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Perpanjang token JWT.
    Menerima token yang masih VALID MAUPUN SUDAH EXPIRED —
    selama signature-nya sah. Ini mencegah user terpaksa login ulang
    hanya karena sesi habis saat sedang aktif pakai aplikasi.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tidak ditemukan")

    token = auth_header[len("Bearer "):].strip()
    payload = decode_token_allow_expired(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tidak valid atau signature salah")

    email: str = payload.get("sub")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tidak valid")

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User tidak ditemukan atau tidak aktif")

    new_token = create_access_token(data={"sub": user.email})
    return RefreshResponse(access_token=new_token)


@router.post("/google", response_model=LoginResponse)
def google_login(req: GoogleLoginRequest, db: Session = Depends(get_db)):
    """Login atau Register menggunakan Google ID Token."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google Client ID belum dikonfigurasi di server"
        )
    
    try:
        idinfo = id_token.verify_oauth2_token(
            req.credential, 
            requests.Request(), 
            client_id,
            clock_skew_in_seconds=120  # Toleransi 2 menit
        )
        email = idinfo['email']
        name = idinfo.get('name', '')
        
        # Cek apakah user sudah terdaftar
        user = db.query(models.User).filter(models.User.email == email).first()
        
        if not user:
            # Generate random password & username for new Google user
            import uuid
            import string
            import random
            
            base_username = email.split('@')[0].lower()
            safe_username = ''.join(c for c in base_username if c.isalnum())
            username = safe_username
            while db.query(models.User).filter(models.User.username == username).first():
                username = f"{safe_username}_{random.randint(100, 999)}"
                
            random_password = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
            
            user = models.User(
                email=email,
                username=username,
                hashed_password=hash_password(random_password),
                full_name=name,
                loyalty_points=50,  # Welcome bonus
                loyalty_tier=models.LoyaltyTier.BRONZE,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            
        elif not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Akun kamu tidak aktif. Hubungi support."
            )
            
        # Generate token
        token = create_access_token(data={"sub": user.email})
        
        return LoginResponse(
            access_token=token,
            user_id=user.id,
            email=user.email,
            username=user.username,
            loyalty_tier=user.loyalty_tier,
            loyalty_points=user.loyalty_points,
        )
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token Google tidak valid: {str(e)}"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Terjadi kesalahan saat login Google: {str(e)} ({type(e).__name__})"
        )