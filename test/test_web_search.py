import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.mcp_server import create_mcp_server
from tools.web_search import (
    _SEARCH_CACHE,
    _CompatibleBing,
    _ddgs_search,
    image_search,
    web_search,
)


class SearchToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _SEARCH_CACHE.clear()

    async def test_text_search_is_structured_ranked_and_deduplicated(self) -> None:
        raw = [
            {"title": "Other", "href": "https://Example.com/a?utm_source=x", "body": "python body"},
            {"title": "Python guide", "href": "https://example.com/a#section", "body": "python"},
            {"title": "Python docs", "href": "https://docs.example.com/b", "body": "reference"},
        ]
        with patch("tools.web_search._ddgs_search", return_value=raw) as search:
            result = await web_search("python", max_results=2)

        self.assertEqual(result["category"], "text")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["results"][0]["title"], "Python guide")
        self.assertEqual(result["results"][0]["url"], "https://example.com/a")
        self.assertEqual(result["results"][1]["url"], "https://docs.example.com/b")
        self.assertEqual(search.call_count, 1)
        self.assertEqual(search.call_args.kwargs["max_results"], 6)
        self.assertEqual(search.call_args.kwargs["backend"], "bing")

    async def test_domain_filter_uses_exact_or_subdomain_matching(self) -> None:
        raw = [
            {"title": "Allowed", "href": "https://www.example.com/a", "body": "term"},
            {"title": "Fake", "href": "https://fakeexample.com/a", "body": "term"},
            {"title": "Other", "href": "https://other.test/a", "body": "term"},
        ]
        with patch("tools.web_search._ddgs_search", return_value=raw) as search:
            result = await web_search("term", include_domains=["example.com"])

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["url"], "https://www.example.com/a")
        self.assertIn("site:example.com", search.call_args.args[1])

    async def test_excluded_domains_are_removed(self) -> None:
        raw = [
            {"title": "Blocked", "href": "https://news.example.com/a", "body": "term"},
            {"title": "Allowed", "href": "https://allowed.example/a", "body": "term"},
        ]
        with patch("tools.web_search._ddgs_search", return_value=raw) as search:
            result = await web_search("term", exclude_domains=["example.com"])

        self.assertEqual([item["title"] for item in result["results"]], ["Allowed"])
        self.assertIn("-site:example.com", search.call_args.args[1])

    async def test_empty_primary_uses_one_fallback(self) -> None:
        fallback = [{"title": "Result", "href": "https://example.com/a", "body": "query"}]
        with patch("tools.web_search._ddgs_search", side_effect=[[], fallback]) as search:
            result = await web_search("query")

        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args_list[1].kwargs["backend"], "startpage")
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["warnings"])

    def test_bing_text_adapter_converts_results_without_ddgs_auto(self) -> None:
        engine = SimpleNamespace(
            search=lambda *args, **kwargs: [
                SimpleNamespace(
                    title="Bing result",
                    href="https://example.com/result",
                    body="Snippet",
                )
            ]
        )
        with patch("tools.web_search._CompatibleBing", return_value=engine) as bing:
            results = _ddgs_search(
                "text",
                "query",
                region="cn-zh",
                safesearch="moderate",
                timelimit=None,
                max_results=5,
                page=1,
                backend="bing",
                extra={},
            )

        bing.assert_called_once_with(proxy=None, timeout=5)
        self.assertEqual(
            results,
            [
                {
                    "title": "Bing result",
                    "href": "https://example.com/result",
                    "body": "Snippet",
                }
            ],
        )

    def test_bing_adapter_supports_both_title_link_layouts(self) -> None:
        engine = object.__new__(_CompatibleBing)
        html = """
        <ol>
          <li class="b_algo">
            <h2><a href="https://old.example/result">Old title</a></h2>
            <p>Old body</p>
          </li>
          <li class="b_algo">
            <div class="b_algoheader">
              <a href="https://new.example/result"><h2>New title</h2></a>
            </div>
            <p>New body</p>
          </li>
        </ol>
        """

        results = engine.extract_results(html)

        self.assertEqual(
            [(item.title, item.href, item.body) for item in results],
            [
                ("Old title", "https://old.example/result", "Old body"),
                ("New title", "https://new.example/result", "New body"),
            ],
        )

    async def test_each_attempt_uses_only_one_backend(self) -> None:
        with patch("tools.web_search._ddgs_search", return_value=[]) as search:
            await web_search("query")
            await web_search("query", category="news")
            await image_search("query")

        self.assertEqual(search.call_count, 6)
        for call in search.call_args_list:
            self.assertNotIn(",", call.kwargs["backend"])

    async def test_successful_search_is_cached(self) -> None:
        raw = [{"title": "Result", "href": "https://example.com/a", "body": "cached"}]
        with patch("tools.web_search._ddgs_search", return_value=raw) as search:
            first = await web_search("cached")
            second = await web_search("cached")

        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(search.call_count, 1)

    async def test_news_preview_is_opt_in(self) -> None:
        raw = [
            {
                "title": "News",
                "url": "https://example.com/news",
                "body": "latest",
                "date": "2026-08-25T00:00:00Z",
                "image": "https://example.com/news.jpg",
                "source": "Example",
            }
        ]
        with patch("tools.web_search._ddgs_search", return_value=raw):
            without_image = await web_search("latest", category="news")
            with_image = await web_search("latest", category="news", include_preview_images=True)

        self.assertIsNone(without_image["results"][0]["image_url"])
        self.assertEqual(with_image["results"][0]["image_url"], "https://example.com/news.jpg")

    async def test_image_search_forwards_filters_and_normalizes_fields(self) -> None:
        raw = [
            {
                "title": "Image",
                "url": "https://example.com/page",
                "image": "https://cdn.example.com/full.jpg",
                "thumbnail": "https://cdn.example.com/thumb.jpg",
                "width": "1200",
                "height": 800,
                "source": "Bing",
            }
        ]
        with patch("tools.web_search._ddgs_search", return_value=raw) as search:
            result = await image_search(
                "landscape",
                size="Large",
                color="Blue",
                layout="Wide",
            )

        item = result["results"][0]
        self.assertEqual(result["category"], "images")
        self.assertEqual(item["image_url"], "https://cdn.example.com/full.jpg")
        self.assertEqual(item["width"], 1200)
        self.assertEqual(search.call_args.kwargs["extra"], {"size": "Large", "color": "Blue", "layout": "Wide"})

    async def test_bounds_and_categories_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            await web_search("query", max_results=11)
        with self.assertRaises(ValueError):
            await web_search("query", page=4)
        with self.assertRaises(ValueError):
            await web_search("query", category="videos")  # type: ignore[arg-type]

    async def test_only_planned_search_tools_are_registered(self) -> None:
        server = create_mcp_server()
        tool_names = {tool.name for tool in await server.list_tools()}

        self.assertTrue(
            {"web_search", "image_search", "fetch_webpage_content"}.issubset(tool_names)
        )
        self.assertTrue(
            {"videos", "books", "extract", "extract_content"}.isdisjoint(tool_names)
        )


if __name__ == "__main__":
    unittest.main()
