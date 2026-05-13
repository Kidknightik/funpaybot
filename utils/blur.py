"""Username obfuscation helpers."""

from __future__ import annotations


def blur_username(username: str) -> str:
    """
    Obfuscates a Telegram username, keeping first and last character visible.
    Example: Kidknightik → K*d*n*g*t*k
    """
    username = username.lstrip("@")
    if len(username) <= 2:
        return username[0] + "*" * (len(username) - 1)
    chars = list(username)
    for i in range(1, len(chars) - 1, 2):
        chars[i] = "*"
    return "".join(chars)


def format_recipient(username: str) -> str:
    """Returns 'nickname: K*d*n*g*t*k' style string."""
    return f"nickname: {blur_username(username)}"
