"""High-level Fragment manager that owns the Playwright lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
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
        # Set when session check finishes (ready OR timed out)
        self._ready_event: asyncio.Event = asyncio.Event()

    @property
    def ready_event(self) -> asyncio.Event:
        return self._ready_event

    async def start(self) -> None:
        """Launch Playwright, clean stale locks, open the persistent browser context."""
        self._clean_edge_locks()
        self._playwright = await async_playwright().start()
        self._context = await get_browser_context(self._playwright)
        self._gifter = FragmentGifter(self._context)
        logger.info("Fragment browser context started")

        # Schedule full session check — sets _ready_event when done
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

    def _clean_edge_locks(self) -> None:
        """Remove stale Edge profile lock files left by a crashed previous session."""
        from .auth import SESSION_DIR
        lock_paths = [
            SESSION_DIR / "Default" / "LOCK",
            SESSION_DIR / "SingletonLock",
            SESSION_DIR / "SingletonCookie",
            SESSION_DIR / "Default" / "SingletonLock",
        ]
        for p in lock_paths:
            if p.exists():
                try:
                    p.unlink()
                    logger.debug(f"Removed stale lock: {p.name}")
                except Exception as exc:
                    logger.warning(f"Could not remove lock {p}: {exc}")

    async def _check_session(self) -> None:
        """
        Background task: check Fragment auth state.
        Sets _ready_event when done (regardless of outcome).
        """
        try:
            page = await self._context.new_page()
            info = await check_session(page)
            self.session_info = info

            if info.fully_ready:
                logger.info(
                    f"Fragment ready | "
                    f"TG: @{info.tg_username or '?'} | "
                    f"Wallet: {info.wallet_address or '?'} | "
                    f"Balance: {info.ton_balance or '?'} TON"
                )
                await page.close()
                self._ready_event.set()
                return

            if not info.logged_in:
                logger.warning(
                    "Fragment: need to log in via Telegram. "
                    "Open browser and click 'Connect Telegram'."
                )
            elif not info.wallet_connected:
                logger.warning(
                    "Fragment: Telegram connected but TON wallet not linked. "
                    "Click 'Connect TON' in the open browser."
                )

            logger.info("Waiting for Fragment auth (up to 5 min)...")
            await self._wait_for_full_auth(page)

        except Exception as exc:
            logger.warning(f"Fragment session check error (non-fatal): {exc}")
        finally:
            # Always unblock order processing, even if auth failed
            self._ready_event.set()
            logger.debug("Fragment ready_event set")

    async def _wait_for_full_auth(self, page, timeout_sec: int = 300) -> None:
        """Poll every 5 s until fully authenticated or timeout."""
        from .auth import _SELECTOR_CONNECT_TG, _SELECTOR_CONNECT_TON

        for _ in range(timeout_sec // 5):
            await asyncio.sleep(5)
            try:
                has_tg  = await page.locator(_SELECTOR_CONNECT_TG).count()  > 0
                has_ton = await page.locator(_SELECTOR_CONNECT_TON).count() > 0
                if not has_tg and not has_ton:
                    info = await check_session(page)
                    self.session_info = info
                    logger.info(
                        f"Fragment authenticated | "
                        f"TG: @{info.tg_username or '?'} | "
                        f"Wallet: {info.wallet_address or '?'}"
                    )
                    await page.close()
                    return
            except Exception:
                pass

        logger.warning("Fragment auth timeout (5 min). Bot will still process orders but gifting may fail.")
        try:
            await page.close()
        except Exception:
            pass
