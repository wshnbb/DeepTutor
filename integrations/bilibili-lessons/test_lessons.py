"""Focused checks for opt-in behavior, URL validation, reuse, and evidence limits."""
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx

from lesson_tools import Desktop, LessonClient, LessonError, canonical_video, find_lesson, select_candidates

URL = "https://www.bilibili.com/video/BV17j411u7YR/"


class LessonTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_search_uses_existing_provider_without_personal_endpoint(self):
        queries = []

        def web_search(query, verbose=False):
            queries.append(query)
            return {"search_results": [{"title": "转差率教程", "url": URL, "snippet": "公开课程"}]}

        fake_search = types.ModuleType("deeptutor.services.search")
        fake_search.web_search = web_search
        with patch.dict(sys.modules, {"deeptutor.services.search": fake_search}), patch("lesson_tools.SEARCH_ORIGIN", ""):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: self.fail("No adapter request expected"))) as http:
                rows = await LessonClient(Desktop("http://127.0.0.1:8001", "http://127.0.0.1:3782"), http).search("转差率")
        self.assertEqual(rows[0]["url"], URL)
        self.assertEqual(len(queries), 1)
        self.assertIn("site:bilibili.com/video/", queries[0])

    async def test_ordinary_or_negative_questions_never_discover_or_search(self):
        with patch("lesson_tools.Desktop.discover") as discover:
            for request in ("解释一下转差率", "不用找视频，直接讲解转差率", "不要自动帮我找课程"):
                with self.assertRaises(LessonError):
                    await find_lesson("转差率", request)
            discover.assert_not_called()

    def test_canonical_urls_reject_other_hosts_and_deduplicate_tracking(self):
        for value in ("https://www.bilibili.com.evil.test/video/BV17j411u7YR/", "http://127.0.0.1/video/BV17j411u7YR/", "https://user@www.bilibili.com/video/BV17j411u7YR/", URL + "?p=-1"):
            self.assertIsNone(canonical_video(value))
        rows = select_candidates([
            {"url": URL + "?utm_source=openai", "title": "转差率教程"},
            {"url": URL, "title": "bilibili.com"},
        ], "转差率")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], URL)
        self.assertEqual(rows[0]["title"], "转差率教程")

    async def test_repeated_import_reuses_ready_material_without_writes(self):
        calls = []

        def transport(request):
            calls.append((request.method, request.url.path))
            return httpx.Response(200, json={"matches": [{"material": {"material_id": "m-test", "source_url": URL, "status": "ready"}, "collections": [{"workspace_id": "rw-existing"}]}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            result = await LessonClient(Desktop("http://127.0.0.1:8001", "http://127.0.0.1:3782"), http).import_candidate("转差率", {"url": URL, "title": "转差率"})
        self.assertTrue(result["reused"])
        self.assertEqual(calls, [("POST", "/api/reading/library/duplicate-check")])

    async def test_chapter_markers_are_never_reported_as_spoken_subtitles(self):
        def transport(request):
            if request.url.path.endswith("/library/materials"):
                data = {"materials": [{"material_id": "m-test", "status": "ready", "source_kind": "bilibili", "source_url": URL}]}
            elif request.url.path.endswith("/transcript"):
                data = {"unit_count": 1, "segments": [{"locator": 1, "text": "Chapter marker: 转差率. Spoken transcript unavailable.", "source_href": "#t=10"}]}
            else:
                data = {"title": "转差率", "extractor": "bilibili-chapters-only"}
            return httpx.Response(200, json=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            result = await LessonClient(Desktop("http://127.0.0.1:8001", "http://127.0.0.1:3782"), http).read_lesson("m-test")
        self.assertFalse(result["transcript_available"])
        self.assertEqual(result["evidence_kind"], "chapter_titles_only")


if __name__ == "__main__":
    unittest.main()
