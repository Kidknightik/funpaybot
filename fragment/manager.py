"""High-level Fragment manager that owns the Playwright lifecycle."""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger
from playwright.async_api import async_playwright, BrowserContext

from .auth import get_browser_context, is_logged_in
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

    async def start(self) -> None:
        """Launch Playwright and open the persistent browser context."""
        self._playwright = await async_playwright().start()
        self._context = await get_browser_context(self._playwright)
        self._gifter = FragmentGifter(self._context)
        logger.info("Fragment browser context started")

        # Verify session
        page = await self._context.new_page()
        logged = await is_logged_in(page)
        await page.close()
        if not logged:
            logger.warning(
                "Fragment session not authenticated. "
                "Please log in via the opened browser window."
            )

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Fragment browser context stopped")

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
