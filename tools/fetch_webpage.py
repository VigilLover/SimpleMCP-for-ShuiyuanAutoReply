"""Safe wrapper around the MCP reference fetch server."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from urllib.parse import urljoin, urlparse

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


logger = logging.getLogger("mcp_tools.fetch")

DEFAULT_MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
# 0.6.3 appears in the upstream source metadata but was never published to
# PyPI. Pin the current published reference-server release so uvx is usable.
FETCH_SERVER_VERSION = "2026.7.10"
# The fetch release declares only mcp>=1.1.3; MCP 2.x renamed McpError and
# currently breaks this server at import time. Keep the child environment on
# the same compatible 1.x SDK release as this project.
FETCH_MCP_SDK_VERSION = "1.27.0"
FETCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class FetchSafetyError(ValueError):
    """Raised when a URL fails the fetch safety policy."""


def _max_download_bytes() -> int:
    raw_value = os.getenv("FETCH_MAX_DOWNLOAD_BYTES")
    if raw_value is None:
        return DEFAULT_MAX_DOWNLOAD_BYTES
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid FETCH_MAX_DOWNLOAD_BYTES=%r; using %d",
            raw_value,
            DEFAULT_MAX_DOWNLOAD_BYTES,
        )
        return DEFAULT_MAX_DOWNLOAD_BYTES
    if value <= 0:
        logger.warning(
            "FETCH_MAX_DOWNLOAD_BYTES must be positive; using %d",
            DEFAULT_MAX_DOWNLOAD_BYTES,
        )
        return DEFAULT_MAX_DOWNLOAD_BYTES
    return value


async def _assert_public_hostname(hostname: str) -> None:
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            None,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise FetchSafetyError("URL 主机无法解析") from exc
    if not addresses:
        raise FetchSafetyError("URL 主机无法解析")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise FetchSafetyError("禁止访问内网、回环、链路本地或保留地址")


async def _validate_public_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise FetchSafetyError("URL 端口无效") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise FetchSafetyError("仅允许 http 和 https URL")
    if not parsed.hostname:
        raise FetchSafetyError("URL 缺少有效主机")
    if parsed.username is not None or parsed.password is not None:
        raise FetchSafetyError("URL 不能包含用户名或密码")
    effective_port = port or (443 if parsed.scheme.lower() == "https" else 80)
    if effective_port not in {80, 443}:
        raise FetchSafetyError("URL 仅允许使用 80 或 443 端口")
    await _assert_public_hostname(parsed.hostname)


def _declared_content_length(response: httpx.Response) -> int | None:
    raw_value = response.headers.get("content-length")
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except ValueError:
        return None
    return value if value >= 0 else None


async def _preflight_fetch(url: str, max_download_bytes: int) -> None:
    """Validate the initial URL, redirect chain, and declared response size.

    The reference fetch server downloads the response itself, so this check is
    deliberately best-effort when a server omits or misreports Content-Length.
    """
    current_url = url
    timeout = httpx.Timeout(10.0, connect=5.0)
    headers = {"User-Agent": FETCH_USER_AGENT}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=False) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            await _validate_public_url(current_url)
            try:
                response = await client.head(current_url)
            except httpx.HTTPError as exc:
                logger.warning(
                    "Fetch preflight HEAD failed for %s; response size is unverified: %s",
                    current_url,
                    exc,
                )
                return

            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise FetchSafetyError("重定向响应缺少 Location")
                if redirect_count >= MAX_REDIRECTS:
                    raise FetchSafetyError("URL 重定向次数超过限制")
                current_url = urljoin(current_url, location)
                continue

            if response.status_code in {405, 501}:
                logger.warning(
                    "Fetch preflight HEAD is unsupported for %s; response size is unverified",
                    current_url,
                )
                return

            content_length = _declared_content_length(response)
            if content_length is None:
                logger.warning(
                    "Fetch preflight for %s returned no valid Content-Length; response size is unverified",
                    current_url,
                )
            elif content_length > max_download_bytes:
                raise FetchSafetyError(
                    f"页面声明大小 {content_length} 字节，超过允许的 {max_download_bytes} 字节"
                )
            return

    raise FetchSafetyError("URL 重定向次数超过限制")


async def fetch_webpage_content(
    url: str,
    max_length: int = 5000,
    start_index: int = 0,
    raw: bool = False,
) -> str:
    """Fetch a public web page and convert it to Markdown with the reference server.

    The wrapper performs a best-effort URL and declared-size preflight before
    starting ``mcp-server-fetch``. ``max_length`` limits returned characters;
    ``FETCH_MAX_DOWNLOAD_BYTES`` (5 MiB by default) limits declared response
    sizes when the target supplies a valid Content-Length header.
    """
    if not 1 <= max_length < 1_000_000:
        return "调用官方 fetch 工具失败：max_length 必须在 1 到 999999 之间。"
    if start_index < 0:
        return "调用官方 fetch 工具失败：start_index 不能小于 0。"

    try:
        await _preflight_fetch(url, _max_download_bytes())
    except FetchSafetyError as exc:
        return f"抓取请求被安全策略拒绝，URL: {url}\n原因: {exc}"

    server_params = StdioServerParameters(
        command="uvx",
        args=[
            "--with",
            f"mcp=={FETCH_MCP_SDK_VERSION}",
            f"mcp-server-fetch=={FETCH_SERVER_VERSION}",
            "--ignore-robots-txt",
            f"--user-agent={FETCH_USER_AGENT}",
        ],
    )

    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "fetch",
                    arguments={
                        "url": url,
                        "max_length": max_length,
                        "start_index": start_index,
                        "raw": raw,
                    },
                )
                if not result.content:
                    return "获取失败：没有返回内容"

                texts = []
                for content in result.content:
                    if content.type == "text":
                        texts.append(content.text)
                    else:
                        texts.append(f"[{content.type} content]")
                return "\n".join(texts)
    except Exception as exc:
        logger.warning("Reference fetch failed for %s: %s", url, exc)
        return (
            f"调用官方 fetch 工具失败，URL: {url}\n原因: {exc}\n"
            "提示: 请确保环境已安装 'uv' 命令，且网络允许执行 uvx。"
        )
