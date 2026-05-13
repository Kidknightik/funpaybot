"""Fragment HTTP client for user search (no auth required)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import aiohttp
from loguru import logger

from config import settings

_FRAGMENT_API = "https://fragment.com/api"
_TIMEOUT = aiohttp.ClientTimeout(total=15)


@dataclass
class FragmentUser:
    username: str
    name: str
    peer_type: str    # "user" | "bot" | "channel"
    photo_url: Optional[str] = None


class FragmentSearchClient:
    """Searches for Telegram usernames via Fragment's undocumented mention-search API."""

    def __init__(self) -> None:
        proxy = settings.FRAGMENT_PROXY or None
        self._proxy = proxy

    async def search_username(self, username: str) -> Optional[FragmentUser]:
        """
        Returns a FragmentUser if the username is registered on Fragment, else None.
        """
        username = username.lstrip("@")
        params = {"method": "searchMention", "query": username, "limit": "5"}

        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(
                connector=connector, timeout=_TIMEOUT
            ) as session:
                async with session.get(
                    _FRAGMENT_API,
                    params=params,
                    proxy=self._proxy,
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"Fragment search returned HTTP {resp.status}")
                        return None
                    data = await resp.json(content_type=None)
        except Exception as exc:
            logger.error(f"Fragment search error: {exc}")
            return None

        if not data.get("ok") or not data.get("results"):
            return None

        for item in data["results"]:
            if item.get("username", "").lower() == username.lower():
                return FragmentUser(
                    username=item["username"],
                    name=item.get("name", item["username"]),
                    peer_type=item.get("peer_type", "user"),
                    photo_url=item.get("photo"),
                )
        return None
