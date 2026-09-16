import asyncio
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

    async def test_legacy_slice_contract_remains_available(self):
        page = DownloadedPage(
            "https://example.com", "text/plain", "utf-8", b"abcdef"
        )
        with patch("tools.fetch_webpage._download_public", return_value=page):
            result = await fetch_webpage_content(
                "https://example.com", max_length=3, start_index=2
            )
        self.assertEqual(result, "cde")


if __name__ == "__main__":
    unittest.main()
