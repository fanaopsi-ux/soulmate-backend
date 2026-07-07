"""
Routes package init — expose semua router.
"""
from app.routes import auth, subscription, webhook, loyalty, ai

__all__ = ["auth", "subscription", "webhook", "loyalty", "ai"]
