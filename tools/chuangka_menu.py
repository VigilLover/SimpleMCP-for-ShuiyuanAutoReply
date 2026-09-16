"""Compact, paginated access to the current ChuangKa shop menus."""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlencode

from tools.fetch_webpage import _download_public, _error

Location = Literal["all", "zhutu", "huanyuan"]

API_URL = "https://m.yk.fkw.com/api/product/list"
PAGE_LIMIT = 200
MAX_PAGES = 10
LOCATIONS = {
    "zhutu": {"label": "交图创咖（主图）", "aid": 32677668},
    "huanyuan": {"label": "交环创咖（环院）", "aid": 32822471},
}
_CATEGORY_ALIASES = {
    "all": "all",
    "全部": "all",
    "完整": "all",
    "完整菜单": "all",
    "ice_cream": "ice_cream",
    "icecream": "ice_cream",
    "冰淇淋": "ice_cream",
    "冰激凌": "ice_cream",
    "甜筒": "ice_cream",
    "圣代": "ice_cream",
}
_ICE_CREAM_MARKERS = (
    "冰淇淋",
    "冰激凌",
    "雪底",
    "筒甜",
    "甜筒",
    "圣代",
    "阿芙佳朵",
    "吐冰",
)
_FLAVOR_ALIASES = {
    "香草": ("香草",),
    "草莓": ("草莓",),
    "抹茶": ("抹茶",),
    "巧克力": ("巧克力",),
    "茉莉乌龙": ("茉莉乌龙", "乌龙", "茉莉"),
}


def _build_url(aid: int, page: int) -> str:
    query = urlencode(
        {
            "aid": aid,
            "yid": 1,
            "storeId": 0,
            "page": page,
            "limit": PAGE_LIMIT,
            "__from": 1,
        }
    )
    return f"{API_URL}?{query}"


def _ice_cream_prefix(name: str) -> str | None:
    positions = [name.find(marker) for marker in _ICE_CREAM_MARKERS]
    positions = [position for position in positions if position >= 0]
    return name[: min(positions)] if positions else None


def _ice_cream_flavors(name: str) -> list[str]:
    prefix = _ice_cream_prefix(name)
    if prefix is None:
        return []
    searchable = prefix or name
    return [
        flavor
        for flavor, aliases in _FLAVOR_ALIASES.items()
        if any(alias in searchable for alias in aliases)
    ]


def _category_ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            result.append(int(item["categoryId"]))
        except (KeyError, TypeError, ValueError):
            continue
    return list(dict.fromkeys(result))


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compact_product(row: dict[str, Any], location: str) -> dict[str, Any]:
    name = str(row.get("name") or "").strip()
    price_cents = _integer(row.get("price"))
    status_values = [row.get(key) for key in ("status", "wxStatus", "orderStatus")]
    orderable_hint = all(value in (None, 1, True, "1") for value in status_values)
    selling_point = re.sub(r"\s+", " ", str(row.get("sellingPoint") or "")).strip()
    return {
        "location": location,
        "product_id": _integer(row.get("productId") or row.get("id")),
        "name": name,
        "price_cents": price_cents,
        "price": (
            f"¥{Decimal(price_cents) / Decimal(100):.2f}"
            if price_cents is not None
            else "价格未知"
        ),
        "selling_point": selling_point[:300],
        "category_ids": _category_ids(row.get("categoryList")),
        "status": _integer(row.get("status")),
        "wx_status": _integer(row.get("wxStatus")),
        "order_status": _integer(row.get("orderStatus")),
        "orderable_hint": orderable_hint,
        "ice_cream_flavors": _ice_cream_flavors(name),
    }


