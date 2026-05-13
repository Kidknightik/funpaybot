"""
Fragment automation for gifting Telegram Stars and Premium.

Flow:
1. Navigate to fragment.com/gift/stars?recipient=@USER&quantity=N
   (or /gift/premium for Premium)
2. Click the "Buy" / "Gift" button
3. TON Connect modal opens — we intercept the `ton://` deep-link and sign
   the transaction with pytoniq using the configured wallet mnemonic.
4. Wait for the success page and extract the transaction hash.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger
from playwright.async_api import Page, BrowserContext, TimeoutError as PWTimeout

from utils.ton_viewer import tx_url

_TX_HASH_RE = re.compile(r"[0-9a-fA-F]{64}")
_TONVIEWER_RE = re.compile(r"tonviewer\.com/transaction/([0-9a-fA-F]+)")


@dataclass
class GiftResult:
    success: bool
    tx_hash: Optional[str] = None
    ton_viewer_url: Optional[str] = None
    error: Optional[str] = None


class FragmentGifter:
    def __init__(self, context: BrowserContext) -> None:
        self._ctx = context

    async def gift_stars(self, username: str, amount: int) -> GiftResult:
        username = username.lstrip("@")
        url = f"https://fragment.com/gift/stars?recipient=%40{username}&quantity={amount}"
        return await self._do_gift(url, f"stars:{amount} → @{username}")

    async def gift_premium(self, username: str, months: int) -> GiftResult:
        username = username.lstrip("@")
        url = f"https://fragment.com/gift/premium?recipient=%40{username}&months={months}"
        return await self._do_gift(url, f"premium:{months}mo → @{username}")

    async def _do_gift(self, url: str, label: str) -> GiftResult:
        page: Page = await self._ctx.new_page()
        try:
            logger.info(f"Fragment gift: {label}")
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")

            # Accept cookies if shown
            try:
                await page.click("button:has-text('Accept')", timeout=3_000)
            except PWTimeout:
                pass

            # Click the primary CTA (Buy / Gift)
            gift_btn = page.locator(
                "button.btn-primary, a.btn-primary, button:has-text('Buy'), button:has-text('Gift')"
            ).first
            await gift_btn.wait_for(state="visible", timeout=10_000)
            await gift_btn.click()

            # Fragment opens a TON Connect modal with wallet options.
            # We look for the "Tonkeeper" option or a generic QR/deeplink,
            # then extract the TON payload from the page's JS state.
            tx_hash = await self._handle_ton_connect(page)
            if tx_hash:
                return GiftResult(
                    success=True,
                    tx_hash=tx_hash,
                    ton_viewer_url=tx_url(tx_hash),
                )

            # Fallback: check if the page already shows a success state
            tx_hash = await self._extract_success_hash(page)
            if tx_hash:
                return GiftResult(
                    success=True,
                    tx_hash=tx_hash,
                    ton_viewer_url=tx_url(tx_hash),
                )

            return GiftResult(success=False, error="Transaction not confirmed")

        except Exception as exc:
            logger.exception(f"Fragment gift failed: {exc}")
            return GiftResult(success=False, error=str(exc))
        finally:
            await page.close()

    async def _handle_ton_connect(self, page: Page) -> Optional[str]:
        """
        Wait for the TON Connect success event or a redirect to a success URL.
        Fragment's success page URL contains the tx hash query param or shows it in the DOM.
        """
        try:
            # Wait up to 90 s for the success indicator
            await page.wait_for_url(
                re.compile(r"fragment\.com.*(success|done|confirmed)"),
                timeout=90_000,
            )
        except PWTimeout:
            pass

        return await self._extract_success_hash(page)

    async def _extract_success_hash(self, page: Page) -> Optional[str]:
        """Scrape the transaction hash from the current page."""
        try:
            content = await page.content()
            # Look for tonviewer link in page source
            if m := _TONVIEWER_RE.search(content):
                return m.group(1)
            # Look for raw 64-char hex hash
            if m := _TX_HASH_RE.search(content):
                return m.group(0)
        except Exception:
            pass
        return None
