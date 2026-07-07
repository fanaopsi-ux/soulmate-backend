"""
Database setup untuk AI VTuber Assistant Backend.
Menggunakan SQLAlchemy dengan SQLite (dev) / PostgreSQL (production).
"""

import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

# Anchor the default SQLite path to this file's location (Backend-v2/), not the
# process cwd — running `python main.py` from the wrong folder was creating
# stray vtuber.db copies outside Backend-v2, which sit unprotected from
# Live Server's file watcher and trigger unwanted full-page reloads.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "vtuber.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

# SQLite perlu connect_args ini, PostgreSQL tidak
connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,  # Set True untuk debug SQL queries
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """
    FastAPI Dependency — Inject database session ke route handler.
    Otomatis close session setelah request selesai.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Inisialisasi database dan buat semua tabel.
    Dipanggil saat aplikasi startup.
    """
    # Import semua model agar SQLAlchemy tahu tabel apa yang perlu dibuat
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    print("[DB] Database initialized successfully!")
