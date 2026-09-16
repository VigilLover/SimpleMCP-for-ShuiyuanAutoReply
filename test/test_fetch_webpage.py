import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from tools.fetch_webpage import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    FETCH_MCP_SDK_VERSION,
    FETCH_SERVER_VERSION,
    FetchSafetyError,
    _preflight_fetch,
    _validate_public_url,
    fetch_webpage_content,
)


class FetchSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_non_http_credentials_and_nonstandard_ports(self) -> None:
        with self.assertRaises(FetchSafetyError):
            await _validate_public_url("file:///etc/passwd")
        with self.assertRaises(FetchSafetyError):
            await _validate_public_url("https://user:pass@example.com/")
        with self.assertRaises(FetchSafetyError):
            await _validate_public_url("https://example.com:8443/")

    async def test_rejects_non_global_ipv4_and_ipv6(self) -> None:
        blocked_urls = [
            "http://127.0.0.1/",
            "http://10.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://[fc00::1]/",
        ]
        for url in blocked_urls:
            with self.subTest(url=url), self.assertRaises(FetchSafetyError):
                await _validate_public_url(url)

    async def test_accepts_public_host_after_dns_check(self) -> None:
        with patch("tools.fetch_webpage._assert_public_hostname", new=AsyncMock()) as check:
            await _validate_public_url("https://example.com/path")
        check.assert_awaited_once_with("example.com")

    async def test_preflight_rejects_declared_oversized_response(self) -> None:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.head.return_value = httpx.Response(
            200,
            headers={"content-length": str(DEFAULT_MAX_DOWNLOAD_BYTES + 1)},
        )
        with (
            patch("tools.fetch_webpage.httpx.AsyncClient", return_value=client),
            patch("tools.fetch_webpage._validate_public_url", new=AsyncMock()),
        ):
            with self.assertRaises(FetchSafetyError):
                await _preflight_fetch("https://example.com", DEFAULT_MAX_DOWNLOAD_BYTES)

    async def test_preflight_validates_each_redirect(self) -> None:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.head.return_value = httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private"},
        )
        validate = AsyncMock(side_effect=[None, FetchSafetyError("private")])
        with (
            patch("tools.fetch_webpage.httpx.AsyncClient", return_value=client),
            patch("tools.fetch_webpage._validate_public_url", new=validate),
        ):
            with self.assertRaises(FetchSafetyError):
                await _preflight_fetch("https://example.com", DEFAULT_MAX_DOWNLOAD_BYTES)
        self.assertEqual(validate.await_count, 2)

    async def test_missing_content_length_is_allowed_and_logged(self) -> None:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.head.return_value = httpx.Response(200)
        with (
            patch("tools.fetch_webpage.httpx.AsyncClient", return_value=client),
            patch("tools.fetch_webpage._validate_public_url", new=AsyncMock()),
            self.assertLogs("mcp_tools.fetch", level="WARNING") as logs,
        ):
            await _preflight_fetch("https://example.com", DEFAULT_MAX_DOWNLOAD_BYTES)
        self.assertIn("size is unverified", " ".join(logs.output))

    async def test_unsupported_head_is_allowed_and_logged(self) -> None:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.head.return_value = httpx.Response(
            405,
            headers={"content-length": str(DEFAULT_MAX_DOWNLOAD_BYTES + 1)},
        )
        with (
            patch("tools.fetch_webpage.httpx.AsyncClient", return_value=client),
            patch("tools.fetch_webpage._validate_public_url", new=AsyncMock()),
            self.assertLogs("mcp_tools.fetch", level="WARNING") as logs,
        ):
            await _preflight_fetch("https://example.com", DEFAULT_MAX_DOWNLOAD_BYTES)
        self.assertIn("HEAD is unsupported", " ".join(logs.output))

    async def test_output_bounds_are_checked_before_preflight(self) -> None:
        with patch("tools.fetch_webpage._preflight_fetch", new=AsyncMock()) as preflight:
            result = await fetch_webpage_content("https://example.com", max_length=1_000_000)
        self.assertIn("max_length", result)
        preflight.assert_not_awaited()

    def test_reference_server_version_is_pinned(self) -> None:
        self.assertEqual(FETCH_SERVER_VERSION, "2026.7.10")
        self.assertEqual(FETCH_MCP_SDK_VERSION, "1.27.0")

    async def test_fetch_arguments_and_text_output_remain_compatible(self) -> None:
        class AsyncContext:
            def __init__(self, value: object) -> None:
                self.value = value

            async def __aenter__(self) -> object:
                return self.value

            async def __aexit__(self, *args: object) -> None:
                return None

        session = SimpleNamespace(
            initialize=AsyncMock(),
            call_tool=AsyncMock(
                return_value=SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="markdown body")]
                )
            ),
        )
        stdio = patch(
            "tools.fetch_webpage.stdio_client",
            return_value=AsyncContext((object(), object())),
        )
        with (
            patch("tools.fetch_webpage._preflight_fetch", new=AsyncMock()),
            stdio as stdio_client,
            patch(
                "tools.fetch_webpage.ClientSession",
                return_value=AsyncContext(session),
            ),
        ):
            result = await fetch_webpage_content(
                "https://example.com", max_length=1234, start_index=99, raw=True
            )

        self.assertEqual(result, "markdown body")
        server_params = stdio_client.call_args.args[0]
        self.assertEqual(
            server_params.args[:3],
            ["--with", "mcp==1.27.0", "mcp-server-fetch==2026.7.10"],
        )
        session.call_tool.assert_awaited_once_with(
            "fetch",
            arguments={
                "url": "https://example.com",
                "max_length": 1234,
                "start_index": 99,
                "raw": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
