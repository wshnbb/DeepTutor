"""Local stdio MCP tools. No listener, scheduler, login, downloads, or purchases."""
from __future__ import annotations

from pathlib import Path
import sys

# DeepTutor ships Python's isolated embeddable runtime (python312._pth).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from lesson_tools import LessonError, find_lesson, read_lesson

mcp = FastMCP("bilibili-lessons", instructions="只在用户本轮明确要求找课、找B站视频或推荐视频时找课；普通知识问答不得主动找课。")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
async def find_bilibili_lesson(topic: str, user_request: str, import_to_library: bool = True, content_workspace: str = "") -> dict:
    """按用户要求找 B站课程并自动导入 DeepTutor，返回阅读链接和实际字幕。

    ONLY when the current user explicitly asks to find/recommend a lesson or
    Bilibili video. NEVER on ordinary explanations or merely mentioning a topic.
    user_request MUST quote the current user's actual request; do not invent it.
    Default: search, select ONE relevant video, import, check actual status,
    return bounded timestamped subtitles for tutoring. If user only wants links,
    use import_to_library=false. Supply content_workspace only when the current
    context explicitly identifies its dt_workspace ID; otherwise leave empty.
    Missing subtitles must be disclosed; chapter markers are not spoken content.
    """
    try:
        return await find_lesson(topic, user_request, import_to_library, content_workspace)
    except LessonError as exc:
        return {"ok": False, "error": str(exc), "imported": False}
    except Exception as exc:
        return {"ok": False, "error": f"找课或导入未完成（{type(exc).__name__}），不得编造课程或字幕。", "imported": False}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
async def read_bilibili_lesson(material_id: str, start_segment: int = 1, segment_count: int = 8, content_workspace: str = "") -> dict:
    """读取已导入 B站课程的真实字幕片段和时间戳，或检查导入状态。

    Does not search, import, download, or transcribe. Continue only with actual
    returned segments; report missing subtitles and never claim to see footage.
    Use next_segment for bounded continuation instead of loading a whole course.
    """
    try:
        return await read_lesson(material_id, start_segment, segment_count, content_workspace)
    except LessonError as exc:
        return {"ok": False, "error": str(exc), "transcript_available": False}
    except Exception as exc:
        return {"ok": False, "error": f"暂时无法读取课程（{type(exc).__name__}）。", "transcript_available": False}


if __name__ == "__main__":
    mcp.run(transport="stdio")
