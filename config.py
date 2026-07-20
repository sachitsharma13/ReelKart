# ==============================================================================
# config.py  —  ReelKart Theatres  |  Centralized settings
#
# Single source of truth for every environment-driven setting, instead of
# os.environ.get() calls scattered across db.py / main.py.
#
# Also auto-loads your .env file on import, via python-dotenv — so you no
# longer need to manually export variables into your shell before running
# `uvicorn main:app`. Just `copy env.example .env`, fill it in, and run.
# ==============================================================================

import os
from pathlib import Path
from dotenv import load_dotenv

# Load the .env file that sits next to this file (if present). Values already
# set in the real environment (e.g. by Railway) take priority and are not
# overridden, so this is safe in both local dev and production.
load_dotenv(Path(__file__).resolve().parent / ".env")


class Settings:
    # ── Database ─────────────────────────────────────────────────────────────
    DB_HOST: str     = os.environ.get("DB_HOST", "localhost")
    DB_USER: str     = os.environ.get("DB_USER", "root")
    DB_PASSWORD: str = os.environ.get("DB_PASSWORD", "")
    DB_NAME: str     = os.environ.get("DB_NAME", "reelkartTheatresdb")

    # ── Admin credentials ────────────────────────────────────────────────────
    ADMIN_USERNAME: str      = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD_HASH: str = os.environ.get("ADMIN_PASSWORD_HASH", "")   # bcrypt — preferred
    ADMIN_PASSWORD: str      = os.environ.get("ADMIN_PASSWORD", "")       # plaintext — legacy fallback

    # ── JWT ──────────────────────────────────────────────────────────────────
    JWT_SECRET: str            = os.environ.get("JWT_SECRET", "change-me-in-production-please")
    JWT_ALGORITHM: str         = "HS256"
    TOKEN_EXPIRE_MINUTES: int  = int(os.environ.get("TOKEN_EXPIRE_MINUTES", "60"))

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins, or "*" for all (fine for local
    # dev; tighten this to your deployed frontend's origin in production).
    CORS_ORIGINS_RAW: str = os.environ.get("CORS_ORIGINS", "*")

    # ── Email (SendGrid) ─────────────────────────────────────────────────────
    # Leave SENDGRID_API_KEY blank to disable email sending — booking still
    # works fine, it just skips the confirmation email (logged, not an error).
    SENDGRID_API_KEY: str   = os.environ.get("SENDGRID_API_KEY", "")
    SENDGRID_FROM_EMAIL: str = os.environ.get("SENDGRID_FROM_EMAIL", "noreply@reelkarttheatres.example")
    SENDGRID_FROM_NAME: str  = os.environ.get("SENDGRID_FROM_NAME", "ReelKart Theatres")

    @property
    def CORS_ORIGINS(self) -> list[str]:
        if self.CORS_ORIGINS_RAW.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS_RAW.split(",") if origin.strip()]


settings = Settings()
