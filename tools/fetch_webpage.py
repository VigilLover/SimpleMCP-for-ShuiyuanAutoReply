"""Bounded, public-only webpage retrieval for the MCP server."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from bs4 import BeautifulSoup, Comment


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

FetchMode = Literal["auto", "document", "json", "raw"]
_NOISE_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "dialog",
    "svg",
    "canvas",
}
_JSON_PATH = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\Z")
_NOISY_FIELD = re.compile(r"(?:^|_)(?:img|image|picture|thumbnail|video|url)s?(?:_|$)", re.I)


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


def _normalize_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    result: list[str] = []
    for line in lines:
        if not line:
            if result and result[-1]:
                result.append("")
        elif not result or result[-1] != line:
            result.append(line)
    return "\n".join(result).strip()


_OVERLAY_SELECTORS = (
    "[class*=cookie]",
    "[id*=cookie]",
    "[class*=consent]",
    "[id*=consent]",
    "[class*=subscribe]",
    "[class*=newsletter]",
    "[class*=popup]",
    "[class*=modal]",
    "[role=dialog]",
    "[role=alertdialog]",
    "[class*=share]",
    "[class*=breadcrumb]",
    "[class*=comment]",
    "[id*=comment]",
    "[class*=related]",
    "[class*=recommend]",
    "[class*=sidebar]",
)
_BLOCK_TAGS = [
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "pre",
    "blockquote",
    "tr",
    "figcaption",
    "dt",
    "dd",
]
_DATE_META = (
    ("property", "article:published_time"),
    ("name", "article:published_time"),
    ("property", "og:published_time"),
    ("name", "pubdate"),
    ("name", "publishdate"),
    ("name", "date"),
    ("itemprop", "datePublished"),
    ("name", "citation_publication_date"),
)


def _page_metadata(soup: BeautifulSoup) -> dict[str, str]:
    """Title and publish date when the page declares them; nothing is guessed."""
    result: dict[str, str] = {}
    for attrs in (
        {"property": "og:title"},
        {"name": "twitter:title"},
    ):
        node = soup.find("meta", attrs=attrs)
        if node and node.get("content"):
            result["title"] = _normalize_text(str(node["content"]))
            break
    if "title" not in result:
        node = soup.find("title")
        if node and node.get_text(strip=True):
            result["title"] = _normalize_text(node.get_text(" ", strip=True))
    for key, value in _DATE_META:
        node = soup.find("meta", attrs={key: value})
        if node and node.get("content"):
            result["published_at"] = str(node["content"]).strip()
            break
    if "published_at" not in result:
        node = soup.find("time", attrs={"datetime": True})
        if node:
            result["published_at"] = str(node["datetime"]).strip()
    return result


def _block_markdown(node) -> str:
    """Render one block as light Markdown so structure survives the flattening."""
    name = node.name
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        text = _normalize_text(node.get_text(" ", strip=True))
        return f"{'#' * int(name[1])} {text}" if text else ""
    if name == "pre":
        code = node.get_text("\n")
        code = "\n".join(line.rstrip() for line in code.splitlines()).strip("\n")
        return f"```\n{code}\n```" if code.strip() else ""
    if name == "li":
        text = _normalize_text(node.get_text(" ", strip=True))
        return f"- {text}" if text else ""
    if name == "blockquote":
        text = _normalize_text(node.get_text(" ", strip=True))
        return f"> {text}" if text else ""
    if name == "tr":
        cells = [
            _normalize_text(cell.get_text(" ", strip=True))
            for cell in node.find_all(["th", "td"], recursive=False)
        ]
        cells = [cell for cell in cells if cell]
        return "| " + " | ".join(cells) + " |" if cells else ""
    if name == "dt":
        text = _normalize_text(node.get_text(" ", strip=True))
        return f"**{text}**" if text else ""
    return _normalize_text(node.get_text(" ", strip=True))


def _document_text(html: str, query: str | None) -> tuple[str, dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    metadata = _page_metadata(soup)
    for node in soup.find_all(_NOISE_TAGS):
        node.decompose()
    for selector in _OVERLAY_SELECTORS:
        for node in soup.select(selector):
            # Never strip the main article container because of a loose class name.
            if node.name in {"article", "main", "body", "html"}:
                continue
            node.decompose()
    hidden_nodes = soup.find_all(
        lambda node: getattr(node, "attrs", None)
        and (
            node.has_attr("hidden")
            or str(node.get("aria-hidden", "")).casefold() == "true"
            or bool(
                re.search(
                    r"(?:display\s*:\s*none|visibility\s*:\s*hidden)",
                    str(node.get("style", "")),
                    re.I,
                )
            )
        )
    )
    for node in hidden_nodes:
        node.decompose()
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    root = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.body
        or soup
    )
    blocks: list[str] = []
    seen: set[str] = set()
    for node in root.find_all(_BLOCK_TAGS):
        # Nested blocks (a <p> inside <li>, a <li> inside <blockquote>) would be
        # emitted twice; keep the outermost rendering only.
        if node.find_parent(_BLOCK_TAGS) is not None and node.name != "tr":
            continue
        text = _block_markdown(node)
        key = text.lstrip("#>-*| ").casefold()
        if text and key and key not in seen:
            seen.add(key)
            blocks.append(text)
    if not blocks:
        blocks = [
            line for line in _normalize_text(root.get_text("\n")).splitlines() if line
        ]
    if query:
        needle = query.casefold()
        indexes = {i for i, block in enumerate(blocks) if needle in block.casefold()}
        selected = sorted(
            index
            for hit in indexes
            for index in range(max(0, hit - 2), min(len(blocks), hit + 3))
        )
        blocks = [blocks[index] for index in selected]
    return "\n\n".join(blocks), metadata


def _resolve_path(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"JSON 路径不存在: {path}")
        current = current[part]
    return current


def _largest_top_level_list(value: Any) -> tuple[Any, str | None]:
    if not isinstance(value, dict):
        return value, None
    candidates = [
        (len(item), key, item)
        for key, item in value.items()
        if isinstance(item, list)
    ]
    if not candidates:
        return value, None
    _, key, result = max(candidates, key=lambda row: row[0])
    return result, key


def _contains_query(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_query(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_query(item, needle) for item in value)
    return needle in str(value).casefold()


def _field_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _compact_item(value: Any, fields: list[str] | None) -> Any:
    if fields:
        return {
            field: selected
            for field in fields
            if (selected := _field_value(value, field)) is not None
        }
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if _NOISY_FIELD.search(key):
            continue
        if item is None or isinstance(item, (bool, int, float)):
            result[key] = item
        elif isinstance(item, str):
            if item.startswith(("http://", "https://")):
                continue
            result[key] = item[:300]
        if len(result) >= 24:
            break
    return result


def _json_text(
    value: Any,
    *,
    query: str | None,
    json_path: str | None,
    fields: list[str] | None,
    max_results: int,
) -> tuple[str, int]:
    selected = _resolve_path(value, json_path)
    selected_path = json_path
    if not json_path:
        selected, selected_path = _largest_top_level_list(selected)
    rows = selected if isinstance(selected, list) else [selected]
    if query:
        needle = query.casefold()
        rows = [row for row in rows if _contains_query(row, needle)]
    matched_count = len(rows)
    payload = {
        "path": selected_path,
        "total_items": len(selected) if isinstance(selected, list) else 1,
        "matched_items": matched_count,
        "items": [_compact_item(row, fields) for row in rows[:max_results]],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), matched_count


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "status": "error",
        "code": code,
        "message": message,
        "retryable": retryable,
    }


async def fetch_webpage_content(
    url: str,
    max_length: int = 8000,
    start_index: int = 0,
    raw: bool = False,
    mode: FetchMode = "auto",
    query: str | None = None,
    json_path: str | None = None,
    fields: list[str] | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    """Fetch, clean, query, and page one public HTML, JSON, or text resource."""
    if not 1 <= max_length < 1_000_000:
        return _error("invalid_arguments", "max_length 必须在 1 到 999999 之间")
    if start_index < 0:
        return _error("invalid_arguments", "start_index 不能小于 0")
    if mode not in {"auto", "document", "json", "raw"}:
        return _error("invalid_arguments", "mode 必须是 auto/document/json/raw")
    if raw:
        if mode not in {"auto", "raw"}:
            return _error("invalid_arguments", "raw=true 不能与其他 mode 同时使用")
        mode = "raw"
    query = query.strip() if query else None
    if query and len(query) > 500:
        return _error("invalid_arguments", "query 最多 500 个字符")
    if json_path and not _JSON_PATH.fullmatch(json_path):
        return _error("invalid_arguments", "json_path 只允许点分隔的对象键")
    if fields and (
        len(fields) > 32 or any(not _JSON_PATH.fullmatch(field) for field in fields)
    ):
        return _error("invalid_arguments", "fields 最多 32 个点分隔字段")
    if not 1 <= max_results <= 100:
        return _error("invalid_arguments", "max_results 必须在 1 到 100 之间")
    try:
        page = await _download_public(url)
        text = page.data.decode(page.charset, errors="replace")
        mime_type = page.content_type.split(";", 1)[0].strip().lower()
        parsed_json = None
        if mode in {"auto", "json"} and (
            "json" in mime_type or text.lstrip().startswith(("{", "["))
        ):
            try:
                parsed_json = json.loads(text)
            except ValueError:
                if mode == "json":
                    return _error("invalid_json", "响应不是有效 JSON")
        selected_mode = mode
        matched_count = None
        metadata: dict[str, str] = {}
        if mode == "raw":
            processed = text
        elif parsed_json is not None:
            selected_mode = "json"
            processed, matched_count = _json_text(
                parsed_json,
                query=query,
                json_path=json_path,
                fields=fields,
                max_results=max_results,
            )
        elif mode == "json":
            return _error("invalid_json", "响应不是有效 JSON")
        elif mode == "document" or "html" in mime_type:
            selected_mode = "document"
            processed, metadata = _document_text(text, query)
        else:
            selected_mode = "document"
            normalized = _normalize_text(text)
            if query:
                needle = query.casefold()
                lines = normalized.splitlines()
                indexes = {i for i, line in enumerate(lines) if needle in line.casefold()}
                selected = sorted(
                    index
                    for hit in indexes
                    for index in range(max(0, hit - 1), min(len(lines), hit + 2))
                )
                normalized = "\n".join(lines[index] for index in selected)
            processed = normalized
        if start_index > len(processed):
            return _error("invalid_arguments", "start_index 超过清洗后正文长度")
        end = min(start_index + max_length, len(processed))
        result = {
            "status": "ok",
            "url": page.url,
            "content_type": mime_type or "application/octet-stream",
            "mode": selected_mode,
            "content": processed[start_index:end],
            "total_chars": len(processed),
            "start_index": start_index,
            "truncated": end < len(processed),
            "next_start_index": end if end < len(processed) else None,
            "warnings": [],
        }
        if matched_count is not None:
            result["matched_count"] = matched_count
        result.update(metadata)
        return result
    except FetchSafetyError as exc:
        return _error("blocked_url", str(exc))
    except (aiohttp.ClientError, TimeoutError, LookupError) as exc:
        return _error("fetch_failed", type(exc).__name__, retryable=True)
    except ValueError as exc:
        return _error("invalid_arguments", str(exc))
