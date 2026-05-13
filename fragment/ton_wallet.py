"""
TON Connect 2.0 wallet client for Fragment auto-confirmation.

Protocol:
1. Fragment generates a session (DApp keypair) and shows a Tonkeeper link.
2. We parse the link to get the DApp's X25519 public key and connect request.
3. We generate our own X25519 session keypair.
4. We connect to the TON Connect bridge (SSE) and send our wallet address.
5. Fragment sends a send_transaction event via the bridge.
6. We sign the transaction with our ed25519 wallet key and submit to TON.
7. We send a success reply via the bridge.
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
from typing import Optional
from urllib.parse import parse_qs, urlparse

import aiohttp
from loguru import logger

try:
    from nacl.public import Box, PrivateKey, PublicKey
    _NACL_AVAILABLE = True
except ImportError:
    _NACL_AVAILABLE = False
    logger.warning("PyNaCl not installed — TON auto-confirm disabled. Run: pip install PyNaCl")

try:
    from tonsdk.crypto import mnemonic_to_wallet_key
    from tonsdk.contract.wallet import WalletV4ContractR2
    from tonsdk.utils import to_nano
    _TONSDK_AVAILABLE = True
except ImportError:
    _TONSDK_AVAILABLE = False
    logger.warning("tonsdk not available — TON auto-confirm disabled")

from config import settings

BRIDGE_URL = "https://bridge.tonapi.io/bridge"
TONAPI_URL = "https://tonapi.io/v2"


class TonConnectWallet:
    """
    Acts as a TON Connect 2.0 wallet.
    Call handle_tonconnect_page(page) after Fragment shows the payment dialog.
    """

    def __init__(self) -> None:
        if not (_NACL_AVAILABLE and _TONSDK_AVAILABLE):
            self._ready = False
            return
        self._ready = True

        mnemonic_words = settings.TON_WALLET_MNEMONIC.split()
        self._pub_key, self._priv_key = mnemonic_to_wallet_key(mnemonic_words)
        self._wallet = WalletV4ContractR2(
            public_key=self._pub_key, private_key=self._priv_key
        )
        self._wallet_address = self._wallet.address.to_string(True, True, True)
        logger.info(f"TON wallet: {self._wallet_address}")

    # ── Public API ─────────────────────────────────────────────────────────────

    async def handle_tonconnect_page(self, page, timeout_ms: int = 90_000) -> Optional[str]:
        """
        Wait for Fragment to show the Tonkeeper link, then auto-sign the transaction.
        Returns the transaction hash on success, None on failure.
        """
        if not self._ready:
            logger.warning("TonConnectWallet not ready — skipping auto-confirm")
            return None

        tc_url = await self._find_tonconnect_url(page, timeout_ms)
        if not tc_url:
            logger.warning("Tonkeeper link not found on Fragment page")
            return None

        logger.info(f"Found TON Connect URL: {tc_url[:80]}...")
        return await self._connect_and_sign(tc_url)

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _find_tonconnect_url(self, page, timeout_ms: int) -> Optional[str]:
        """Scan the page DOM for a Tonkeeper universal link."""
        selectors = [
            'a[href*="tonkeeper.com/ton-connect"]',
            'a[href*="ton-connect"]',
            '[data-tc-url]',
            'a[href^="tc://"]',
        ]
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        while asyncio.get_event_loop().time() < deadline:
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        href = await el.get_attribute("href") or await el.get_attribute("data-tc-url")
                        if href:
                            return href
                except Exception:
                    pass

            # Also try to find it in page source
            try:
                content = await page.content()
                import re
                m = re.search(r'https://app\.tonkeeper\.com/ton-connect\?[^\s"\'<>]+', content)
                if m:
                    return m.group(0)
            except Exception:
                pass

            await asyncio.sleep(1)
        return None

    def _parse_tc_url(self, url: str) -> tuple[Optional[bytes], dict]:
        """Parse Tonkeeper universal link. Returns (dapp_pub_key_bytes, connect_request)."""
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)

            dapp_id_hex = params.get("id", [None])[0]
            r_param = params.get("r", [None])[0]

            dapp_pub = bytes.fromhex(dapp_id_hex) if dapp_id_hex else None

            connect_request = {}
            if r_param:
                # Add padding and decode base64url
                padded = r_param + "==" * ((4 - len(r_param) % 4) % 4)
                connect_request = json.loads(base64.urlsafe_b64decode(padded))

            return dapp_pub, connect_request
        except Exception as exc:
            logger.error(f"Failed to parse TC URL: {exc}")
            return None, {}

    def _encrypt(self, message: bytes, session_key: "PrivateKey", their_pub: bytes) -> str:
        box = Box(session_key, PublicKey(their_pub))
        encrypted = box.encrypt(message)
        return base64.b64encode(bytes(encrypted)).decode()

    def _decrypt(self, payload_b64: str, session_key: "PrivateKey", their_pub: bytes) -> bytes:
        box = Box(session_key, PublicKey(their_pub))
        return box.decrypt(base64.b64decode(payload_b64))

    async def _connect_and_sign(self, tc_url: str) -> Optional[str]:
        """Full TC2.0 flow: connect → receive transaction → sign → submit."""
        dapp_pub, connect_request = self._parse_tc_url(tc_url)
        if not dapp_pub:
            return None

        # Generate per-session keypair for bridge encryption
        session_key: PrivateKey = PrivateKey.generate()
        wallet_id_hex = bytes(session_key.public_key).hex()
        dapp_id_hex = dapp_pub.hex()

        # Build wallet state init (base64 BOC)
        try:
            state_init = self._wallet.create_state_init()["state_init"]
            state_init_b64 = base64.b64encode(state_init.to_boc(False)).decode()
        except Exception:
            state_init_b64 = ""

        connect_reply = {
            "event": "connect",
            "id": 1,
            "payload": {
                "items": [
                    {
                        "name": "ton_addr",
                        "address": self._wallet_address,
                        "network": "-239",
                        "walletStateInit": state_init_b64,
                        "publicKey": self._pub_key.hex(),
                    }
                ],
                "device": {
                    "platform": "browser",
                    "appName": "Tonkeeper",
                    "appVersion": "3.0.0",
                    "maxProtocolVersion": 2,
                    "features": [{"name": "SendTransaction", "maxMessages": 4}],
                },
            },
        }

        encrypted_reply = self._encrypt(
            json.dumps(connect_reply).encode(), session_key, dapp_pub
        )

        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            # Send connect reply to bridge
            try:
                await http.post(
                    f"{BRIDGE_URL}/message",
                    params={
                        "client_id": wallet_id_hex,
                        "to": dapp_id_hex,
                        "ttl": "300",
                    },
                    data=encrypted_reply,
                    headers={"Content-Type": "text/plain"},
                )
                logger.debug("TC connect reply sent to bridge")
            except Exception as exc:
                logger.error(f"Bridge send error: {exc}")
                return None

            # Listen for transaction request (SSE)
            tx_params = None
            tx_request_id = 2
            try:
                async with http.get(
                    f"{BRIDGE_URL}/events",
                    params={"client_id": wallet_id_hex},
                ) as resp:
                    async for raw_line in resp.content:
                        line = raw_line.decode().strip()
                        if not line.startswith("data:"):
                            continue
                        try:
                            event_data = json.loads(line[5:])
                        except json.JSONDecodeError:
                            continue

                        if event_data.get("from") != dapp_id_hex:
                            continue

                        try:
                            decrypted = self._decrypt(
                                event_data["message"], session_key, dapp_pub
                            )
                            event = json.loads(decrypted)
                        except Exception as exc:
                            logger.warning(f"TC message decrypt error: {exc}")
                            continue

                        if event.get("event") == "send_transaction":
                            tx_params = event.get("params", [{}])[0]
                            tx_request_id = event.get("id", 2)
                            logger.info("Received send_transaction from Fragment")
                            break

            except asyncio.TimeoutError:
                logger.warning("TC bridge timeout waiting for transaction request")
            except Exception as exc:
                logger.error(f"TC bridge listen error: {exc}")

            if not tx_params:
                return None

            # Sign and submit the transaction
            tx_hash = await self._sign_and_submit(tx_params)

            # Send success reply to bridge
            if tx_hash:
                reply = {
                    "event": "sign_and_send_transaction",
                    "id": tx_request_id,
                    "payload": {"result": tx_hash},
                }
            else:
                reply = {
                    "event": "send_transaction",
                    "id": tx_request_id,
                    "payload": {"error": {"code": 300, "message": "tx failed"}},
                }

            encrypted_reply2 = self._encrypt(
                json.dumps(reply).encode(), session_key, dapp_pub
            )
            try:
                await http.post(
                    f"{BRIDGE_URL}/message",
                    params={
                        "client_id": wallet_id_hex,
                        "to": dapp_id_hex,
                        "ttl": "300",
                    },
                    data=encrypted_reply2,
                    headers={"Content-Type": "text/plain"},
                )
            except Exception:
                pass

        return tx_hash

    async def _sign_and_submit(self, tx_params: dict) -> Optional[str]:
        """Build, sign, and submit the TON transaction from the TC send_transaction payload."""
        messages = tx_params.get("messages", [])
        if not messages:
            logger.error("TC send_transaction has no messages")
            return None

        # Get current seqno
        seqno = await self._get_seqno()
        if seqno is None:
            return None

        # Build transfer
        msg = messages[0]
        to_addr = msg["address"]
        amount = int(msg.get("amount", 0))

        # Decode payload cell if present
        payload = None
        if msg.get("payload"):
            try:
                from tonsdk.boc import Cell
                payload = Cell.one_from_boc(base64.b64decode(msg["payload"]))
            except Exception as exc:
                logger.warning(f"Could not decode TC payload: {exc}")

        try:
            transfer = self._wallet.create_transfer_message(
                to_addr=to_addr,
                amount=amount,
                seqno=seqno,
                payload=payload,
                send_mode=3,
            )
            boc_bytes = transfer["message"].to_boc(False)
        except Exception as exc:
            logger.error(f"Transaction build error: {exc}")
            return None

        return await self._broadcast_boc(boc_bytes)

    async def _get_seqno(self) -> Optional[int]:
        """Fetch wallet seqno — try tonapi.io, fall back to toncenter."""
        # tonapi.io v2
        try:
            url = f"{TONAPI_URL}/blockchain/accounts/{self._wallet_address}/methods/seqno"
            async with aiohttp.ClientSession() as s:
                resp = await s.get(url, timeout=aiohttp.ClientTimeout(total=10))
                if resp.status == 200:
                    data = await resp.json()
                    if "decoded" in data and data["decoded"].get("seqno") is not None:
                        return int(data["decoded"]["seqno"])
                    # fallback within tonapi response
                    for item in data.get("stack", []):
                        if isinstance(item, dict) and "num" in item:
                            return int(item["num"], 16)
        except Exception as exc:
            logger.warning(f"tonapi seqno error: {exc}")

        # toncenter fallback
        try:
            url2 = f"https://toncenter.com/api/v2/runGetMethod?address={self._wallet_address}&method=seqno&stack=[]"
            async with aiohttp.ClientSession() as s:
                resp = await s.get(url2, timeout=aiohttp.ClientTimeout(total=10))
                data = await resp.json()
                if data.get("ok"):
                    raw = data["result"]["stack"]
                    if raw:
                        return int(raw[0][1], 16)
                    return 0
        except Exception as exc:
            logger.error(f"toncenter seqno error: {exc}")

        return None

    async def _broadcast_boc(self, boc: bytes) -> Optional[str]:
        """Submit the signed BOC to TON and return tx hash."""
        boc_b64 = base64.b64encode(boc).decode()
        # Try tonapi.io
        try:
            async with aiohttp.ClientSession() as s:
                resp = await s.post(
                    f"{TONAPI_URL}/blockchain/message",
                    json={"boc": boc_b64},
                    timeout=aiohttp.ClientTimeout(total=15),
                )
                data = await resp.json()
                tx_hash = data.get("message_hash") or data.get("hash")
                if tx_hash:
                    logger.info(f"TON tx submitted: {tx_hash}")
                    return tx_hash
        except Exception as exc:
            logger.error(f"Broadcast error (tonapi): {exc}")

        # Fallback: toncenter
        try:
            async with aiohttp.ClientSession() as s:
                resp = await s.post(
                    "https://toncenter.com/api/v2/sendBoc",
                    json={"boc": boc_b64},
                    timeout=aiohttp.ClientTimeout(total=15),
                )
                data = await resp.json()
                if data.get("ok"):
                    result = data.get("result", {})
                    tx_hash = result.get("hash", "submitted")
                    logger.info(f"TON tx submitted via toncenter: {tx_hash}")
                    return tx_hash
        except Exception as exc:
            logger.error(f"Broadcast error (toncenter): {exc}")

        return None
