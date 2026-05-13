from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Required env var {key!r} is not set. See .env.example")
    return val


def _int_list(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
ADMIN_IDS: list[int] = _int_list(os.getenv("ADMIN_IDS", ""))

# ── FunPay ────────────────────────────────────────────────────────────────────
FUNPAY_GOLDEN_KEY: str = _require("FUNPAY_GOLDEN_KEY")
FUNPAY_USER_AGENT: Optional[str] = os.getenv("FUNPAY_USER_AGENT")

# ── Fragment / TON ────────────────────────────────────────────────────────────
TON_WALLET_MNEMONIC: str = _require("TON_WALLET_MNEMONIC")
FRAGMENT_PROXY: Optional[str] = os.getenv("FRAGMENT_PROXY") or None

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/bot.db")

# ── Misc ──────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOGS_DIR: Path = BASE_DIR / "logs"
DATA_DIR: Path = BASE_DIR / "data"
BROWSER_DATA_DIR: Path = BASE_DIR / "browser_data"

# ensure dirs exist at import time
for _d in (LOGS_DIR, DATA_DIR, BROWSER_DATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)
