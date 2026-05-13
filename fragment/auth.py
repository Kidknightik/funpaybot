"""Fragment browser session management via Playwright persistent context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.async_api import Page, TimeoutError as PWTimeout

from config import settings

SESSION_DIR: Path = settings.BROWSER_DATA_DIR / "fragment_session"


@dataclass
class FragmentSessionInfo:
    logged_in: bool
    wallet_connected: bool
    wallet_address: Optional[str] = None
    tg_username: Optional[str] = None
    ton_balance: Optional[str] = None

    def __str__(self) -> str:
        if not self.logged_in:
            return "❌ Не авторизован на Fragment"
        lines = ["✅ Fragment авторизован"]
        if self.tg_username:
            lines.append(f"👤 Telegram: @{self.tg_username}")
        if self.wallet_connected and self.wallet_address:
            short = self.wallet_address[:6] + "…" + self.wallet_address[-4:]
            lines.append(f"💎 Кошелёк: {short}")
            if self.ton_balance:
                lines.append(f"💰 Баланс: {self.ton_balance} TON")
        else:
            lines.append("⚠️  TON кошелёк не подключён")
        return "\n".join(lines)


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


async def check_session(page: Page) -> FragmentSessionInfo:
    """
    Full Fragment session check:
    - Is user logged in?
    - Is TON wallet connected?
    - What's the wallet address and balance?
    """
    try:
        await page.goto("https://fragment.com/", timeout=20_000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)
    except Exception as exc:
        logger.warning(f"Fragment page load failed: {exc}")
        return FragmentSessionInfo(logged_in=False, wallet_connected=False)

    try:
        content = await page.content()

        # ── Check login state ─────────────────────────────────────────────────
        # Fragment shows "Sign In" / "Log in" button when not authenticated
        sign_in_btn = page.locator("a.tm-section-header-login, button:has-text('Log in'), a:has-text('Sign In')")
        has_sign_in = await sign_in_btn.count() > 0

        if has_sign_in:
            return FragmentSessionInfo(logged_in=False, wallet_connected=False)

        # ── Extract Telegram username ──────────────────────────────────────────
        tg_username: Optional[str] = None
        try:
            # Fragment shows "@username" in the top user menu
            user_el = page.locator(".tm-header-username, .header-username, [class*='username']").first
            raw = (await user_el.inner_text(timeout=2000)).strip().lstrip("@")
            if raw:
                tg_username = raw
        except Exception:
            pass

        # ── Check wallet connection ───────────────────────────────────────────
        wallet_address: Optional[str] = None
        ton_balance: Optional[str] = None
        wallet_connected = False

        try:
            # Fragment displays shortened wallet address (e.g. "UQ…abc") in header
            wallet_el = page.locator(
                "button[class*='wallet'], .tm-wallet-address, "
                "[class*='wallet-address'], [class*='walletAddress']"
            ).first
            addr_text = (await wallet_el.inner_text(timeout=3000)).strip()
            if addr_text and len(addr_text) > 4:
                wallet_address = addr_text
                wallet_connected = True
        except Exception:
            pass

        # Fallback: check page source for TON address pattern
        if not wallet_connected:
            import re
            ton_addr_re = re.compile(r'\b(UQ|EQ)[A-Za-z0-9_\-]{46}\b')
            matches = ton_addr_re.findall(content)
            if matches:
                wallet_address = matches[0]
                wallet_connected = True

        # ── Extract balance ───────────────────────────────────────────────────
        if wallet_connected:
            try:
                balance_el = page.locator(
                    "[class*='balance'], [class*='ton-amount'], [class*='tonAmount']"
                ).first
                bal = (await balance_el.inner_text(timeout=2000)).strip()
                if bal:
                    ton_balance = bal
            except Exception:
                pass

        return FragmentSessionInfo(
            logged_in=True,
            wallet_connected=wallet_connected,
            wallet_address=wallet_address,
            tg_username=tg_username,
            ton_balance=ton_balance,
        )

    except Exception as exc:
        logger.warning(f"Fragment session parse error: {exc}")
        return FragmentSessionInfo(logged_in=True, wallet_connected=False)
