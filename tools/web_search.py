"""Structured web, news, and image search tools backed by DDGS."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ddgs import DDGS
from ddgs.engines.bing import Bing


logger = logging.getLogger("mcp_tools.search")

SearchCategory = Literal["text", "news"]
SafeSearch = Literal["on", "moderate", "off"]
TimeLimit = Literal["d", "w", "m", "y"]

_PRIMARY_BACKENDS = {
    # DDGS 9.15.0 disables Bing in its public text-engine registry even though
    # the bundled engine still works. _ddgs_search handles this value with a
    # pinned, controlled adapter instead of allowing DDGS to fall back to auto.
    "text": "bing",
    "news": "bing",
    "images": "bing",
}
_FALLBACK_BACKENDS = {
    "text": "startpage",
    "news": "duckduckgo",
    "images": "duckduckgo",
}
_CACHE_TTL_SECONDS = {"text": 15 * 60, "news": 2 * 60, "images": 15 * 60}
_CACHE_MAX_ENTRIES = 256
_TRACKING_PARAMETERS = {"fbclid", "gclid"}
_TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)


class _CompatibleBing(Bing):
    """Bing text engine compatible with both current result-title layouts."""

    elements_xpath = {
        "title": ".//h2//text()",
        "href": ".//h2/a/@href | .//h2/parent::a/@href",
        "body": ".//p//text()",
    }


class _TTLCache:
    """Small process-local, thread-safe LRU/TTL cache."""

    def __init__(self, max_entries: int = _CACHE_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._items: OrderedDict[tuple[Any, ...], tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return copy.deepcopy(value)

    def set(self, key: tuple[Any, ...], value: dict[str, Any], ttl: int) -> None:
        with self._lock:
            self._items[key] = (time.monotonic() + ttl, copy.deepcopy(value))
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_SEARCH_CACHE = _TTLCache()


def _validate_common_inputs(
    query: str,
    region: str,
    safesearch: str,
    timelimit: str | None,
    max_results: int,
    page: int,
) -> str:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query 不能为空")
    if len(normalized_query) > 512:
        raise ValueError("query 不能超过 512 个字符")
    if not region.strip():
        raise ValueError("region 不能为空")
    if safesearch not in {"on", "moderate", "off"}:
        raise ValueError("safesearch 必须是 on、moderate 或 off")
    if timelimit not in {None, "d", "w", "m", "y"}:
        raise ValueError("timelimit 必须是 d、w、m、y 或空值")
    if not 1 <= max_results <= 10:
        raise ValueError("max_results 必须在 1 到 10 之间")
    if not 1 <= page <= 3:
        raise ValueError("page 必须在 1 到 3 之间")
    return normalized_query


def _normalize_domain(domain: str) -> str:
    value = domain.strip().lower().rstrip(".")
    if not value:
        raise ValueError("域名不能为空")
    parsed = urlparse(value if "://" in value else f"//{value}")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError(f"域名过滤器不能包含凭据或端口: {domain}")
    hostname = parsed.hostname
    if not hostname or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(f"无效域名: {domain}")
    try:
        return hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ValueError(f"无效域名: {domain}") from exc


def _normalize_domains(domains: list[str] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_normalize_domain(domain) for domain in domains or []))


def _domain_matches(hostname: str, domain: str) -> bool:
    host = hostname.lower().rstrip(".")
    return host == domain or host.endswith(f".{domain}")


def _url_allowed(url: str, included: tuple[str, ...], excluded: tuple[str, ...]) -> bool:
    try:
        hostname = (urlparse(url).hostname or "").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return False
    if not hostname:
        return False
    if included and not any(_domain_matches(hostname, domain) for domain in included):
        return False
    return not any(_domain_matches(hostname, domain) for domain in excluded)


def _canonicalize_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
        port = parsed.port
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return ""
    netloc = hostname
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_PARAMETERS:
            continue
        filtered_query.append((key, value))
    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path or "/",
            "",
            urlencode(filtered_query, doseq=True),
            "",
        )
    )


def _build_effective_query(
    query: str, included: tuple[str, ...], excluded: tuple[str, ...]
) -> str:
    operators: list[str] = []
    if included:
        site_expression = " OR ".join(f"site:{domain}" for domain in included)
        operators.append(f"({site_expression})" if len(included) > 1 else site_expression)
    operators.extend(f"-site:{domain}" for domain in excluded)
    return " ".join([query, *operators]).strip()


def _query_tokens(query: str) -> set[str]:
    return {token for token in _TOKEN_PATTERN.findall(query.lower()) if len(token) >= 2}


def _relevance_score(result: dict[str, Any], tokens: set[str]) -> int:
    title = str(result.get("title") or "").lower()
    snippet = str(result.get("snippet") or "").lower()
    return 2 * sum(token in title for token in tokens) + sum(token in snippet for token in tokens)


def _as_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_result(category: str, raw: dict[str, Any], include_preview_images: bool) -> dict[str, Any]:
    if category == "images":
        page_url = _canonicalize_url(str(raw.get("url") or ""))
        image_url = _canonicalize_url(str(raw.get("image") or "")) or None
        thumbnail_url = _canonicalize_url(str(raw.get("thumbnail") or "")) or None
        return {
            "title": str(raw.get("title") or ""),
            "url": page_url,
            "snippet": "",
            "source": str(raw.get("source") or ""),
            "published_at": None,
            "image_url": image_url,
            "thumbnail_url": thumbnail_url,
            "width": _as_int(raw.get("width")),
            "height": _as_int(raw.get("height")),
        }
    if category == "news":
        return {
            "title": str(raw.get("title") or ""),
            "url": _canonicalize_url(str(raw.get("url") or "")),
            "snippet": str(raw.get("body") or ""),
            "source": str(raw.get("source") or ""),
            "published_at": str(raw.get("date") or "") or None,
            "image_url": (
                _canonicalize_url(str(raw.get("image") or "")) or None
            )
            if include_preview_images
            else None,
            "thumbnail_url": None,
            "width": None,
            "height": None,
        }
    return {
        "title": str(raw.get("title") or ""),
        "url": _canonicalize_url(str(raw.get("href") or raw.get("link") or "")),
        "snippet": str(raw.get("body") or raw.get("snippet") or ""),
        "source": str(raw.get("source") or ""),
        "published_at": None,
        "image_url": None,
        "thumbnail_url": None,
        "width": None,
        "height": None,
    }


def _filter_rank_results(
    raw_results: list[dict[str, Any]],
    *,
    category: str,
    query: str,
    included: tuple[str, ...],
    excluded: tuple[str, ...],
    max_results: int,
    include_preview_images: bool,
) -> list[dict[str, Any]]:
    tokens = _query_tokens(query)
    deduplicated: dict[str, tuple[int, int, dict[str, Any]]] = {}
    for index, raw in enumerate(raw_results):
        result = _normalize_result(category, raw, include_preview_images)
        page_url = str(result["url"] or "")
        if not page_url or not _url_allowed(page_url, included, excluded):
            continue
        if category == "images" and not result["image_url"]:
            continue
        dedupe_url = str(result["image_url"] or page_url) if category == "images" else page_url
        dedupe_key = _canonicalize_url(dedupe_url)
        if not dedupe_key:
            continue
        candidate = (_relevance_score(result, tokens), index, result)
        existing = deduplicated.get(dedupe_key)
        if existing is None or candidate[0] > existing[0]:
            deduplicated[dedupe_key] = candidate
    ranked = list(deduplicated.values())
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[:max_results]]


def _ddgs_search(
    category: str,
    query: str,
    *,
    region: str,
    safesearch: str,
    timelimit: str | None,
    max_results: int,
    page: int,
    backend: str,
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if category == "text" and backend == "bing":
        # Calling DDGS.text(..., backend="bing") does not select Bing in
        # 9.15.0: the disabled engine is absent from the registry and DDGS
        # silently switches to "auto". Invoke the pinned bundled engine
        # explicitly so backend selection remains deterministic.
        engine = _CompatibleBing(proxy=os.getenv("DDGS_PROXY"), timeout=5)
        results = engine.search(
            query,
            region=region,
            safesearch=safesearch,
            timelimit=timelimit,
            page=page,
            **extra,
        )
        return [
            {
                "title": result.title,
                "href": result.href,
                "body": result.body,
            }
            for result in results[:max_results]
        ]

    client = DDGS()
    method = getattr(client, category)
    return list(
        method(
            query=query,
            region=region,
            safesearch=safesearch,
            timelimit=timelimit,
            max_results=max_results,
            page=page,
            backend=backend,
            **extra,
        )
    )


async def _run_search(
    *,
    query: str,
    category: str,
    region: str,
    safesearch: str,
    timelimit: str | None,
    max_results: int,
    page: int,
    included: tuple[str, ...],
    excluded: tuple[str, ...],
    include_preview_images: bool,
    extra: dict[str, Any],
) -> dict[str, Any]:
    effective_query = _build_effective_query(query, included, excluded)
    cache_key = (
        category,
        effective_query,
        region,
        safesearch,
        timelimit,
        max_results,
        page,
        included,
        excluded,
        include_preview_images,
        tuple(sorted(extra.items())),
    )
    cached = _SEARCH_CACHE.get(cache_key)
    if cached is not None:
        cached["cached"] = True
        return cached

    candidate_limit = min(max_results * 3, 30)
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    for attempt, backend in enumerate((_PRIMARY_BACKENDS[category], _FALLBACK_BACKENDS[category])):
        try:
            raw_results = await asyncio.to_thread(
                _ddgs_search,
                category,
                effective_query,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
                max_results=candidate_limit,
                page=page,
                backend=backend,
                extra=extra,
            )
        except Exception as exc:  # DDGS wraps several backend-specific exception types.
            logger.warning("DDGS %s search failed with backend %s: %s", category, backend, exc)
            if attempt == 0:
                warnings.append("主搜索后端不可用，已尝试备用后端。")
            else:
                warnings.append("备用搜索后端也不可用。")
            continue
        results = _filter_rank_results(
            raw_results,
            category=category,
            query=query,
            included=included,
            excluded=excluded,
            max_results=max_results,
            include_preview_images=include_preview_images,
        )
        if results:
            break
        if attempt == 0:
            warnings.append("主搜索后端未返回符合条件的结果，已尝试备用后端。")
    if not results:
        warnings.append("未找到符合条件的结果。")
    response = {
        "query": query,
        "category": category,
        "count": len(results),
        "cached": False,
        "results": results,
        "warnings": warnings,
    }
    if results:
        _SEARCH_CACHE.set(cache_key, response, _CACHE_TTL_SECONDS[category])
    return response


async def web_search(
    query: str,
    category: SearchCategory = "text",
    region: str = "cn-zh",
    safesearch: SafeSearch = "moderate",
    timelimit: TimeLimit | None = None,
    max_results: int = 5,
    page: int = 1,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_preview_images: bool = False,
) -> dict[str, Any]:
    """Search web pages or recent news using bounded, task-neutral DDGS backends.

    Use ``include_domains`` to restrict results to one or more websites and
    ``exclude_domains`` to remove websites. Domain filtering is exact and also
    accepts legitimate subdomains. Use ``category='news'`` for time-sensitive
    news results. This tool does not fetch full page contents; call
    ``fetch_webpage_content`` for that.
    """
    normalized_query = _validate_common_inputs(query, region, safesearch, timelimit, max_results, page)
    if category not in {"text", "news"}:
        raise ValueError("category 必须是 text 或 news")
    included = _normalize_domains(include_domains)
    excluded = _normalize_domains(exclude_domains)
    return await _run_search(
        query=normalized_query,
        category=category,
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        max_results=max_results,
        page=page,
        included=included,
        excluded=excluded,
        include_preview_images=include_preview_images,
        extra={},
    )


async def image_search(
    query: str,
    region: str = "cn-zh",
    safesearch: SafeSearch = "moderate",
    timelimit: TimeLimit | None = None,
    max_results: int = 5,
    page: int = 1,
    size: str | None = None,
    color: str | None = None,
    type_image: str | None = None,
    layout: str | None = None,
    license_image: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Search images using DDGS and return source pages plus image URLs.

    The tool is content-neutral. Use ``include_domains`` and
    ``exclude_domains`` for website constraints. It returns URLs and metadata;
    it does not download image bytes.
    """
    normalized_query = _validate_common_inputs(query, region, safesearch, timelimit, max_results, page)
    included = _normalize_domains(include_domains)
    excluded = _normalize_domains(exclude_domains)
    extra = {
        key: value
        for key, value in {
            "size": size,
            "color": color,
            "type_image": type_image,
            "layout": layout,
            "license_image": license_image,
        }.items()
        if value is not None
    }
    return await _run_search(
        query=normalized_query,
        category="images",
        region=region,
        safesearch=safesearch,
        timelimit=timelimit,
        max_results=max_results,
        page=page,
        included=included,
        excluded=excluded,
        include_preview_images=True,
        extra=extra,
    )
