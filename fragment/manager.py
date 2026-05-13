"""High-level Fragment manager that owns the Playwright lifecycle."""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger
from playwright.async_api import async_playwright, BrowserContext

from .auth import get_browser_context, check_session, FragmentSessionInfo
from .client import FragmentSearchClient, FragmentUser
from .gifter import FragmentGifter, GiftResult


class FragmentManager:
    """
    Singleton-like manager that keeps a persistent Playwright context open
    for the lifetime of the bot process.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._search = FragmentSearchClient()
        self._gifter: Optional[FragmentGifter] = None
        self._lock = asyncio.Lock()
        self.session_info: Optional[FragmentSessionInfo] = None

    async def start(self) -> None:
        """Launch Playwright and open the persistent browser context."""
        self._playwright = await async_playwright().start()
        self._context = await get_browser_context(self._playwright)
        self._gifter = FragmentGifter(self._context)
        logger.info("Fragment browser context started")

        # Schedule full session check in background — don't block startup
        asyncio.create_task(self._check_session())

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Fragment browser context stopped")

    async def refresh_session(self) -> FragmentSessionInfo:
        """Manually re-run the session check and return fresh info."""
        page = await self._context.new_page()
        try:
            info = await check_session(page)
            self.session_info = info
            return info
        finally:
            await page.close()

    # ── Public API ────────────────────────────────────────────────────────────

    async def search_user(self, username: str) -> Optional[FragmentUser]:
        return await self._search.search_username(username)

    async def gift_stars(self, username: str, amount: int) -> GiftResult:
        async with self._lock:
            assert self._gifter, "FragmentManager not started"
            return await self._gifter.gift_stars(username, amount)

    async def gift_premium(self, username: str, months: int) -> GiftResult:
        async with self._lock:
            assert self._gifter, "FragmentManager not started"
            return await self._gifter.gift_premium(username, months)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _check_session(self) -> None:
        """
        Background task: open Fragment, check auth state, guide admin to log in.
        The page stays open so the admin can complete the login manually.
        After login/wallet connect, session is auto-saved by the persistent context.
        """
        try:
            page = await self._context.new_page()
            info = await check_session(page)
            self.session_info = info

            if info.fully_ready:
                logger.info(
                    f"Fragment ready ✓ | "
                    f"TG: @{info.tg_username or '?'} | "
                    f"Wallet: {info.wallet_address or '?'} | "
                    f"Balance: {info.ton_balance or '?'} TON"
                )
                await page.close()
                return

            # Not fully authenticated — leave browser window open for manual login
            if not info.logged_in:
                logger.warning(
                    "Fragment: нужно войти через Telegram. "
                    "В открытом браузере нажмите 'Connect Telegram'."
                )
            elif not info.wallet_connected:
                logger.warning(
                    "Fragment: Telegram подключён, но TON кошелёк не привязан. "
                    "В открытом браузере нажмите 'Connect TON'."
                )

            # Wait up to 5 minutes for the user to complete auth in the open window
            logger.info("Ожидаю завершения авторизации в браузере (до 5 мин)...")
            await self._wait_for_full_auth(page)

        except Exception as exc:
            logger.warning(f"Fragment session check error (non-fatal): {exc}")

    async def _wait_for_full_auth(self, page, timeout_sec: int = 300) -> None:
        """Poll Fragment every 5 seconds until fully authenticated or timeout."""
        from .auth import _SELECTOR_CONNECT_TG, _SELECTOR_CONNECT_TON
        import asyncio

        for _ in range(timeout_sec // 5):
            await asyncio.sleep(5)
            try:
                has_tg  = await page.locator(_SELECTOR_CONNECT_TG).count()  > 0
                has_ton = await page.locator(_SELECTOR_CONNECT_TON).count() > 0
                if not has_tg and not has_ton:
                    # Fully connected — re-run full check to get details
                    info = await check_session(page)
                    self.session_info = info
                    logger.info(
                        f"Fragment авторизован ✓ | "
                        f"TG: @{info.tg_username or '?'} | "
                        f"Wallet: {info.wallet_address or '?'}"
                    )
                    await page.close()
                    return
            except Exception:
                pass

        logger.warning("Таймаут ожидания авторизации Fragment (5 мин). Перезапустите бота.")
