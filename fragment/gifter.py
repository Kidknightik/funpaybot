"""
Fragment automation — buy Telegram Stars / Premium for another user.

Stars flow  (https://fragment.com/stars/buy):
  1. Fill "Enter Telegram username…" with recipient
  2. Fill the amount field
  3. Click the first "Buy … Telegram Stars" button (shows confirmation modal)
  4. In the modal — UNCHECK "Show my name to the recipient" (anonymous)
  5. Click "Buy Stars for <username>" final button
  6. Auto-confirm TON payment via TonConnectWallet

Premium flow (https://fragment.com/premium?gift=1):
  Same but months instead of amount.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger
from playwright.async_api import Page, BrowserContext, TimeoutError as PWTimeout

from utils.ton_viewer import tx_url as make_tx_url
from .ton_wallet import TonConnectWallet

_TX_HASH_RE   = re.compile(r'[0-9a-fA-F]{64}')
_TONVIEWER_RE = re.compile(r'tonviewer\.com/transaction/([0-9a-fA-F]+)')


@dataclass
class GiftResult:
    success: bool
    tx_hash: Optional[str] = None
    ton_viewer_url: Optional[str] = None
    error: Optional[str] = None


class FragmentGifter:
    def __init__(self, context: BrowserContext) -> None:
        self._ctx = context
        self._ton = TonConnectWallet()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def gift_stars(self, username: str, amount: int) -> GiftResult:
        username = username.lstrip("@")
        return await self._do_stars(username, amount)

    async def gift_premium(self, username: str, months: int) -> GiftResult:
        username = username.lstrip("@")
        return await self._do_premium(username, months)

    # ── Stars flow ─────────────────────────────────────────────────────────────

    async def _do_stars(self, username: str, amount: int) -> GiftResult:
        page: Page = await self._ctx.new_page()
        label = f"{amount} stars → @{username}"
        try:
            logger.info(f"Fragment: {label}")
            await page.goto(
                "https://fragment.com/stars/buy",
                timeout=30_000,
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(2000)

            # Step 1: fill recipient username
            await self._fill_username(page, username)

            # Step 2: fill star amount
            await self._fill_stars_amount(page, str(amount))

            # Step 3: click "Buy X Telegram Stars" button (opens modal)
            await self._click_initial_buy_button(page)

            # Step 4: in the modal — uncheck "Show my name to the recipient"
            await self._uncheck_show_name(page)

            # Step 5: click "Buy Stars for <username>" final confirmation
            await self._click_confirm_buy(page, username)

            # Step 6: auto-confirm TON payment
            tx_hash = await self._ton.handle_tonconnect_page(page, timeout_ms=90_000)

            # Fallback: try to extract hash from the page itself
            if not tx_hash:
                tx_hash = await self._extract_tx_hash(page)

            if tx_hash:
                logger.info(f"Gift success: {label} | tx={tx_hash}")
                return GiftResult(
                    success=True,
                    tx_hash=tx_hash,
                    ton_viewer_url=make_tx_url(tx_hash),
                )
            return GiftResult(success=False, error="Transaction not confirmed")

        except Exception as exc:
            logger.exception(f"Fragment gift failed [{label}]: {exc}")
            return GiftResult(success=False, error=str(exc))
        finally:
            await page.close()

    # ── Premium flow ────────────────────────────────────────────────────────────

    async def _do_premium(self, username: str, months: int) -> GiftResult:
        page: Page = await self._ctx.new_page()
        label = f"premium {months}mo → @{username}"
        try:
            logger.info(f"Fragment: {label}")
            await page.goto(
                "https://fragment.com/premium?gift=1",
                timeout=30_000,
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(2000)

            await self._fill_username(page, username)
            await self._fill_months(page, str(months))
            await self._click_initial_buy_button(page)
            await self._uncheck_show_name(page)
            await self._click_confirm_buy(page, username)

            tx_hash = await self._ton.handle_tonconnect_page(page, timeout_ms=90_000)
            if not tx_hash:
                tx_hash = await self._extract_tx_hash(page)

            if tx_hash:
                logger.info(f"Gift success: {label} | tx={tx_hash}")
                return GiftResult(
                    success=True,
                    tx_hash=tx_hash,
                    ton_viewer_url=make_tx_url(tx_hash),
                )
            return GiftResult(success=False, error="Transaction not confirmed")

        except Exception as exc:
            logger.exception(f"Fragment premium failed [{label}]: {exc}")
            return GiftResult(success=False, error=str(exc))
        finally:
            await page.close()

    # ── Sub-steps ──────────────────────────────────────────────────────────────

    async def _fill_username(self, page: Page, username: str) -> None:
        """Fill the 'Enter Telegram username...' field."""
        selectors = [
            "input[placeholder*='Enter Telegram username']",
            "input[placeholder*='username']",
            "input[placeholder*='Username']",
            "input[placeholder*='@']",
            ".tm-input-field input[type='text']",
            "input.tm-input",
            "input[type='text']",
        ]
        filled = False
        for sel in selectors:
            try:
                field = page.locator(sel).first
                await field.wait_for(state="visible", timeout=4000)
                await field.click()
                await field.fill(f"@{username}")
                await page.wait_for_timeout(1200)

                # Accept autocomplete suggestion
                for suggest_sel in [
                    ".tm-suggest-item",
                    "[class*='suggest']",
                    "[class*='autocomplete']",
                    f"[data-username='{username}']",
                ]:
                    try:
                        sug = page.locator(suggest_sel).first
                        if await sug.count() > 0:
                            await sug.click()
                            await page.wait_for_timeout(500)
                            break
                    except Exception:
                        pass

                logger.debug(f"Username filled: @{username}")
                filled = True
                break
            except Exception:
                continue

        if not filled:
            logger.warning("Could not fill username — trying to continue anyway")

    async def _fill_stars_amount(self, page: Page, amount_str: str) -> None:
        """Fill the stars quantity input."""
        selectors = [
            "input[placeholder*='Enter amount']",
            "input[placeholder*='amount']",
            "input[name='amount']",
            "input[name='count']",
            "input[name='stars']",
            "input[type='number']",
            ".tm-input-field input",
        ]
        for sel in selectors:
            try:
                field = page.locator(sel).first
                if await field.count() == 0:
                    continue
                await field.wait_for(state="visible", timeout=3000)
                await field.triple_click()
                await field.fill(amount_str)
                await page.wait_for_timeout(400)
                logger.debug(f"Stars amount set: {amount_str}")
                return
            except Exception:
                continue
        logger.warning("Could not set stars amount — trying to continue")

    async def _fill_months(self, page: Page, months_str: str) -> None:
        """Fill the Premium months input."""
        selectors = [
            "input[name='months']",
            "input[name='amount']",
            "input[placeholder*='month']",
            "input[type='number']",
        ]
        for sel in selectors:
            try:
                field = page.locator(sel).first
                if await field.count() == 0:
                    continue
                await field.wait_for(state="visible", timeout=3000)
                await field.triple_click()
                await field.fill(months_str)
                await page.wait_for_timeout(400)
                return
            except Exception:
                continue

    async def _click_initial_buy_button(self, page: Page) -> None:
        """
        Click the first 'Buy X Telegram Stars' button that opens the confirmation modal.
        This is NOT the final 'Buy Stars for <user>' button — that comes next.
        """
        selectors = [
            # Exact text variants Fragment uses
            "button:has-text('Buy Telegram Stars')",
            "button:has-text('Buy Stars')",
            "button:has-text('Buy Premium')",
            "button:has-text('Купить')",
            "button.btn-primary",
            ".tm-section-buy button",
            "form button[type='submit']",
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=5000)
                await btn.click()
                logger.debug(f"Initial buy button clicked: {sel}")
                await page.wait_for_timeout(1500)
                return
            except Exception:
                continue
        raise RuntimeError("Initial buy button not found on Fragment page")

    async def _uncheck_show_name(self, page: Page) -> None:
        """
        In the confirmation modal, UNCHECK 'Show my name to the recipient'.
        It is CHECKED by default — we must uncheck it for anonymous gifting.
        """
        # The checkbox / toggle that controls showing the sender's name
        selectors = [
            "input[name='anonymous']",
            "input[name='show_name']",
            "input[name='show_sender']",
            # Look for a checked checkbox near text 'Show my name'
            "label:has-text('Show my name') input[type='checkbox']",
            "label:has-text('Show my name') input",
            "[class*='show-name'] input",
            "[class*='showName'] input",
        ]
        for sel in selectors:
            try:
                cb = page.locator(sel).first
                if await cb.count() == 0:
                    continue
                await cb.wait_for(state="visible", timeout=3000)
                if await cb.is_checked():
                    await cb.uncheck()
                    await page.wait_for_timeout(300)
                    logger.info("Anonymous: unchecked 'Show my name' ✓")
                else:
                    logger.info("Anonymous: 'Show my name' already unchecked ✓")
                return
            except Exception:
                continue

        # Fallback: click any label/toggle mentioning "Show"
        for sel in [
            "label:has-text('Show my name')",
            "label:has-text('Show name')",
            "[class*='show-name']",
            "[class*='showName']",
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    await page.wait_for_timeout(300)
                    logger.info(f"Clicked show-name toggle via fallback: {sel}")
                    return
            except Exception:
                continue

        logger.warning("'Show my name' checkbox not found — gift may NOT be anonymous")

    async def _click_confirm_buy(self, page: Page, username: str) -> None:
        """Click the final 'Buy Stars for <username>' confirmation button in the modal."""
        selectors = [
            f"button:has-text('Buy Stars for {username}')",
            f"button:has-text('Buy Stars for @{username}')",
            "button:has-text('Buy Stars for')",
            "button:has-text('Buy Premium for')",
            # Fallback: any primary button in the modal
            ".modal button.btn-primary",
            "[class*='dialog'] button.btn-primary",
            "[class*='modal'] button:has-text('Buy')",
            "button.btn-primary:has-text('Buy')",
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=8000)
                await btn.click()
                logger.debug(f"Confirm buy button clicked: {sel}")
                await page.wait_for_timeout(1500)
                return
            except Exception:
                continue
        raise RuntimeError("Confirm buy button not found in Fragment modal")

    async def _extract_tx_hash(self, page: Page) -> Optional[str]:
        """Scrape the TON transaction hash from the page (fallback)."""
        try:
            # Wait a moment for success page to load
            await page.wait_for_timeout(3000)
            content = await page.content()
            if m := _TONVIEWER_RE.search(content):
                return m.group(1)
            if m := _TX_HASH_RE.search(content):
                return m.group(0)
        except Exception:
            pass
        return None