async def _fetch_location(
    location: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    config = LOCATIONS[location]
    products: list[dict[str, Any]] = []
    sources: list[str] = []
    warnings: list[str] = []
    seen: set[tuple[int | None, str]] = set()
    expected_total: int | None = None

    for page_number in range(1, MAX_PAGES + 1):
        requested_url = _build_url(config["aid"], page_number)
        page = await _download_public(requested_url)
        sources.append(page.url)
        try:
            payload = json.loads(page.data.decode(page.charset, errors="replace"))
        except ValueError as exc:
            raise ValueError(f"{config['label']}返回了无效 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{config['label']}返回格式不是对象")
        if payload.get("code") not in (None, 0):
            raise ValueError(f"{config['label']}接口错误: code={payload.get('code')}")
        rows = payload.get("dataList") or []
        if not isinstance(rows, list):
            raise ValueError(f"{config['label']}的 dataList 不是数组")
        current_total = _integer(payload.get("total"))
        if expected_total is None:
            expected_total = current_total
        elif current_total is not None and current_total != expected_total:
            warnings.append(
                f"{config['label']}抓取期间 total 从 {expected_total} 变为 {current_total}"
            )
            expected_total = max(expected_total or 0, current_total)
        for row in rows:
            if not isinstance(row, dict):
                continue
            product = _compact_product(row, location)
            identity = (product["product_id"], product["name"])
            if identity in seen:
                continue
            seen.add(identity)
            products.append(product)
        if len(rows) < PAGE_LIMIT or (
            expected_total is not None and len(products) >= expected_total
        ):
            break
    else:
        warnings.append(f"{config['label']}达到 {MAX_PAGES} 页抓取上限")

    if expected_total is not None and len(products) < expected_total:
        warnings.append(
            f"{config['label']}声明 {expected_total} 项，实际取得 {len(products)} 项"
        )
    return products, sources, warnings


def _render_menu(
    products: list[dict[str, Any]],
    *,
    locations: list[str],
    category: str,
    query: str | None,
    fetched_at: str,
) -> str:
    title = "创咖当前完整菜单" if category == "all" else "创咖当前冰淇淋菜单"
    lines = [f"# {title}", f"抓取时间：{fetched_at}"]
    if query:
        lines.append(f"关键词：{query}")
    lines.append("价格来自商城当前商品列表；“当前不可下单”仅依据状态字段推断。")
    for location in locations:
        rows = [item for item in products if item["location"] == location]
        config = LOCATIONS[location]
        heading = f"## {config['label']}（{len(rows)} 项）"
        if category == "ice_cream":
            flavors = [
                flavor
                for flavor in _FLAVOR_ALIASES
                if any(flavor in item["ice_cream_flavors"] for item in rows)
            ]
            heading += f"\n当前识别口味：{'、'.join(flavors) if flavors else '未识别'}"
        lines.append(heading)
        if not rows:
            lines.append("- 没有匹配商品")
            continue
        for item in rows:
            availability = "" if item["orderable_hint"] else "｜当前不可下单"
            lines.append(f"- {item['name']}｜{item['price']}{availability}")
            if item["selling_point"]:
                lines.append(f"  {item['selling_point']}")
    return "\n".join(lines)


async def get_chuangka_menu(
    location: Location = "all",
    category: str = "all",
    query: str | None = None,
    orderable_only: bool = False,
    max_length: int = 6000,
    start_index: int = 0,
) -> dict[str, Any]:
    """Return the current ChuangKa menu or the ice-cream category."""
    if location not in {"all", *LOCATIONS}:
        return _error("invalid_arguments", "location 必须是 all/zhutu/huanyuan")
    normalized_category = _CATEGORY_ALIASES.get(category.strip().casefold())
    if normalized_category is None:
        return _error("invalid_arguments", "category 当前仅支持 all 或 ice_cream")
    if not 1 <= max_length <= 12_000:
        return _error("invalid_arguments", "max_length 必须在 1 到 12000 之间")
    if start_index < 0:
        return _error("invalid_arguments", "start_index 不能小于 0")
    normalized_query = query.strip() if query else None
    if normalized_query and len(normalized_query) > 200:
        return _error("invalid_arguments", "query 最多 200 个字符")

    selected_locations = list(LOCATIONS) if location == "all" else [location]
    products: list[dict[str, Any]] = []
    source_urls: list[str] = []
    warnings: list[str] = []
    failed_locations: list[str] = []
    total_by_location: dict[str, int] = {}
    for location_name in selected_locations:
        try:
            rows, sources, fetch_warnings = await _fetch_location(location_name)
        except Exception as exc:  # one shop must not hide the other shop's menu
            failed_locations.append(location_name)
            warnings.append(
                f"{LOCATIONS[location_name]['label']}抓取失败: {type(exc).__name__}"
            )
            continue
        products.extend(rows)
        source_urls.extend(sources)
        warnings.extend(fetch_warnings)
        total_by_location[location_name] = len(rows)

    if not total_by_location:
        result = _error("fetch_failed", "创咖菜单抓取失败", retryable=True)
        result["warnings"] = warnings
        return result

    total_products = len(products)
    if normalized_category == "ice_cream":
        products = [
            item for item in products if _ice_cream_prefix(item["name"]) is not None
        ]
    if orderable_only:
        products = [item for item in products if item["orderable_hint"]]
    if normalized_query:
        needle = normalized_query.casefold()
        products = [
            item
            for item in products
            if needle in f"{item['name']} {item['selling_point']}".casefold()
        ]

    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    content = _render_menu(
        products,
        locations=selected_locations,
        category=normalized_category,
        query=normalized_query,
        fetched_at=fetched_at,
    )
    if start_index > len(content):
        return _error("invalid_arguments", "start_index 超过清洗后菜单长度")
    end = min(start_index + max_length, len(content))
    return {
        "status": "ok",
        "source": "chuangka_current_product_list",
        "fetched_at": fetched_at,
        "location": location,
        "category": normalized_category,
        "query": normalized_query,
        "orderable_only": orderable_only,
        "total_products": total_products,
        "total_by_location": total_by_location,
        "matched_count": len(products),
        "content": content[start_index:end],
        "total_chars": len(content),
        "start_index": start_index,
        "truncated": end < len(content),
        "next_start_index": end if end < len(content) else None,
        "source_urls": list(dict.fromkeys(source_urls)),
        "failed_locations": failed_locations,
        "warnings": warnings,
    }
