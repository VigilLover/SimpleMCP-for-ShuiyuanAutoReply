import json
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from tools.chuangka_menu import get_chuangka_menu
from tools.fetch_webpage import DownloadedPage


class _FixedDatetime:
    @classmethod
    def now(cls):
        return datetime.fromisoformat("2026-09-16T12:00:00+08:00")


def _product(
    product_id,
    name,
    price,
    *,
    selling_point="",
    order_status=1,
    category_ids=(),
):
    return {
        "productId": product_id,
        "name": name,
        "price": price,
        "sellingPoint": selling_point,
        "status": 1,
        "wxStatus": 1,
        "orderStatus": order_status,
        "categoryList": [{"categoryId": category_id} for category_id in category_ids],
        "imgUrls": ["https://cdn.example.com/" + "x" * 1000],
        "skuList": [{"noise": "y" * 1000}],
    }


class ChuangKaMenuTests(unittest.IsolatedAsyncioTestCase):
    def _downloader(self, *, fail_huanyuan=False):
        pages = {
            (32677668, 1): {
                "code": 0,
                "total": 3,
                "dataList": [
                    _product(1, "香草·筒甜", 350, category_ids=(23, 119)),
                    _product(2, "香草拿铁", 1200, selling_point="咖啡饮品"),
                ],
            },
            (32677668, 2): {
                "code": 0,
                "total": 3,
                "dataList": [_product(3, "半熟芝士糕点（抹茶味）", 990)],
            },
            (32822471, 1): {
                "code": 0,
                "total": 2,
                "dataList": [
                    _product(4, "草莓味圣代", 250, selling_point="草莓冰淇淋"),
                    _product(5, "暂停供应咖啡", 800, order_status=0),
                ],
            },
        }

        async def download(url):
            query = parse_qs(urlsplit(url).query)
            aid = int(query["aid"][0])
            page = int(query["page"][0])
            if fail_huanyuan and aid == 32822471:
                raise TimeoutError("offline")
            data = json.dumps(pages[(aid, page)], ensure_ascii=False).encode()
            return DownloadedPage(url, "application/json", "utf-8", data)

        return download

    async def test_complete_menu_fetches_every_api_page_and_drops_noise(self):
        with (
            patch("tools.chuangka_menu.PAGE_LIMIT", 2),
            patch("tools.chuangka_menu.datetime", _FixedDatetime),
            patch(
                "tools.chuangka_menu._download_public",
                side_effect=self._downloader(),
            ) as download,
        ):
            result = await get_chuangka_menu(max_length=12_000)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["total_products"], 5)
        self.assertEqual(result["matched_count"], 5)
        self.assertEqual(result["total_by_location"], {"zhutu": 3, "huanyuan": 2})
        self.assertEqual(download.await_count, 3)
        self.assertIn("香草·筒甜｜¥3.50", result["content"])
        self.assertIn("半熟芝士糕点（抹茶味）｜¥9.90", result["content"])
        self.assertIn("暂停供应咖啡｜¥8.00｜当前不可下单", result["content"])
        self.assertNotIn("imgUrls", result["content"])
        self.assertFalse(result["truncated"])

    async def test_ice_cream_category_uses_semantic_markers_without_false_positives(
        self,
    ):
        with (
            patch("tools.chuangka_menu.PAGE_LIMIT", 2),
            patch(
                "tools.chuangka_menu._download_public",
                side_effect=self._downloader(),
            ),
        ):
            result = await get_chuangka_menu(category="冰淇淋", max_length=12_000)

        self.assertEqual(result["category"], "ice_cream")
        self.assertEqual(result["matched_count"], 2)
        self.assertIn("香草·筒甜", result["content"])
        self.assertIn("草莓味圣代", result["content"])
        self.assertIn("当前识别口味：香草", result["content"])
        self.assertIn("当前识别口味：草莓", result["content"])
        self.assertNotIn("香草拿铁", result["content"])
        self.assertNotIn("抹茶味", result["content"])

    async def test_query_orderable_filter_and_location_are_applied(self):
        with (
            patch("tools.chuangka_menu.PAGE_LIMIT", 2),
            patch(
                "tools.chuangka_menu._download_public",
                side_effect=self._downloader(),
            ),
        ):
            result = await get_chuangka_menu(
                location="huanyuan",
                query="咖啡",
                orderable_only=True,
                max_length=12_000,
            )

        self.assertEqual(result["total_products"], 2)
        self.assertEqual(result["matched_count"], 0)
        self.assertIn("没有匹配商品", result["content"])

    async def test_cleaned_menu_pagination_has_no_gap_or_overlap(self):
        with (
            patch("tools.chuangka_menu.PAGE_LIMIT", 2),
            patch("tools.chuangka_menu.datetime", _FixedDatetime),
            patch(
                "tools.chuangka_menu._download_public",
                side_effect=self._downloader(),
            ),
        ):
            full = await get_chuangka_menu(location="zhutu", max_length=12_000)
            first = await get_chuangka_menu(location="zhutu", max_length=60)
            second = await get_chuangka_menu(
                location="zhutu",
                max_length=12_000,
                start_index=first["next_start_index"],
            )

        self.assertTrue(first["truncated"])
        self.assertEqual(first["content"] + second["content"], full["content"])
        self.assertFalse(second["truncated"])

    async def test_one_location_failure_returns_partial_menu_with_warning(self):
        with (
            patch("tools.chuangka_menu.PAGE_LIMIT", 2),
            patch(
                "tools.chuangka_menu._download_public",
                side_effect=self._downloader(fail_huanyuan=True),
            ),
        ):
            result = await get_chuangka_menu(max_length=12_000)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["failed_locations"], ["huanyuan"])
        self.assertEqual(result["total_products"], 3)
        self.assertTrue(any("抓取失败" in warning for warning in result["warnings"]))

    async def test_invalid_category_is_a_structured_error(self):
        result = await get_chuangka_menu(category="coffee")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
