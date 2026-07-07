"""
SQLAlchemy Models untuk AI VTuber Assistant Backend.
Tabel: User, Transaction, Discount, ChatSession
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Enum, Text, JSON
)
from sqlalchemy.orm import relationship
import enum

from app.database import Base


# ============================================================
# Enums
# ============================================================

class LoyaltyTier(str, enum.Enum):
    BRONZE = "bronze"   # 0 - 499 poin
    SILVER = "silver"   # 500 - 1999 poin
    GOLD   = "gold"     # 2000+ poin


class TransactionStatus(str, enum.Enum):
    PENDING  = "pending"
    SUCCESS  = "success"
    FAILED   = "failed"
    EXPIRED  = "expired"
    REFUNDED = "refunded"


class PaymentGateway(str, enum.Enum):
    MIDTRANS = "midtrans"   # Pembayaran IDR / Indonesia
    STRIPE   = "stripe"     # Pembayaran Internasional


class SubscriptionPackage(str, enum.Enum):
    BASIC    = "basic"      # Akses basic AI chat
    PRO      = "pro"        # + TTS, memory lebih banyak
    ULTIMATE = "ultimate"   # Semua fitur + prioritas


# ============================================================
# User Model
# ============================================================

class User(Base):
    __tablename__ = "users"

    id             = Column(Integer, primary_key=True, index=True)
    email          = Column(String(255), unique=True, index=True, nullable=False)
    username       = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name      = Column(String(200), nullable=True)

    # Loyalty System
    loyalty_points = Column(Integer, default=0, nullable=False)
    loyalty_tier   = Column(
        Enum(LoyaltyTier),
        default=LoyaltyTier.BRONZE,
        nullable=False
    )
    total_spent    = Column(Float, default=0.0)  # Total uang yang sudah dihabiskan (IDR)

    # Subscription
    subscription_package = Column(
        Enum(SubscriptionPackage),
        nullable=True,
        default=None
    )
    subscription_expires_at = Column(DateTime, nullable=True)
    is_subscription_active  = Column(Boolean, default=False)
    
    # Quota System
    daily_chat_count = Column(Integer, default=0, nullable=False)
    last_chat_date = Column(DateTime, nullable=True)

    # Auth
    is_active  = Column(Boolean, default=True)
    is_admin   = Column(Boolean, default=False)
    has_provided_feedback = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    transactions  = relationship("Transaction", back_populates="user")
    chat_sessions = relationship("ChatSession", back_populates="user")

    def __repr__(self):
        return f"<User id={self.id} email={self.email} tier={self.loyalty_tier}>"

    @property
    def tier_discount_percentage(self) -> float:
        """Persentase diskon berdasarkan tier loyalty."""
        discounts = {
            LoyaltyTier.BRONZE: 0.0,
            LoyaltyTier.SILVER: 5.0,
            LoyaltyTier.GOLD:   10.0,
        }
        return discounts.get(self.loyalty_tier, 0.0)

    @property
    def points_to_next_tier(self) -> int:
        """Berapa poin lagi untuk naik tier."""
        if self.loyalty_tier == LoyaltyTier.BRONZE:
            return max(0, 500 - self.loyalty_points)
        elif self.loyalty_tier == LoyaltyTier.SILVER:
            return max(0, 2000 - self.loyalty_points)
        else:
            return 0  # Sudah Gold


# ============================================================
# Transaction Model
# ============================================================

class Transaction(Base):
    __tablename__ = "transactions"

    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_id       = Column(String(100), unique=True, index=True, nullable=False)

    # Harga
    original_amount    = Column(Float, nullable=False)  # Harga sebelum diskon
    discount_amount    = Column(Float, default=0.0)     # Jumlah diskon
    loyalty_discount   = Column(Float, default=0.0)     # Diskon dari loyalty tier
    flash_discount     = Column(Float, default=0.0)     # Flash discount random
    final_amount       = Column(Float, nullable=False)  # Harga akhir yang dibayar
    currency           = Column(String(10), default="IDR")  # IDR atau USD

    # Payment Info
    gateway            = Column(Enum(PaymentGateway), nullable=False)
    payment_method     = Column(String(50), nullable=True)  # e.g. "credit_card", "gopay"
    status             = Column(Enum(TransactionStatus), default=TransactionStatus.PENDING)
    package            = Column(Enum(SubscriptionPackage), nullable=True)

    # Gateway-specific IDs
    gateway_transaction_id = Column(String(200), nullable=True)  # Midtrans/Stripe ID
    snap_token             = Column(String(500), nullable=True)   # Midtrans Snap Token
    stripe_session_id      = Column(String(200), nullable=True)  # Stripe Session ID

    # Points
    points_earned  = Column(Integer, default=0)   # Poin yang didapat dari transaksi ini
    points_used    = Column(Integer, default=0)   # Poin yang dipakai untuk redeem diskon

    # Timestamps
    created_at  = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    paid_at     = Column(DateTime, nullable=True)  # Waktu konfirmasi pembayaran
    expires_at  = Column(DateTime, nullable=True)  # Waktu expired transaksi

    # Relationships
    user = relationship("User", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction id={self.id} order_id={self.order_id} status={self.status}>"


# ============================================================
# Discount Model
# ============================================================

class Discount(Base):
    __tablename__ = "discounts"

    id                  = Column(Integer, primary_key=True, index=True)
    code                = Column(String(50), unique=True, index=True, nullable=False)
    description         = Column(String(200), nullable=True)
    percentage          = Column(Float, nullable=False)  # e.g. 15.0 untuk 15%
    is_active           = Column(Boolean, default=True)
    is_flash_discount   = Column(Boolean, default=False)  # Flash discount random
    max_uses            = Column(Integer, nullable=True)   # None = unlimited
    current_uses        = Column(Integer, default=0)
    min_loyalty_required = Column(Integer, default=0)     # Minimum poin untuk pakai
    min_purchase_amount = Column(Float, default=0.0)      # Minimum pembelian
    expires_at          = Column(DateTime, nullable=True)
    created_at          = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Discount code={self.code} {self.percentage}%>"

    @property
    def is_valid(self) -> bool:
        """Cek apakah diskon masih valid."""
        now = datetime.now(timezone.utc)
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at.replace(tzinfo=timezone.utc) < now:
            return False
        if self.max_uses and self.current_uses >= self.max_uses:
            return False
        return True


# ============================================================
# ChatSession Model
# ============================================================

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_id     = Column(String(100), unique=True, index=True, nullable=False)
    # session_id juga digunakan sebagai Mem0 memory namespace

    title          = Column(String(200), nullable=True)   # Judul sesi (auto-generated)
    message_count  = Column(Integer, default=0)
    total_tokens   = Column(Integer, default=0)           # Total token yang digunakan
    is_active      = Column(Boolean, default=True)

    created_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_message_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="chat_sessions")

    def __repr__(self):
        return f"<ChatSession id={self.id} user_id={self.user_id}>"


# ============================================================
# ClinicalAssessment Model (Agent 2 output)
# ============================================================

class ClinicalAssessment(Base):
    __tablename__ = "clinical_assessments"

    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_id     = Column(String(100), index=True, nullable=False)

    alam_perasaan               = Column(JSON, default=list)
    interaksi_selama_wawancara  = Column(String(200), nullable=True)
    persepsi_halusinasi_jenis   = Column(JSON, default=list)
    isi_pikir                   = Column(JSON, default=list)
    koping_adaptif              = Column(JSON, default=list)
    koping_maladaptif           = Column(JSON, default=list)
    hubungan_sosial             = Column(String(200), nullable=True)
    konsep_diri                 = Column(String(200), nullable=True)
    resiko_bunuh_diri_terdeteksi = Column(Boolean, default=False, nullable=False)
    catatan_klinis_a2           = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<ClinicalAssessment id={self.id} user_id={self.user_id} risk={self.resiko_bunuh_diri_terdeteksi}>"


# ============================================================
# A2Directive Model (Agent 2 -> Agent 1 context injection)
# ============================================================

class A2Directive(Base):
    __tablename__ = "a2_directives"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(100), index=True, nullable=False)
    directive  = Column(Text, nullable=False)
    is_used    = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<A2Directive id={self.id} session_id={self.session_id} used={self.is_used}>"


# ============================================================
# Feedback Model
# ============================================================

class Feedback(Base):
    __tablename__ = "feedbacks"

    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, ForeignKey("users.id"), nullable=True)  # Bisa null jika tidak login
    name           = Column(String(100), nullable=False)
    age            = Column(Integer, nullable=True)
    gender         = Column(String(50), nullable=True)
    occupation     = Column(String(200), nullable=False)
    necessity      = Column(String(50), nullable=False)
    rating         = Column(Integer, nullable=False)
    feedback_text  = Column(Text, nullable=False)
    category       = Column(String(100), nullable=False)
    
    created_at     = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Feedback id={self.id} user={self.name} rating={self.rating}>"
