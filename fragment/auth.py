"""Fragment browser session management via Playwright persistent context."""

from __future__ import annotations

from pathlib import Path
from loguru import logger

from config import settings

SESSION_DIR: Path = settings.BROWSER_DATA_DIR / "fragment_session"


async def get_browser_context(playwright):
    """
    Returns a persistent Playwright BrowserContext with saved Fragment session.
    On first run the user must log in manually; the session is then reused.
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    proxy_settings = {"server": settings.FRAGMENT_PROXY} if settings.FRAGMENT_PROXY else None

    context = await playwright.chromium.launch_persistent_context(
        str(SESSION_DIR),
        channel="msedge",
        headless=False,
        proxy=proxy_settings,
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        args=["--disable-blink-features=AutomationControlled"],
        ignore_https_errors=True,
    )
    return context


async def is_logged_in(page) -> bool:
    """Check whether the Fragment session is still authenticated."""
    try:
        await page.goto("https://fragment.com/", timeout=15_000)
        await page.wait_for_timeout(2000)
        # Logged-in Fragment shows a wallet address or username in top bar
        content = await page.content()
        return "log-in" not in content.lower() or "wallet" in content.lower()
    except Exception as exc:
        logger.warning(f"Session check failed: {exc}")
        return False
