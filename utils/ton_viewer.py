"""Helpers for TonViewer transaction links."""

from __future__ import annotations

TON_VIEWER_BASE = "https://tonviewer.com/transaction"


def tx_url(tx_hash: str) -> str:
    """Build a TonViewer link for a transaction hash."""
    return f"{TON_VIEWER_BASE}/{tx_hash}"
