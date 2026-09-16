"""Bounded, public-only webpage retrieval for the MCP server."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver


DEFAULT_MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
FETCH_TIMEOUT_SECONDS = 30
CACHE_TTL_SECONDS = 60
CACHE_MAX_ENTRIES = 16
CACHE_MAX_BYTES = 32 * 1024 * 1024
FETCH_USER_AGENT = "ShuiyuanMCP/2.0"
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_gate = asyncio.Semaphore(1)


class FetchSafetyError(ValueError):
    """Raised when a URL or response violates the public fetch policy."""


def _public_address(address: str) -> bool:
    value = ipaddress.ip_address(address)
    return value.is_global and not (
        getattr(value, "ipv4_mapped", None) and not value.ipv4_mapped.is_global
    )


def _validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise FetchSafetyError("URL 端口无效") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise FetchSafetyError("仅允许 http 和 https URL")
    if not parsed.hostname:
        raise FetchSafetyError("URL 缺少有效主机")
    if parsed.username is not None or parsed.password is not None:
        raise FetchSafetyError("URL 不能包含用户名或密码")
    if (port or (443 if parsed.scheme.lower() == "https" else 80)) not in {80, 443}:
        raise FetchSafetyError("URL 仅允许使用 80 或 443 端口")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not _public_address(str(address)):
        raise FetchSafetyError("禁止访问内网、回环、链路本地或保留地址")


class PublicResolver(AbstractResolver):
    """Resolve the address used by the real GET and reject every private answer."""

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        rows = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM, family=family
        )
        if not rows or any(not _public_address(row[4][0]) for row in rows):
            raise FetchSafetyError("DNS 解析到了非公网地址")
        return [
            {
                "hostname": host,
                "host": row[4][0],
                "port": port,
                "family": row[0],
                "proto": row[2],
                "flags": socket.AI_NUMERICHOST,
            }
            for row in rows
        ]

    async def close(self) -> None:
        return None


@dataclass(frozen=True)
class DownloadedPage:
    url: str
    content_type: str
    charset: str
    data: bytes


@dataclass
class _CacheEntry:
    page: DownloadedPage
    stored_at: float


_cache: OrderedDict[str, _CacheEntry] = OrderedDict()
_cache_bytes = 0


def _cache_get(url: str) -> DownloadedPage | None:
    global _cache_bytes
    entry = _cache.get(url)
    if entry is None:
        return None
    if time.monotonic() - entry.stored_at > CACHE_TTL_SECONDS:
        _cache.pop(url, None)
        _cache_bytes -= len(entry.page.data)
        return None
    _cache.move_to_end(url)
    return entry.page


def _cache_put(url: str, page: DownloadedPage) -> None:
    global _cache_bytes
    old = _cache.pop(url, None)
    if old is not None:
        _cache_bytes -= len(old.page.data)
    _cache[url] = _CacheEntry(page=page, stored_at=time.monotonic())
    _cache_bytes += len(page.data)
    while len(_cache) > CACHE_MAX_ENTRIES or _cache_bytes > CACHE_MAX_BYTES:
        _, removed = _cache.popitem(last=False)
        _cache_bytes -= len(removed.page.data)


def _clear_fetch_cache() -> None:
    global _cache_bytes
    _cache.clear()
    _cache_bytes = 0


async def _download_public(url: str) -> DownloadedPage:
    cached = _cache_get(url)
    if cached is not None:
        return cached

    async with _gate:
        cached = _cache_get(url)
        if cached is not None:
            return cached
        connector = aiohttp.TCPConnector(
            resolver=PublicResolver(), use_dns_cache=False, limit=1
        )
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
            auto_decompress=True,
            headers={"User-Agent": FETCH_USER_AGENT},
        ) as client:
            current_url = url
            for redirect_count in range(MAX_REDIRECTS + 1):
                _validate_url(current_url)
                async with client.get(current_url, allow_redirects=False) as response:
                    if response.status in _REDIRECT_STATUSES:
                        location = response.headers.get("Location")
                        if not location:
                            raise FetchSafetyError("重定向响应缺少 Location")
                        if redirect_count >= MAX_REDIRECTS:
                            raise FetchSafetyError("URL 重定向次数超过限制")
                        current_url = urljoin(current_url, location)
                        continue
                    response.raise_for_status()
                    data = bytearray()
                    async for block in response.content.iter_chunked(16384):
                        if len(data) + len(block) > DEFAULT_MAX_DOWNLOAD_BYTES:
                            raise FetchSafetyError("响应正文超过 5 MiB")
                        data.extend(block)
                    content_type = response.headers.get("Content-Type", "")
                    try:
                        charset = response.charset or "utf-8"
                    except (LookupError, ValueError):
                        charset = "utf-8"
                    page = DownloadedPage(
                        url=current_url,
                        content_type=content_type,
                        charset=charset,
                        data=bytes(data),
                    )
                    _cache_put(url, page)
                    return page
    raise FetchSafetyError("没有收到有效响应")


async def fetch_webpage_content(
    url: str,
    max_length: int = 5000,
    start_index: int = 0,
    raw: bool = False,
) -> str:
    """Fetch a public page with an enforced download bound and return one slice."""
    del raw  # Kept for wire compatibility; structured extraction handles it later.
    if not 1 <= max_length < 1_000_000:
        return "调用网页抓取工具失败：max_length 必须在 1 到 999999 之间。"
    if start_index < 0:
        return "调用网页抓取工具失败：start_index 不能小于 0。"
    try:
        page = await _download_public(url)
        text = page.data.decode(page.charset, errors="replace")
        return text[start_index : start_index + max_length]
    except (aiohttp.ClientError, FetchSafetyError, TimeoutError, LookupError) as exc:
        return f"调用网页抓取工具失败，URL: {url}\n原因: {exc}"
