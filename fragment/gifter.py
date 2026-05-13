"""
Fragment automation — gift Telegram Stars and Premium anonymously.

Flow:
1. Navigate to fragment.com/stars (or /premium)
2. Search for the recipient username
3. Click Gift / Buy
4. Enable "Hide my name" checkbox (anonymous gift)
5. Confirm via TON Connect
6. Extract transaction hash from success page
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger
from playwright.async_api import Page, BrowserContext, TimeoutError as PWTimeout

from utils.ton_viewer import tx_url as make_tx_url

_TX_HASH_RE    = re.compile(r'[0-9a-fA-F]{64}')
_TONVIEWER_RE  = re.compile(r'tonviewer\.com/transaction/([0-9a-fA-F]+)')


@dataclass
class GiftResult:
    success: bool
    tx_hash: Optional[str] = None
    ton_viewer_url: Optional[str] = None
    error: Optional[str] = None


class FragmentGifter:
    def __init__(self, context: BrowserContext) -> None:
        self._ctx = context

    # ── Public API ────────────────────────────────────────────────────────────

    async def gift_stars(self, username: str, amount: int) -> GiftResult:
        username = username.lstrip("@")
        url = f"https://fragment.com/stars?gift=1"
        return await self._do_gift(url, username, str(amount), label=f"{amount} stars → @{username}")

    async def gift_premium(self, username: str, months: int) -> GiftResult:
        username = username.lstrip("@")
        url = f"https://fragment.com/premium?gift=1"
        return await self._do_gift(url, username, str(months), label=f"premium {months}mo → @{username}")

    # ── Core gifting flow ────────────────────────────────────────────────────

    async def _do_gift(self, url: str, username: str, amount_str: str, label: str) -> GiftResult:
        page: Page = await self._ctx.new_page()
        try:
            logger.info(f"Fragment gift: {label}")
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # ── Step 1: fill recipient username ──────────────────────────────
            await self._fill_recipient(page, username)

            # ── Step 2: set quantity if needed ───────────────────────────────
            await self._set_amount(page, amount_str)

            # ── Step 3: enable anonymous gift checkbox ────────────────────────
            await self._enable_anonymous(page)

            # ── Step 4: click the primary Buy/Gift button ─────────────────────
            await self._click_buy(page)

            # ── Step 5: wait for TON Connect + transaction success ────────────
            tx_hash = await self._wait_for_success(page)

            if tx_hash:
                logger.info(f"Gift success: {label} | tx={tx_hash}")
                return GiftResult(
                    success=True,
                    tx_hash=tx_hash,
                    ton_viewer_url=make_tx_url(tx_hash),
                )
            return GiftResult(success=False, error="Transaction not confirmed on Fragment")

        except Exception as exc:
            logger.exception(f"Fragment gift failed [{label}]: {exc}")
            return GiftResult(success=False, error=str(exc))
        finally:
            await page.close()

    # ── Sub-steps ─────────────────────────────────────────────────────────────

    async def _fill_recipient(self, page: Page, username: str) -> None:
        """Type the recipient username in the Fragment gift form."""
        selectors = [
            "input[name='recipient']",
            "input[placeholder*='username']",
            "input[placeholder*='Username']",
            "input[placeholder*='@']",
            ".tm-gift-form input[type='text']",
            "input.tm-input",
        ]
        for sel in selectors:
            try:
                field = page.locator(sel).first
                await field.wait_for(state="visible", timeout=4000)
                await field.click()
                await field.fill(f"@{username}")
                await page.wait_for_timeout(1000)
                # Confirm autocomplete suggestion if shown
                suggestion = page.locator(
                    f".tm-suggest-item, [class*='suggest'], [class*='autocomplete']"
                ).first
                if await suggestion.count() > 0:
                    await suggestion.click()
                    await page.wait_for_timeout(500)
                logger.debug(f"Recipient filled: @{username}")
                return
            except Exception:
                continue
        logger.warning("Could not find recipient input field — trying to continue anyway")

    async def _set_amount(self, page: Page, amount_str: str) -> None:
        """Set the stars/months quantity in the form."""
        selectors = [
            "input[name='amount']",
            "input[name='quantity']",
            "input[name='count']",
            "input[name='stars']",
            ".tm-gift-amount input",
            "input[type='number']",
        ]
        for sel in selectors:
            try:
                field = page.locator(sel).first
                if await field.count() == 0:
                    continue
                await field.wait_for(state="visible", timeout=2000)
                current = await field.input_value()
                if current == amount_str:
                    return
                await field.triple_click()
                await field.fill(amount_str)
                await page.wait_for_timeout(500)
                logger.debug(f"Amount set to {amount_str}")
                return
            except Exception:
                continue

    async def _enable_anonymous(self, page: Page) -> None:
        """
        Find and check the 'Hide my name' / anonymous gift checkbox.
        Fragment shows this on the gift form as a toggle/checkbox.
        """
        selectors = [
            "input[name='anonymous']",
            "input[name='hide_sender']",
            "[class*='anonymous'] input",
            "[class*='hide-name'] input",
            "label:has-text('Hide my name') input",
            "label:has-text('Скрыть моё имя') input",
            "label:has-text('Анонимно') input",
            ".tm-checkbox:has-text('anonymous')",
            ".tm-checkbox:has-text('hide')",
        ]
        for sel in selectors:
            try:
                checkbox = page.locator(sel).first
                if await checkbox.count() == 0:
                    continue
                is_checked = await checkbox.is_checked()
                if not is_checked:
                    await checkbox.check()
                    await page.wait_for_timeout(300)
                    logger.info("Anonymous gift: enabled ✓")
                else:
                    logger.info("Anonymous gift: already enabled ✓")
                return
            except Exception:
                continue

        # Fallback: click any label/toggle that mentions anonymity
        toggle_selectors = [
            "label:has-text('Hide')",
            "label:has-text('Скрыть')",
            "label:has-text('Анонимн')",
            ".tm-gift-anonymous",
            "[class*='anonymous']",
        ]
        for sel in toggle_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    await page.wait_for_timeout(300)
                    logger.info(f"Anonymous toggle clicked via fallback: {sel}")
                    return
            except Exception:
                continue

        logger.warning("Anonymous checkbox not found — gift will NOT be anonymous")

    async def _click_buy(self, page: Page) -> None:
        """Click the main Buy/Gift/Send button."""
        selectors = [
            "button.btn-primary:has-text('Buy')",
            "button.btn-primary:has-text('Gift')",
            "button.btn-primary:has-text('Send')",
            "button.btn-primary:has-text('Купить')",
            "button.btn-primary:has-text('Отправить')",
            "button.btn-primary",
            ".tm-gift-form button[type='submit']",
            "button:has-text('Buy Stars')",
            "button:has-text('Gift Stars')",
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=5000)
                await btn.click()
                logger.debug(f"Buy button clicked: {sel}")
                await page.wait_for_timeout(1500)
                return
            except Exception:
                continue
        raise RuntimeError("Buy button not found on Fragment gift page")

    async def _wait_for_success(self, page: Page, timeout_ms: int = 120_000) -> Optional[str]:
        """
        Wait for TON Connect to complete the transaction.
        Returns the transaction hash if found.
        """
        # Fragment redirects to a success URL after payment
        try:
            await page.wait_for_url(
                re.compile(r'fragment\.com.*(success|done|complete|gift)'),
                timeout=timeout_ms,
            )
        except PWTimeout:
            pass

        # Try to extract hash from current page
        tx = await self._extract_tx_hash(page)
        if tx:
            return tx

        # Also check if a success message appeared without URL change
        try:
            success_el = page.locator(
                "[class*='success'], [class*='Success'], "
                "div:has-text('successfully'), div:has-text('sent')"
            ).first
            await success_el.wait_for(state="visible", timeout=10_000)
            tx = await self._extract_tx_hash(page)
        except Exception:
            pass

        return tx

    async def _extract_tx_hash(self, page: Page) -> Optional[str]:
        """Scrape the TON transaction hash from the page."""
        try:
            content = await page.content()
            if m := _TONVIEWER_RE.search(content):
                return m.group(1)
            if m := _TX_HASH_RE.search(content):
                return m.group(0)
        except Exception:
            pass
        return None
