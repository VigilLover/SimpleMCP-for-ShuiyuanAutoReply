import asyncio
import json
import socket
import unittest
from unittest.mock import patch

from tools.fetch_webpage import (
    CACHE_TTL_SECONDS,
    DEFAULT_MAX_DOWNLOAD_BYTES,
    DownloadedPage,
    FetchSafetyError,
    PublicResolver,
    _cache_get,
    _cache_put,
    _clear_fetch_cache,
    _download_public,
    _validate_url,
    fetch_webpage_content,
)


class _Chunks:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self.chunks:
            yield chunk


class _Response:
    def __init__(self, chunks=(), status=200, headers=None, charset="utf-8"):
        self.status = status
        self.headers = headers or {"Content-Type": "text/plain; charset=utf-8"}
        self.charset = charset
        self.content = _Chunks(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return next(self.responses)


class FetchSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _clear_fetch_cache()

    def test_rejects_non_http_credentials_ports_and_private_ips(self):
        for url in (
            "file:///etc/passwd",
            "https://user:pass@example.com/",
            "https://example.com:8443/",
            "http://127.0.0.1/",
            "http://10.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
        ):
            with self.subTest(url=url), self.assertRaises(FetchSafetyError):
                _validate_url(url)

    async def test_resolver_rejects_any_private_dns_answer(self):
        loop = asyncio.get_running_loop()
        rows = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with patch.object(loop, "getaddrinfo", return_value=rows):
            with self.assertRaises(FetchSafetyError):
                await PublicResolver().resolve("example.com", 443)

    async def test_streaming_limit_is_enforced_without_content_length(self):
        session = _Session(
            [_Response([b"x" * DEFAULT_MAX_DOWNLOAD_BYTES, b"overflow"])]
        )
        with (
            patch("tools.fetch_webpage.aiohttp.TCPConnector", return_value=object()),
            patch("tools.fetch_webpage.aiohttp.ClientSession", return_value=session),
        ):
            with self.assertRaises(FetchSafetyError):
                await _download_public("https://example.com/data")

    async def test_redirect_is_revalidated_and_missing_location_rejected(self):
        session = _Session([_Response(status=302, headers={})])
        with (
            patch("tools.fetch_webpage.aiohttp.TCPConnector", return_value=object()),
            patch("tools.fetch_webpage.aiohttp.ClientSession", return_value=session),
        ):
            with self.assertRaises(FetchSafetyError):
                await _download_public("https://example.com/start")

    async def test_successful_response_is_cached(self):
        session = _Session([_Response([b"abcdef"])])
        with (
            patch("tools.fetch_webpage.aiohttp.TCPConnector", return_value=object()),
            patch("tools.fetch_webpage.aiohttp.ClientSession", return_value=session),
        ):
            first = await _download_public("https://example.com/data")
            second = await _download_public("https://example.com/data")
        self.assertEqual(first.data, b"abcdef")
        self.assertIs(first, second)
        self.assertEqual(len(session.requests), 1)

    def test_expired_cache_entry_is_removed(self):
        page = DownloadedPage("https://example.com", "text/plain", "utf-8", b"x")
        with patch("tools.fetch_webpage.time.monotonic", return_value=0):
            _cache_put(page.url, page)
        with patch(
            "tools.fetch_webpage.time.monotonic", return_value=CACHE_TTL_SECONDS + 1
        ):
            self.assertIsNone(_cache_get(page.url))

    async def test_structured_slice_has_exact_continuation_metadata(self):
        page = DownloadedPage(
            "https://example.com", "text/plain", "utf-8", b"abcdef"
        )
        with patch("tools.fetch_webpage._download_public", return_value=page):
            first = await fetch_webpage_content(
                "https://example.com", max_length=3, start_index=2
            )
            second = await fetch_webpage_content(
                "https://example.com",
                max_length=3,
                start_index=first["next_start_index"],
            )
        self.assertEqual(first["content"], "cde")
        self.assertTrue(first["truncated"])
        self.assertEqual(first["next_start_index"], 5)
        self.assertEqual(second["content"], "f")
        self.assertFalse(second["truncated"])

    async def test_large_json_query_finds_late_item_and_projects_fields(self):
        rows = [
            {"name": f"普通商品{i}", "price": i, "imgUrls": ["x" * 2000]}
            for i in range(180)
        ]
        rows.append(
            {
                "name": "海盐焦糖冰淇淋",
                "price": 18,
                "sellingPoint": "今日口味",
                "imgUrls": ["y" * 2000],
            }
        )
        body = json.dumps({"code": 0, "dataList": rows}, ensure_ascii=False).encode()
        self.assertGreater(len(body), 300_000)
        page = DownloadedPage(
            "https://example.com/menu", "application/json", "utf-8", body
        )
        with patch("tools.fetch_webpage._download_public", return_value=page):
            result = await fetch_webpage_content(
                page.url,
                query="冰淇淋",
                json_path="dataList",
                fields=["name", "price", "sellingPoint"],
            )
        payload = json.loads(result["content"])
        self.assertEqual(result["mode"], "json")
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(
            payload["items"],
            [{"name": "海盐焦糖冰淇淋", "price": 18, "sellingPoint": "今日口味"}],
        )
        self.assertNotIn("imgUrls", result["content"])

    async def test_auto_json_selects_largest_top_level_array(self):
        page = DownloadedPage(
            "https://example.com/data",
            "application/json",
            "utf-8",
            json.dumps({"small": [1], "items": [{"name": "A"}, {"name": "B"}]}).encode(),
        )
        with patch("tools.fetch_webpage._download_public", return_value=page):
            result = await fetch_webpage_content(page.url)
        payload = json.loads(result["content"])
        self.assertEqual(payload["path"], "items")
        self.assertEqual(payload["total_items"], 2)

    async def test_html_prefers_main_and_removes_navigation_noise(self):
        html = (
            "<html><body><nav>" + "噪声" * 20_000 + "</nav>"
            "<main><h1>菜单</h1><p>香草冰淇淋</p><p>热拿铁</p>"
            "<p hidden>隐藏属性噪声</p>"
            "<p aria-hidden='true'>ARIA 噪声</p>"
            "<p style='display: none'>样式噪声</p></main>"
            "<footer>页脚噪声</footer></body></html>"
        )
        page = DownloadedPage(
            "https://example.com/menu", "text/html; charset=utf-8", "utf-8", html.encode()
        )
        with patch("tools.fetch_webpage._download_public", return_value=page):
            result = await fetch_webpage_content(page.url, query="冰淇淋")
        self.assertIn("香草冰淇淋", result["content"])
        self.assertIn("菜单", result["content"])
        self.assertIn("热拿铁", result["content"])
        self.assertNotIn("噪声", result["content"])

    async def test_invalid_json_path_is_structured_error(self):
        page = DownloadedPage(
            "https://example.com/data", "application/json", "utf-8", b'{"items":[]}'
        )
        with patch("tools.fetch_webpage._download_public", return_value=page):
            result = await fetch_webpage_content(page.url, json_path="missing")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
