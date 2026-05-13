"""Fragment browser session management via Playwright persistent context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.async_api import Page, TimeoutError as PWTimeout

from config import settings

SESSION_DIR: Path = settings.BROWSER_DATA_DIR / "fragment_session"

# Fragment's actual button selectors (from DOM inspection)
_SELECTOR_CONNECT_TG  = "button.login-link"          # "Connect Telegram"
_SELECTOR_CONNECT_TON = "button.ton-auth-link"        # "Connect TON"


@dataclass
class FragmentSessionInfo:
    logged_in: bool           # Telegram account connected
    wallet_connected: bool    # TON wallet connected
    wallet_address: Optional[str] = None
    tg_username: Optional[str] = None
    ton_balance: Optional[str] = None

    @property
    def fully_ready(self) -> bool:
        return self.logged_in and self.wallet_connected

    def __str__(self) -> str:
        lines = []
        # Telegram
        if self.logged_in:
            tg = f"@{self.tg_username}" if self.tg_username else "подключён"
            lines.append(f"✅ Telegram: {tg}")
        else:
            lines.append("❌ Telegram: не подключён")

        # TON wallet
        if self.wallet_connected and self.wallet_address:
            short = self.wallet_address[:8] + "…" + self.wallet_address[-4:]
            lines.append(f"✅ TON кошелёк: <code>{short}</code>")
            if self.ton_balance:
                lines.append(f"💰 Баланс: <b>{self.ton_balance} TON</b>")
        elif self.logged_in:
            lines.append("⚠️  TON кошелёк: не подключён")
        else:
            lines.append("⚠️  TON кошелёк: не подключён")

        return "\n".join(lines)


async def get_browser_context(playwright):
    """
    Returns a persistent Playwright BrowserContext with saved Fragment session.
    On first run the user must log in manually in the opened window.
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    proxy_settings = {"server": settings.FRAGMENT_PROXY} if settings.FRAGMENT_PROXY else None

    context = await playwright.chromium.launch_persistent_context(
        str(SESSION_DIR),
        channel="msedge",
        headless=False,    # keep visible so admin can log in on first run
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
    Full Fragment session check using correct DOM selectors.

    Fragment state machine:
      - Both 'Connect Telegram' + 'Connect TON' visible  → not logged in at all
      - Only 'Connect TON' visible                        → TG connected, no wallet
      - Neither button visible                            → fully connected
    """
    try:
        await page.goto(
            "https://fragment.com/",
            timeout=25_000,
            wait_until="domcontentloaded",
        )
        # Wait for JS to render auth state
        await page.wait_for_timeout(3000)
    except Exception as exc:
        logger.warning(f"Fragment page load failed: {exc}")
        return FragmentSessionInfo(logged_in=False, wallet_connected=False)

    try:
        has_connect_tg  = await page.locator(_SELECTOR_CONNECT_TG).count()  > 0
        has_connect_ton = await page.locator(_SELECTOR_CONNECT_TON).count() > 0

        # ── Not logged in at all ──────────────────────────────────────────────
        if has_connect_tg:
            return FragmentSessionInfo(logged_in=False, wallet_connected=False)

        # ── Telegram connected, checking TON wallet ───────────────────────────
        tg_username = await _get_tg_username(page)

        if has_connect_ton:
            return FragmentSessionInfo(
                logged_in=True,
                wallet_connected=False,
                tg_username=tg_username,
            )

        # ── Fully connected — extract wallet info ─────────────────────────────
        wallet_address = await _get_wallet_address(page)
        ton_balance    = await _get_ton_balance(page)

        return FragmentSessionInfo(
            logged_in=True,
            wallet_connected=True,
            tg_username=tg_username,
            wallet_address=wallet_address,
            ton_balance=ton_balance,
        )

    except Exception as exc:
        logger.warning(f"Fragment session parse error: {exc}")
        return FragmentSessionInfo(logged_in=False, wallet_connected=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_tg_username(page: Page) -> Optional[str]:
    """Extract Telegram username from the Fragment header (shown when TG connected)."""
    # Fragment renders the TG username inside the header user-block
    selectors = [
        ".tm-header-userpic + * .tm-header-username",
        "[class*='header-user'] [class*='username']",
        ".tm-header-user .tm-username",
        # generic: any short @username-like text in the header actions
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            text = (await el.inner_text(timeout=1500)).strip().lstrip("@")
            if text:
                return text
        except Exception:
            continue

    # Fallback: check the page source for "@username" near the header area
    try:
        header_html = await page.locator("header").inner_html()
        import re
        m = re.search(r'@([A-Za-z0-9_]{5,32})', header_html)
        if m:
            return m.group(1)
    except Exception:
        pass

    return None


async def _get_wallet_address(page: Page) -> Optional[str]:
    """Extract the connected TON wallet address from the Fragment header."""
    import re
    _TON_ADDR = re.compile(r'\b(UQ|EQ)[A-Za-z0-9_\-]{46}\b')

    selectors = [
        ".tm-header-wallet-address",
        ".tm-wallet",
        "[class*='wallet-addr']",
        "[class*='walletAddr']",
        ".tm-header-actions [class*='address']",
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            text = (await el.inner_text(timeout=1500)).strip()
            if text:
                return text
        except Exception:
            continue

    # Fallback: grep page source for TON address in header
    try:
        header_html = await page.locator("header").inner_html()
        m = _TON_ADDR.search(header_html)
        if m:
            return m.group(0)
    except Exception:
        pass

    return None


async def _get_ton_balance(page: Page) -> Optional[str]:
    """Extract TON balance shown in the header after wallet connection."""
    selectors = [
        ".tm-header-balance",
        "[class*='header-ton-balance']",
        "[class*='tonBalance']",
        ".tm-header-wallet .tm-value",
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            text = (await el.inner_text(timeout=1500)).strip()
            if text:
                return text
        except Exception:
            continue
    return None
