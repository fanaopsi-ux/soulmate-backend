"""
Auth Service — JWT token management dan password hashing.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

# ============================================================
# Config
# ============================================================

SECRET_KEY  = os.getenv("SECRET_KEY", "change-this-in-production-please")
ALGORITHM   = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# ============================================================
# Password Utilities
# ============================================================

def hash_password(password: str) -> str:
    """Hash password menggunakan bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifikasi password plain vs hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ============================================================
# JWT Utilities
# ============================================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Buat JWT access token.
    
    Args:
        data: Payload yang akan di-encode (biasanya {"sub": user_email})
        expires_delta: Custom expiry time, default dari env var
    
    Returns:
        JWT token string
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode JWT token, return None jika invalid."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def decode_token_allow_expired(token: str) -> Optional[dict]:
    """
    Decode JWT token bahkan jika sudah expired.
    Dipakai oleh endpoint /refresh agar user bisa minta token baru
    meski token lama sudah kadaluarsa — asalkan signature-nya masih valid.
    """
    try:
        payload = jwt.decode(
            token, SECRET_KEY, algorithms=[ALGORITHM],
            options={"verify_exp": False}
        )
        return payload
    except JWTError:
        return None


# ============================================================
# FastAPI Dependencies
# ============================================================

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> models.User:
    """
    FastAPI dependency — ambil user dari JWT token.
    Gunakan di route yang butuh autentikasi: Depends(get_current_user)
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token tidak valid atau sudah expired",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_token(token)
    if payload is None:
        raise credentials_exception

    email: str = payload.get("sub")
    if email is None:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun kamu tidak aktif"
        )

    return user


def get_current_active_subscriber(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """
    FastAPI dependency — pastikan user punya subscription aktif.
    Gunakan untuk endpoint AI chat & TTS.
    """
    now = datetime.now(timezone.utc)
    is_expired = (
        current_user.subscription_expires_at is not None
        and current_user.subscription_expires_at.replace(tzinfo=timezone.utc) < now
    )

    if not current_user.is_subscription_active or is_expired:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Kamu belum berlangganan atau subscription sudah expired. "
                   "Silakan checkout di /api/subscription/packages"
        )
    return current_user

def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db)
) -> Optional[models.User]:
    """
    FastAPI dependency — ambil user dari JWT token (opsional).
    Jika token tidak ada, tidak akan error dan return None.
    """
    if not token:
        return None

    payload = decode_token(token)
    if payload is None:
        return None

    email: str = payload.get("sub")
    if email is None:
        return None

    user = db.query(models.User).filter(models.User.email == email).first()
    return user
