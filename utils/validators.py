"""Input validation helpers."""

from __future__ import annotations

import re

_USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")
_STARS_RE = re.compile(r"stars[:\s]+(\d+)", re.IGNORECASE)
_PREMIUM_RE = re.compile(r"premium[:\s]+(\d+)", re.IGNORECASE)


def parse_tg_username(text: str) -> str | None:
    """Extract a bare (no @) Telegram username from order description."""
    for word in text.split():
        if _USERNAME_RE.match(word):
            return word.lstrip("@")
    return None


def parse_stars_amount(description: str) -> int | None:
    """Extract star count from e.g. 'stars:50' in the description."""
    if m := _STARS_RE.search(description):
        return int(m.group(1))
    return None


def parse_premium_months(description: str) -> int | None:
    """Extract premium months from e.g. 'premium:3' in the description."""
    if m := _PREMIUM_RE.search(description):
        return int(m.group(1))
    return None
