"""
AI VTuber Assistant Backend — Entry Point
FastAPI app dengan CORS, routing, dan startup events.
"""

import sys
import io
import logging
from contextlib import asynccontextmanager

# Fix Windows encoding issue — emoji in Groq responses crash cp1252
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import os
import sentry_sdk
import structlog
import secure

# Load .env di awal sebelum import apapun
load_dotenv()

from app.routes import auth, subscription, webhook, loyalty, ai, mood, feedback
from app.database import init_db
from app.limiter import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

# ============================================================
# Logging Setup
# ============================================================

# Use UTF-8 StreamHandler to safely handle emoji in log messages
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(sys.stdout),
)
logger = structlog.get_logger(__name__)

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN", ""),
    traces_sample_rate=1.0,
)


# ============================================================
# Startup & Shutdown
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB. Shutdown: cleanup."""
    logger.info("[STARTUP] Starting AI VTuber Assistant Backend...")
    init_db()
    logger.info("[STARTUP] Database ready!")
    yield
    logger.info("[SHUTDOWN] Backend shutting down...")


# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(
    title="AI VTuber Assistant API",
    description="""
## 🎭 AI VTuber Assistant Backend

Backend service untuk AI VTuber interaktif dengan:
- 🤖 **AI Chat** via Groq (Llama 3)
- 🧠 **Persistent Memory** via Mem0
- 🎙️ **Text-to-Speech** via ElevenLabs
- 💳 **Payment** via Midtrans (IDR) & Stripe (Internasional)
- 🎁 **Loyalty System** dengan Bronze/Silver/Gold tiers
- ⚡ **Flash Discounts** random per tier

### Authentication
Gunakan Bearer token dari `/api/auth/login` untuk endpoint yang butuh autentikasi.
    """,
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ============================================================
# CORS Middleware
# ============================================================

allowed_origins_str = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5500,http://localhost:5500"
)
allowed_origins = [o.strip() for o in allowed_origins_str.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

secure_headers = secure.Secure()

@app.middleware("http")
async def set_secure_headers(request: Request, call_next):
    response = await call_next(request)
    secure_headers.set_headers(response)
    return response


# ============================================================
# Global Exception Handler
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": "Terjadi kesalahan di server. Tim kami sudah diberitahu.",
        },
    )


# ============================================================
# Routes Registration
# ============================================================

app.include_router(auth.router,         prefix="/api/auth",         tags=["🔐 Auth"])
app.include_router(subscription.router, prefix="/api/subscription", tags=["💳 Subscription"])
app.include_router(loyalty.router,      prefix="/api/loyalty",      tags=["🎁 Loyalty"])
app.include_router(feedback.router,     prefix="/api/feedback",     tags=["📝 Feedback"])
app.include_router(ai.router,           prefix="/api/ai",           tags=["🤖 AI"])
app.include_router(webhook.router,      prefix="/api/webhook",      tags=["🔔 Webhooks"])
app.include_router(mood.router,         prefix="/api/mood",         tags=["🧠 Mood"])


# ============================================================
# Root & Health Check
# ============================================================

@app.get("/", tags=["Health"])
def root():
    return {
        "status":  "ok",
        "message": "🎭 AI VTuber Assistant Backend v2.0 is running!",
        "docs":    "/docs",
    }


@app.get("/health", tags=["Health"])
def health_check():
    """Health check endpoint untuk monitoring."""
    return {
        "status":  "healthy",
        "version": "2.0.0",
        "services": {
            "groq":       bool(os.getenv("GROQ_API_KEY")),
            "mem0":       bool(os.getenv("MEM0_API_KEY")),
            "elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
            "midtrans":   bool(os.getenv("MIDTRANS_SERVER_KEY")),
            "stripe":     bool(os.getenv("STRIPE_SECRET_KEY")),
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )