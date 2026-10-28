"""On-demand Bilibili lessons for the existing single-user DeepTutor desktop."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import html
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

STATE = Path(os.environ.get("DEEPTUTOR_DESKTOP_STATE_DIR", str(Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "io.github.wshnbb.deeptutor")))
CONTROL = STATE / "control"
# Optional adapter origin. By default reuse this installation's search service
# and saved provider selection without serializing any credentials here.
SEARCH_ORIGIN = os.environ.get("DEEPTUTOR_LESSON_SEARCH_ORIGIN", "")
ALLOWED_ACCOUNT = "local-admin"
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
NEGATED_SEARCH = re.compile(r"(?:不需要|不用|不要|别|停止)(?:再|自动|主动|继续|帮我|给我|去|联网|上网|你){0,4}(?:找|搜|推荐)")
REQUEST_SEARCH = re.compile(r"找|搜|推荐|find\b|search\b|recommend\b", re.I)
LESSON_TARGET = re.compile(r"视频|课程|教程|网课|讲课|讲解|课|b站|哔哩|bilibili|video|lesson|course|tutorial", re.I)
SEARCH_ONLY = re.compile(r"只(?:要|需)?(?:找|搜|推荐|看|列|给|发|要)|不(?:要|用|必|需要)(?:自动)?(?:导入|添加|保存)|别(?:导入|添加|保存)")


class LessonError(RuntimeError):
    pass


def require_search_request(user_request: str) -> str:
    request = str(user_request).strip()
    if not 2 <= len(request) <= 1200:
        raise LessonError("请传入用户本轮明确要求找课的原话。")
    if NEGATED_SEARCH.search(request) or re.search(r"\b(?:do not|don't|no need to)\s+(?:search|find|recommend)", request, re.I):
        raise LessonError("用户没有授权本轮找课，或明确说了不要找；不执行搜索或导入。")
    if not (REQUEST_SEARCH.search(request) and LESSON_TARGET.search(request)):
        raise LessonError("只有用户明确要求找视频或课程时才调用；普通知识问答不找课。")
    return request


def canonical_video(value: str) -> str | None:
    try:
        parsed = urlsplit(str(value).strip())
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        if parsed.hostname not in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}:
            return None
        if parsed.port not in {None, 80, 443}:
            return None
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path)
        if not match:
            return None
        page = parse_qs(parsed.query).get("p", ["1"])[0]
        page_number = int(page)
        if not 1 <= page_number <= 5000:
            return None
        return "https://www.bilibili.com/video/" + match.group(1) + "/" + (f"?p={page_number}" if page_number > 1 else "")
    except (ValueError, TypeError):
        return None


def clean_text(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", str(value or "")))).strip()[:limit]


def select_candidates(rows: list[dict], topic: str, limit: int = 3) -> list[dict]:
    # Search rank is the baseline. Never treat views, a snippet, or its rank as
    # proof that the instructor is good or the lesson has been watched.
    chosen: dict[str, dict] = {}
    terms = [s for s in re.split(r"\s+", topic) if s]
    for rank, row in enumerate(rows[:30]):
        url = canonical_video(row.get("url", ""))
        if not url:
            continue
        title = clean_text(row.get("title"), 250)
        snippet = clean_text(row.get("content") or row.get("description") or row.get("snippet"), 700)
        score = 100 - rank * 2
        score += sum(5 for term in terms if term.lower() in title.lower())
        score += 4 if any(term in title for term in ("教程", "课程", "教学", "原理", "讲解")) else 0
        if title.lower() in {"bilibili.com", "哔哩哔哩", "bilibili"}:
            score -= 30
        candidate = {"title": title or "B站视频", "url": url, "snippet": snippet,
                     "selection_basis": "按搜索相关度和标题关键词排序，未根据播放量认定教学质量。",
                     "_score": score}
        if url not in chosen or chosen[url]["_score"] < score:
            chosen[url] = candidate
    ordered = sorted(chosen.values(), key=lambda row: row["_score"], reverse=True)[:limit]
    return [{k: v for k, v in row.items() if k != "_score"} for row in ordered]


def loopback_origin(value: str) -> str:
    p = urlsplit(value)
    if p.scheme != "http" or p.hostname != "127.0.0.1" or p.username or p.password or not p.port or p.path not in {"", "/"} or p.query or p.fragment:
        raise LessonError("DeepTutor 服务地址不是预期的本机地址，已停止。")
    return f"http://127.0.0.1:{p.port}"


@dataclass
class Desktop:
    backend: str
    frontend: str
    content_workspace: str = ""

    @classmethod
    def discover(cls, content_workspace: str = "") -> "Desktop":
        if content_workspace and not SAFE_ID.fullmatch(content_workspace):
            raise LessonError("学习空间 ID 无效。")
        entries = sorted(CONTROL.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for entry in entries:
            try:
                row = json.loads(entry.read_text(encoding="utf-8"))
                if row.get("instance") == entry.stem:
                    return cls(loopback_origin(row["backend_url"]), loopback_origin(row["frontend_url"]), content_workspace)
            except (OSError, ValueError, KeyError):
                continue
        raise LessonError("找不到正在运行的 DeepTutor 桌面服务，请先启动 DeepTutor。")

    def api(self, path: str) -> str:
        return self.backend + path + ("?" + urlencode({"dt_workspace": self.content_workspace}) if self.content_workspace else "")

    def reading_url(self, workspace_id: str) -> str:
        path = self.frontend + "/learning/reading/" + workspace_id
        return path + ("?" + urlencode({"dt_workspace": self.content_workspace}) if self.content_workspace else "")


class LessonClient:
    def __init__(self, desktop: Desktop, client: httpx.AsyncClient):
        self.desktop = desktop
        self.client = client

    async def request(self, method: str, path: str, **kwargs) -> dict:
        response = await self.client.request(method, self.desktop.api(path), timeout=25, **kwargs)
        response.raise_for_status()
        return response.json()

    async def guard_account(self) -> None:
        # A stdio extension has no HTTP user's cookie. It must never silently
        # become a bridge into the administrator account of a multi-user app.
        response = await self.client.get(self.desktop.backend + "/api/auth/status", timeout=10)
        response.raise_for_status()
        auth = response.json()
        if auth.get("enabled") is not False or auth.get("user_id") != ALLOWED_ACCOUNT or not auth.get("authenticated"):
            raise LessonError("当前扩展只支持现有本机单用户账户；登录模式或账户发生变化，已停止。")

    async def search(self, topic: str) -> list[dict]:
        query = "site:bilibili.com/video/ " + topic + " 教学 课程"
        if SEARCH_ORIGIN:
            response = await self.client.get(loopback_origin(SEARCH_ORIGIN) + "/search", params={"q": query}, timeout=185)
            response.raise_for_status()
            rows = response.json().get("results", [])
        else:
            from deeptutor.services.search import web_search

            result = await asyncio.wait_for(asyncio.to_thread(web_search, query, verbose=False), timeout=185)
            if result.get("error_code"):
                raise LessonError("现有搜索服务尚未就绪，请先在 DeepTutor 中配置搜索。")
            rows = list(result.get("search_results") or []) + list(result.get("citations") or [])
        candidates = select_candidates(rows if isinstance(rows, list) else [], topic)
        if not candidates:
            raise LessonError("搜索服务没有返回可核实的 B站 BV 视频链接；没有编造或导入任何课程。")
        return candidates

    async def prefer_subtitles(self, candidates: list[dict]) -> list[dict]:
        # Bilibili's public search endpoint can reject requests; discovery uses
        # the user's existing search provider. Check only the three found BV
        # links with the same metadata loader as the installed import feature.
        from deeptutor.reading.ingestion import _load_bilibili_media

        async def inspect(candidate: dict) -> dict:
            result = dict(candidate)
            try:
                media = await asyncio.wait_for(
                    _load_bilibili_media(candidate["url"], ["zh-CN", "zh", "en"]), timeout=22
                )
                result.update(title=media.title, duration_seconds=media.duration_seconds,
                              subtitle_precheck=bool(media.segments), metadata_verified=True)
            except Exception:
                result.update(subtitle_precheck=False, metadata_verified=False)
            return result

        inspected = await asyncio.gather(*(inspect(row) for row in candidates))
        return sorted(inspected, key=lambda row: (not row["subtitle_precheck"], not row["metadata_verified"]))

    async def import_candidate(self, topic: str, candidate: dict) -> dict:
        url = canonical_video(candidate["url"])
        if not url:
            raise LessonError("只允许导入真实 B站 BV 视频网址。")
        duplicates = await self.request("POST", "/api/reading/library/duplicate-check", json={"urls": [url]})
        for match in duplicates.get("matches", []):
            material = match.get("material", {})
            if material.get("source_url") and canonical_video(material["source_url"]) == url:
                collections = match.get("collections", [])
                if collections:
                    return {"material_id": material["material_id"], "workspace_id": collections[0]["workspace_id"],
                            "reused": True, "status": material.get("status", "queued"), "title": material.get("title", candidate["title"]),
                            "error": clean_text(material.get("error_detail") or material.get("error_code"), 250), "url": url}
                created = await self.request("POST", "/api/reading/workspaces", json={"title": "B站伴学 · " + topic[:70], "material_ids": [material["material_id"]]})
                return {"material_id": material["material_id"], "workspace_id": created["workspace"]["workspace_id"],
                        "reused": True, "status": material.get("status", "queued"), "title": material.get("title", candidate["title"]),
                        "error": clean_text(material.get("error_detail") or material.get("error_code"), 250), "url": url}
        result = await self.request("POST", "/api/reading/library/import-urls", json={"urls": [url], "workspace_title": "B站伴学 · " + topic[:70]})
        materials = result.get("materials", [])
        workspace = result.get("workspace") or {}
        if not materials or not workspace.get("workspace_id"):
            raise LessonError("DeepTutor 没有返回导入结果。")
        material = materials[0]
        mid = material["material_id"]
        status = material.get("status", "queued")
        error = ""
        # Queueing is not success. Report the actual persisted terminal state,
        # or leave it visibly processing and provide a continuation tool.
        deadline = time.monotonic() + 30
        while status in {"queued", "processing"} and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            listing = await self.request("GET", "/api/reading/library/materials")
            found = next((m for m in listing.get("materials", []) if m.get("material_id") == mid), None)
            if found:
                material = found
                status = found.get("status", status)
                error = clean_text(found.get("error_detail") or found.get("error_code"), 250)
        return {"material_id": mid, "workspace_id": workspace["workspace_id"], "reused": False,
                "status": status, "error": error, "title": material.get("title", candidate["title"]), "url": url}

    async def read_lesson(self, material_id: str, start_segment: int = 1, segment_count: int = 8) -> dict:
        if not SAFE_ID.fullmatch(material_id):
            raise LessonError("资料 ID 无效。")
        listing = await self.request("GET", "/api/reading/library/materials")
        record = next((m for m in listing.get("materials", []) if m.get("material_id") == material_id), None)
        if not record or record.get("source_kind") != "bilibili" or not canonical_video(record.get("source_url", "")):
            raise LessonError("该资料不是当前学习空间中的 B站课程。")
        status = record.get("status")
        if status != "ready":
            return {"material_id": material_id, "status": status, "transcript_available": False,
                    "error": clean_text(record.get("error_detail") or record.get("error_code"), 250), "instruction": "尚未取得可用字幕，不得声称已看完或已读懂该视频。"}
        detail = await self.request("GET", f"/api/reading/materials/{material_id}")
        extractor = detail.get("extractor", "")
        available = extractor == "bilibili-subtitles"
        start = max(1, int(start_segment))
        count = min(12, max(1, int(segment_count)))
        transcript = await self.request("GET", f"/api/reading/materials/{material_id}/transcript")
        rows = transcript.get("segments", [])
        selected = [r for r in rows if start <= int(r.get("locator", 0)) < start + count]
        segments = [{"segment": r.get("locator"), "timestamp": r.get("source_href", ""),
                     "text": str(r.get("text", ""))[:3500]} for r in selected]
        next_segment = start + count if any(int(r.get("locator", 0)) >= start + count for r in rows) else None
        return {"material_id": material_id, "status": "ready", "title": detail.get("title", record.get("title", "")),
                "source_url": record["source_url"], "extractor": extractor,
                "transcript_available": available, "evidence_kind": "spoken_subtitles" if available else "chapter_titles_only" if extractor == "bilibili-chapters-only" else "no_transcript",
                "total_segments": transcript.get("unit_count", len(rows)), "segments": segments,
                "next_segment": next_segment, "source_truncated": transcript.get("truncated", False),
                "instruction": "仅根据已返回的字幕片段讲解，引用时间戳；视频画面未读取。" if available else "没有取得口述字幕。章节标题不是讲课正文；可按用户要求用通用知识讲解，但必须与视频实际内容区分。"}


async def find_lesson(topic: str, user_request: str, import_to_library: bool = True, content_workspace: str = "") -> dict:
    request = require_search_request(user_request)
    topic = clean_text(topic, 160)
    if not 2 <= len(topic) <= 160 or "http" in topic.lower():
        raise LessonError("请提供具体知识点或课程主题，不要把网址当成搜索主题。")
    desktop = Desktop.discover(content_workspace)
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as http:
        client = LessonClient(desktop, http)
        await client.guard_account()
        candidates = await client.search(topic)
        candidates = await client.prefer_subtitles(candidates)
        result: dict[str, Any] = {"topic": topic, "candidates": candidates, "selected": candidates[0],
                                  "imported": False, "search_service": "现有搜索服务", "trigger": "用户本轮明确要求找课"}
        if not import_to_library or SEARCH_ONLY.search(request):
            result["instruction"] = "本轮只找课，不导入。展示真实链接和选择依据，不声称看过视频。"
            return result
        imported = await client.import_candidate(topic, candidates[0])
        result["import"] = imported
        result["imported"] = imported["status"] == "ready"
        result["reading_url"] = desktop.reading_url(imported["workspace_id"])
        if imported["status"] == "ready":
            try:
                result["lesson"] = await client.read_lesson(imported["material_id"])
            except (httpx.HTTPError, LessonError):
                result["lesson"] = {"transcript_available": False, "instruction": "视频已导入，但本次读取字幕失败。不要把导入成功当成读懂了视频，可稍后用 read_bilibili_lesson 重试。"}
        else:
            result["instruction"] = "尚未完成导入。必须说明实际状态；处理中的资料稍后用 read_bilibili_lesson 查看，失败时给出错误。"
        return result


async def read_lesson(material_id: str, start_segment: int = 1, segment_count: int = 8, content_workspace: str = "") -> dict:
    desktop = Desktop.discover(content_workspace)
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as http:
        client = LessonClient(desktop, http)
        await client.guard_account()
        return await client.read_lesson(material_id, start_segment, segment_count)
