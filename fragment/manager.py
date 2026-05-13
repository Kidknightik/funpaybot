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
        """Background task: navigate to Fragment, check login + wallet status."""
        try:
            page = await self._context.new_page()
            info = await check_session(page)
            self.session_info = info

            if info.logged_in and info.wallet_connected:
                logger.info(str(info))
                await page.close()
            elif info.logged_in and not info.wallet_connected:
                logger.warning(
                    "Fragment: залогинен, но TON кошелёк не подключён. "
                    "Подключите кошелёк в открытом браузере."
                )
                # Leave page open so admin can connect wallet
            else:
                logger.warning(
                    "Fragment: не авторизован. "
                    "Войдите через Telegram в открытом браузере."
                )
                # Leave page open for login
        except Exception as exc:
            logger.warning(f"Fragment session check error (non-fatal): {exc}")
