"""Stable JSON CLI for 轻舟下载器.

The CLI reuses the application's Electron browser profile and command/status
files.  It never exports browser cookies and does not depend on GUI coordinates.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from media_core import (
    APP_NAME,
    APP_VERSION,
    BROWSER_PROFILE_DIR,
    CONFIG_FILE,
    DATA_DIR,
    PLATFORMS,
    STATE_DIR,
    append_history,
    clear_history,
    detect_platform,
    extract_url,
    find_firefox_executable,
    list_history,
    load_config,
    platform_payload,
    public_config,
    resolve_output_dir,
    save_config,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
VENDOR_DIR = APP_DIR / ".vendor"
if VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

STATE_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_STATE_FILE = STATE_DIR / "browser_page.json"
BROWSER_COMMAND_FILE = STATE_DIR / "browser_command.json"
BROWSER_SIZE_FILE = STATE_DIR / "browser_size.json"
BROWSER_STATUS_FILE = STATE_DIR / "browser_status.json"
DOWNLOAD_STATUS_FILE = STATE_DIR / "browser_download.json"
DIRECT_DOWNLOAD_STATUS_FILE = STATE_DIR / "direct_download.json"
ELECTRON_EXE = APP_DIR / "electron_runtime" / "electron.exe"
ELECTRON_APP = APP_DIR / "electron_browser"

TERMINAL_DOWNLOAD_STATES = {"completed", "error", "cancelled", "interrupted"}
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
XIAOHONGSHU_HOSTS = {
    "xiaohongshu.com",
    "www.xiaohongshu.com",
    "xhslink.com",
    "www.xhslink.com",
}


class CliError(RuntimeError):
    def __init__(self, message: str, *, code: str = "CLI_ERROR", details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


class QuietYtdlpLogger:
    """Keep stdout/stderr JSON-only; errors are returned through CliError."""

    def debug(self, _message: str) -> None:
        pass

    def warning(self, _message: str) -> None:
        pass

    def error(self, _message: str) -> None:
        pass


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def normalize_media_url(value: str) -> str:
    """Extract and normalize a media URL from a URL or copied share text."""
    raw_url = value.strip()
    match = re.search(r"https?://[^\s]+", raw_url, flags=re.IGNORECASE)
    if match:
        raw_url = match.group(0).rstrip(",.;，。；!！？?)]}）】》")
    try:
        parsed = urlsplit(raw_url)
        host = (parsed.hostname or "").casefold()
        if host in {"douyin.com", "www.douyin.com"}:
            modal_id = (parse_qs(parsed.query).get("modal_id") or [""])[0]
            if modal_id.isdigit():
                # Some Douyin works expose a player only inside the search
                # result modal. Rewriting those URLs to /video/<id> can leave
                # us with a valid title but no <video> element. Preserve the
                # search context while douyin_video_id()/page_matches() still
                # use modal_id as the stable identity.
                if parsed.path.startswith("/search/"):
                    return raw_url
                return f"https://www.douyin.com/video/{modal_id}"
    except (TypeError, ValueError):
        pass
    return raw_url


def resolve_douyin_share_url(value: str, timeout: float = 15) -> str:
    """Resolve a v.douyin.com share URL to a stable /video/<id> URL.

    Douyin can send embedded browsers from the share redirect to /jingxuan,
    while the same HTTP redirect chain still exposes the canonical video id.
    Resolve only the official short-link host and only accept an official
    Douyin video destination.
    """
    normalized = normalize_media_url(value)
    try:
        parsed = urlsplit(normalized)
    except (TypeError, ValueError):
        return normalized
    if (parsed.hostname or "").casefold() not in {"v.douyin.com"}:
        return normalized
    request = Request(
        normalized,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=max(1.0, float(timeout))) as response:
            resolved = str(response.geturl() or "")
    except Exception:
        return normalized
    video_id = douyin_video_id(resolved)
    if not video_id:
        return normalized
    return f"https://www.douyin.com/video/{video_id}"


def is_youtube_url(value: str) -> bool:
    """Return whether *value* is a supported single-video YouTube URL."""
    try:
        normalized = normalize_media_url(value)
        parsed = urlsplit(normalized)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS:
            return False
        if host in {"youtu.be", "www.youtu.be"}:
            return bool(parsed.path.strip("/").split("/", 1)[0])
        path = parsed.path.rstrip("/")
        if path == "/watch":
            return bool((parse_qs(parsed.query).get("v") or [""])[0])
        return any(
            path.startswith(prefix)
            for prefix in ("/shorts/", "/live/", "/embed/", "/v/", "/clip/")
        )
    except (TypeError, ValueError):
        return False


def youtube_format_selectors(
    quality: str = "best",
    format_selector: str | None = None,
) -> tuple[str, ...]:
    """Build ordered yt-dlp format fallbacks for a YouTube MP4 download."""
    if format_selector:
        return (format_selector,)
    if quality == "best":
        return (
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
            "bv*+ba/b",
            "best[ext=mp4]/best",
        )
    try:
        exact_height = int(quality)
    except (TypeError, ValueError) as exc:
        raise CliError(
            f"不支持的清晰度：{quality}",
            code="INVALID_QUALITY",
            details={"quality": quality},
        ) from exc
    return (
        (
            f"bv*[height={exact_height}][ext=mp4]+ba[ext=m4a]/"
            f"b[height={exact_height}][ext=mp4]"
        ),
        f"bv*[height={exact_height}]+ba/b[height={exact_height}]",
        f"best[height={exact_height}]",
    )


def ytdlp_cli_error(exc: Exception, default_code: str) -> CliError:
    """Translate common yt-dlp failures into stable codes for AI callers."""
    detail = str(exc)
    lowered = detail.casefold()
    if (
        "sign in to confirm you" in lowered
        or "not a bot" in lowered
        or "login required" in lowered
    ):
        return CliError(
            (
                "YouTube 要求登录或人机验证。请让用户在真正的系统浏览器中登录，"
                "完全关闭浏览器后，明确允许时使用 --browser-cookies firefox"
                "（Windows 推荐），也可选择 edge/chrome 或指定自己的 "
                "Netscape cookies.txt。内嵌浏览器不能用于 Google 登录。"
            ),
            code="YOUTUBE_AUTH_REQUIRED",
            details={"provider_message": detail},
        )
    if "permission denied" in lowered or "could not copy" in lowered:
        return CliError(
            "系统浏览器的 Cookie 文件仍被占用。请完全关闭浏览器后重试。",
            code="BROWSER_COOKIE_LOCKED",
            details={"provider_message": detail},
        )
    if (
        "failed to decrypt with dpapi" in lowered
        or "could not decrypt" in lowered
        or "v20" in lowered
    ):
        return CliError(
            (
                "Chrome/Edge Cookie 在当前 Windows 环境中无法解密。"
                "请改用 --browser-cookies firefox，或由用户导出 cookies.txt。"
            ),
            code="BROWSER_COOKIE_DECRYPT_FAILED",
            details={"provider_message": detail},
        )
    if "video unavailable" in lowered:
        return CliError(
            "YouTube 视频不可用、已删除或当前地区无法访问。",
            code="YOUTUBE_VIDEO_UNAVAILABLE",
            details={"provider_message": detail},
        )
    if "requested format is not available" in lowered:
        return CliError(
            "请求的 YouTube 清晰度或格式不可用，请改用 --quality best。",
            code="FORMAT_UNAVAILABLE",
            details={"provider_message": detail},
        )
    return CliError(detail, code=default_code)


def douyin_video_id(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if not (parsed.hostname or "").casefold().endswith("douyin.com"):
            return ""
        modal_id = (parse_qs(parsed.query).get("modal_id") or [""])[0]
        if modal_id.isdigit():
            return modal_id
        match = re.search(r"/video/(\d{10,30})", parsed.path)
        return match.group(1) if match else ""
    except (TypeError, ValueError):
        return ""


def page_matches(expected_url: str, actual_url: str) -> bool:
    expected_id = douyin_video_id(expected_url)
    actual_id = douyin_video_id(actual_url)
    if expected_id:
        return expected_id == actual_id
    try:
        expected = urlsplit(expected_url)
        actual = urlsplit(actual_url)
        return (
            (expected.hostname or "").casefold() == (actual.hostname or "").casefold()
            and expected.path.rstrip("/") == actual.path.rstrip("/")
        )
    except (TypeError, ValueError):
        return expected_url == actual_url


def safe_title(value: str, fallback: str = "网页视频") -> str:
    cleaned = re.sub(r"\s*[-_]\s*(抖音|哔哩哔哩_bilibili)\s*$", "", value).strip()
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", cleaned).strip(" ._")
    return cleaned[:110] or fallback


def current_page_state(*, require_fresh: bool = False) -> dict[str, Any]:
    state = read_json(VIDEO_STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    if require_fresh:
        age = time.time() - float(state.get("timestamp") or 0)
        if age > 5:
            raise CliError(
                "浏览器页面状态已过期。",
                code="STALE_PAGE_STATE",
                details={"age_seconds": round(age, 3), "state": state},
            )
    return state


def public_page_state(state: dict[str, Any] | None = None) -> dict[str, Any]:
    source = state if state is not None else current_page_state()
    if not source:
        return {}
    keep = (
        "has_video",
        "page_url",
        "title",
        "media_protocol",
        "video_duration",
        "video_width",
        "video_height",
        "douyin_video_id",
        "douyin_exact_media_ready",
        "xhs_note_type",
        "xhs_note",
        "can_go_back",
        "monitor_muted",
        "timestamp",
    )
    result = {key: source.get(key) for key in keep if key in source}
    result["media_url_present"] = bool(source.get("media_url"))
    result["image_count"] = len(source.get("xhs_image_urls") or [])
    return result


def electron_pids() -> list[int]:
    if os.name != "nt":
        return []

    expected = str(ELECTRON_EXE.resolve()).casefold()
    pids: list[int] = []

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot_api = kernel32.CreateToolhelp32Snapshot
    snapshot_api.argtypes = (wintypes.DWORD, wintypes.DWORD)
    snapshot_api.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    process_next.restype = wintypes.BOOL
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    query_path = kernel32.QueryFullProcessImageNameW
    query_path.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    query_path.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    snapshot = snapshot_api(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (None, wintypes.HANDLE(-1).value):
        return []

    entry = ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        has_entry = bool(process_first(snapshot, ctypes.byref(entry)))
        while has_entry:
            if entry.szExeFile.casefold() == ELECTRON_EXE.name.casefold():
                handle = open_process(0x1000, False, entry.th32ProcessID)
                if handle:
                    try:
                        buffer = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(buffer))
                        if query_path(handle, 0, buffer, ctypes.byref(size)):
                            if str(buffer.value).casefold() == expected:
                                pids.append(int(entry.th32ProcessID))
                    finally:
                        close_handle(handle)
            has_entry = bool(process_next(snapshot, ctypes.byref(entry)))
    finally:
        close_handle(snapshot)
    return pids


def browser_window_alive() -> bool:
    status = read_json(BROWSER_STATUS_FILE, {})
    if not isinstance(status, dict):
        return False
    try:
        hwnd = int(status.get("hwnd") or 0)
        return hwnd > 0 and bool(ctypes.windll.user32.IsWindow(hwnd))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def browser_running() -> bool:
    pids = electron_pids()
    if pids:
        return True
    if browser_window_alive():
        return True
    page_state = read_json(VIDEO_STATE_FILE, {})
    if (
        isinstance(page_state, dict)
        and time.time() - float(page_state.get("timestamp") or 0) <= 3
    ):
        return True
    status = read_json(BROWSER_STATUS_FILE, {})
    return bool(
        isinstance(status, dict)
        and status.get("state") == "ready"
        and time.time() - float(status.get("timestamp") or 0) <= 10
    )


def update_browser_command(**updates: Any) -> dict[str, Any]:
    command = read_json(BROWSER_COMMAND_FILE, {})
    if not isinstance(command, dict):
        command = {}
    command.update(updates)
    command["timestamp"] = time.time()
    write_json_atomic(BROWSER_COMMAND_FILE, command)
    return command


def set_browser_visibility(visible: bool) -> None:
    size = read_json(BROWSER_SIZE_FILE, {})
    if not isinstance(size, dict):
        size = {}
    size.setdefault("x", 80)
    size.setdefault("y", 80)
    size.setdefault("width", 1200)
    size.setdefault("height", 700)
    size["visible"] = visible
    size["timestamp"] = time.time()
    write_json_atomic(BROWSER_SIZE_FILE, size)


def ensure_browser(url: str, *, visible: bool, timeout: float) -> dict[str, Any]:
    normalized = resolve_douyin_share_url(normalize_media_url(url))
    if not re.match(r"^https?://", normalized, flags=re.IGNORECASE):
        raise CliError("请输入完整的 http(s) 作品地址。", code="INVALID_URL")
    if not ELECTRON_EXE.is_file() or not (ELECTRON_APP / "main.js").is_file():
        raise CliError(
            "内置 Electron 浏览器组件不完整。",
            code="BROWSER_COMPONENT_MISSING",
            details={"electron": str(ELECTRON_EXE), "app": str(ELECTRON_APP)},
        )

    running = browser_running()
    set_browser_visibility(visible)
    update_browser_command(
        monitor_muted=True,
        recording=False,
        navigation="open",
        navigation_url=normalized,
        navigation_id=time.time_ns(),
    )

    if not running:
        BROWSER_STATUS_FILE.unlink(missing_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [str(ELECTRON_EXE), str(ELECTRON_APP), str(DATA_DIR)],
            cwd=str(APP_DIR),
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = read_json(BROWSER_STATUS_FILE, {})
        if isinstance(status, dict) and status.get("state") == "ready":
            return {
                "started": not running,
                "visible": visible,
                "url": normalized,
                "browser_status": status,
            }
        time.sleep(0.25)
    raise CliError(
        "内置浏览器启动超时。",
        code="BROWSER_START_TIMEOUT",
        details={"timeout_seconds": timeout},
    )


def open_douyin_search_modal(url: str, *, visible: bool, browser_timeout: float, search_timeout: float) -> dict[str, Any]:
    parsed = urlsplit(url)
    modal_id = (parse_qs(parsed.query).get("modal_id") or [""])[0]
    if not (
        (parsed.hostname or "").casefold() in {"douyin.com", "www.douyin.com"}
        and parsed.path.startswith("/search/")
        and modal_id.isdigit()
    ):
        return ensure_browser(url, visible=visible, timeout=browser_timeout)

    query_without_modal = [
        (key, value)
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if key != "modal_id"
        for value in values
    ]
    search_url = parsed._replace(query=urlencode(query_without_modal)).geturl()
    browser = ensure_browser(search_url, visible=visible, timeout=browser_timeout)

    deadline = time.time() + search_timeout
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        state = current_page_state()
        if state:
            last_state = state
        fresh = time.time() - float(state.get("timestamp") or 0) <= 5
        results = state.get("douyin_search_results") or []
        found = any(
            isinstance(item, dict) and str(item.get("video_id") or "") == modal_id
            for item in results
        )
        if fresh and page_matches(search_url, str(state.get("page_url") or "")) and found:
            update_browser_command(
                search_open_video_id=modal_id,
                search_open_id=time.time_ns(),
            )
            return {**browser, "url": url, "search_url": search_url, "video_id": modal_id}
        time.sleep(0.25)
    raise CliError(
        "等待抖音搜索结果中的目标视频超时。",
        code="SEARCH_RESULT_NOT_FOUND",
        details={
            "video_id": modal_id,
            "search_url": search_url,
            "timeout_seconds": search_timeout,
            "visible_video_ids": [
                str(item.get("video_id") or "")
                for item in last_state.get("douyin_search_results") or []
                if isinstance(item, dict)
            ],
        },
    )


def wait_for_media(url: str | None, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        state = current_page_state()
        if state:
            last_state = state
        page_url = str(state.get("page_url") or "")
        fresh = time.time() - float(state.get("timestamp") or 0) <= 5
        lowered_page_url = page_url.casefold()
        if fresh and "/course/nopermission/" in lowered_page_url:
            raise CliError(
                "小鹅通返回“尚未购买或领取此课程”。这表示内置浏览器当前登录身份"
                "没有这条课程链接的权限。请使用购买课程时相同的手机号、微信或机构"
                "账号登录，并优先从“我的已购课程”进入；若官方页面仍显示无权限，"
                "请联系课程商家核对订单绑定的账号和课程链接。",
                code="XIAOETONG_NO_PERMISSION",
                details={
                    "page_url": page_url,
                    "next": "在内置浏览器切换为购买课程时使用的身份，再从已购课程入口打开。",
                },
            )
        if fresh and "/login/auth" in lowered_page_url:
            raise CliError(
                "小鹅通要求登录。请在内置浏览器完成登录，并使用购买课程时的同一身份。",
                code="XIAOETONG_LOGIN_REQUIRED",
                details={"page_url": page_url},
            )
        has_media = (
            bool(state.get("has_video"))
            or bool(state.get("xhs_image_urls"))
            or bool(state.get("douyin_exact_media_ready"))
        )
        matches = not url or page_matches(url, page_url)
        if fresh and has_media and matches:
            return state
        time.sleep(0.25)
    raise CliError(
        "等待作品媒体超时。页面可能要求登录/验证，或视频尚未开始加载。",
        code="MEDIA_WAIT_TIMEOUT",
        details={
            "timeout_seconds": timeout,
            "last_page_state": public_page_state(last_state),
            "next": "运行 browser-open --show，在内置浏览器完成登录或验证后重试。",
        },
    )


def ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg
    except ImportError:
        return ""
    try:
        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return ""


def default_output_dir() -> Path:
    return resolve_output_dir("unknown")


def is_xiaohongshu_url(value: str) -> bool:
    normalized = normalize_media_url(value)
    try:
        host = (urlsplit(normalized).hostname or "").casefold()
    except ValueError:
        return False
    return host in XIAOHONGSHU_HOSTS or host.endswith(".xiaohongshu.com")


def normalize_xhs_note_metadata(
    note: dict[str, Any] | None,
    *,
    page_url: str,
    fallback_title: str,
) -> dict[str, Any]:
    source = note if isinstance(note, dict) else {}
    raw_tags = source.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = re.findall(r"#[^\s#]+", raw_tags)
    tags: list[str] = []
    for value in raw_tags:
        tag = str(value or "").strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag}"
        if tag not in tags:
            tags.append(tag)
    title = str(source.get("title") or fallback_title or "小红书笔记").strip()
    return {
        "platform": "xiaohongshu",
        "note_id": str(source.get("note_id") or "").strip(),
        "title": title,
        "description": str(source.get("description") or "").strip(),
        "author": str(source.get("author") or "").strip(),
        "tags": tags,
        "interaction_text": str(source.get("interaction_text") or "").strip(),
        "page_url": str(source.get("page_url") or page_url or "").strip(),
        "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def write_xhs_metadata_files(
    target_dir: Path,
    *,
    page_url: str,
    fallback_title: str,
    note: dict[str, Any] | None,
) -> list[Path]:
    """Write human-readable and machine-readable companion files for an XHS note."""
    target_dir.mkdir(parents=True, exist_ok=True)
    metadata = normalize_xhs_note_metadata(
        note,
        page_url=page_url,
        fallback_title=fallback_title,
    )
    json_path = target_dir / "作品信息.json"
    markdown_path = target_dir / "正文.md"
    json_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [f"# {metadata['title']}", ""]
    if metadata["author"]:
        lines.append(f"- 作者：{metadata['author']}")
    if metadata["page_url"]:
        lines.append(f"- 来源：{metadata['page_url']}")
    if metadata["interaction_text"]:
        lines.append(f"- 互动数据：{metadata['interaction_text']}")
    if metadata["tags"]:
        lines.append(f"- 标签：{' '.join(metadata['tags'])}")
    if len(lines) > 2:
        lines.append("")
    lines.extend(["## 正文", "", metadata["description"] or "（页面未提供可读取的正文）", ""])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return [json_path, markdown_path]


def queue_current_download(output_dir: Path) -> tuple[str, Path, str]:
    state = current_page_state(require_fresh=True)
    page_url = str(state.get("page_url") or "")
    if not page_url:
        raise CliError("当前浏览器没有作品页面。", code="NO_CURRENT_PAGE")

    image_urls = [
        str(value)
        for value in state.get("xhs_image_urls") or []
        if re.match(r"^https?://", str(value), flags=re.IGNORECASE)
    ][:30]
    media_kind = "images" if image_urls and not state.get("has_video") else "video"
    title = safe_title(str(state.get("title") or "网页视频"))
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = (
        output_dir / f"{title}_{stamp}_图文"
        if media_kind == "images"
        else output_dir / f"{title}_{stamp}.mp4"
    )
    if media_kind == "images":
        write_xhs_metadata_files(
            target,
            page_url=page_url,
            fallback_title=title,
            note=state.get("xhs_note"),
        )
    download_id = str(time.time_ns())
    DOWNLOAD_STATUS_FILE.unlink(missing_ok=True)
    update_browser_command(
        monitor_muted=True,
        recording=False,
        media_download_id=download_id,
        media_download_kind=media_kind,
        media_download_url=str(state.get("media_url") or ""),
        media_download_urls=image_urls,
        media_download_output=str(target),
        media_download_referer=page_url,
        media_ffmpeg_path=ffmpeg_path(),
    )
    return download_id, target, media_kind


def wait_for_download(download_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_status: dict[str, Any] = {}
    while time.time() < deadline:
        status = read_json(DOWNLOAD_STATUS_FILE, {})
        if isinstance(status, dict) and str(status.get("download_id") or "") == download_id:
            last_status = status
            if str(status.get("state") or "") in TERMINAL_DOWNLOAD_STATES:
                return status
        time.sleep(0.25)
    raise CliError(
        "下载任务等待超时；任务可能仍在后台运行，可用 status 查询。",
        code="DOWNLOAD_TIMEOUT",
        details={
            "download_id": download_id,
            "timeout_seconds": timeout,
            "last_status": last_status,
        },
    )


def available_video_heights(formats: list[dict[str, Any]]) -> list[int]:
    """Return unique downloadable video heights in descending order."""
    heights: set[int] = set()
    for item in formats:
        if item.get("vcodec") in (None, "none"):
            continue
        try:
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if height > 0:
            heights.add(height)
    return sorted(heights, reverse=True)


def metadata_with_ytdlp(
    url: str,
    cookie_file: str | None,
    browser_cookies: str | None = None,
) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise CliError(
            "缺少 yt-dlp；请先安装 requirements.txt。",
            code="DEPENDENCY_MISSING",
            details={"dependency": "yt-dlp"},
        ) from exc

    normalized = normalize_media_url(url)
    page = urlsplit(normalized)
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "logger": QuietYtdlpLogger(),
        "skip_download": True,
        "noplaylist": True,
        # The Python API does not load the user's yt-dlp CLI config. Enable
        # Node explicitly so the bundled yt-dlp-ejs package can solve current
        # YouTube player challenges.
        "js_runtimes": {"node": {}},
        "http_headers": {
            "Referer": normalized,
            "Origin": f"{page.scheme}://{page.netloc}",
        },
    }
    if is_youtube_url(normalized):
        options["extractor_args"] = {
            "youtube": {
                "player_client": [
                    "tv_downgraded",
                    "web_safari",
                    "android_vr",
                ]
            }
        }
    if cookie_file:
        cookie_path = Path(cookie_file).expanduser().resolve()
        if not cookie_path.is_file():
            raise CliError(
                "指定的 cookies.txt 不存在。",
                code="COOKIE_FILE_MISSING",
                details={"path": str(cookie_path)},
            )
        options["cookiefile"] = str(cookie_path)
    elif browser_cookies:
        options["cookiesfrombrowser"] = (browser_cookies,)
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(normalized, download=False)
    except Exception as exc:
        raise ytdlp_cli_error(exc, "METADATA_FAILED") from exc

    formats = info.get("formats") or []
    available_qualities = available_video_heights(formats)
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "webpage_url": info.get("webpage_url") or normalized,
        "format_count": len(formats),
        "available_qualities": available_qualities,
        "max_height": max(available_qualities, default=None),
        "supports_4k": any(height >= 2160 for height in available_qualities),
        "formats": [
            {
                "format_id": item.get("format_id"),
                "ext": item.get("ext"),
                "resolution": item.get("resolution"),
                "vcodec": item.get("vcodec"),
                "acodec": item.get("acodec"),
            }
            for item in formats[:50]
        ],
    }


def info_video_height(info: dict[str, Any]) -> int:
    """Return the selected video's height, including merged format details."""
    heights = [info.get("height")]
    heights.extend(
        item.get("height")
        for item in (info.get("requested_downloads") or [])
        if isinstance(item, dict)
    )
    valid_heights: list[int] = []
    for value in heights:
        try:
            height = int(value or 0)
        except (TypeError, ValueError):
            continue
        if height > 0:
            valid_heights.append(height)
    return max(valid_heights, default=0)


def find_direct_download_outputs(
    output_dir: Path,
    info: dict[str, Any],
    started_at: float,
    *,
    youtube_source: bool,
) -> tuple[list[Path], list[Path]]:
    """Find a fresh output or an existing YouTube file of the same quality."""
    video_id = str(info.get("id") or "")
    created = sorted(
        (
            path
            for path in output_dir.iterdir()
            if path.is_file()
            and path.stat().st_mtime >= started_at - 2
            and (not video_id or f"[{video_id}]" in path.stem)
        ),
        key=lambda path: path.stat().st_mtime,
    )
    media_suffixes = {".mp4", ".mkv", ".webm", ".mov"}
    media_files = [
        path for path in created if path.suffix.casefold() in media_suffixes
    ]
    if media_files or not youtube_source:
        return created, media_files

    expected_height = info_video_height(info)
    if not expected_height:
        return created, media_files
    quality_tag = f"[{expected_height}p]"
    existing_same_quality = sorted(
        (
            path
            for path in output_dir.iterdir()
            if path.is_file()
            and path.suffix.casefold() in media_suffixes
            and (not video_id or f"[{video_id}]" in path.stem)
            and quality_tag.casefold() in path.stem.casefold()
        ),
        key=lambda path: path.stat().st_mtime,
    )
    return created, existing_same_quality


def direct_download_with_ytdlp(
    url: str,
    output_dir: Path,
    cookie_file: str | None = None,
    *,
    quality: str = "best",
    format_selector: str | None = None,
    job_id: str | None = None,
    use_app_session: bool = False,
    browser_cookies: str | None = None,
) -> dict[str, Any]:
    """Download a single media URL through the application's yt-dlp path."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise CliError(
            "缺少 yt-dlp，请先安装 requirements.txt。",
            code="DEPENDENCY_MISSING",
            details={"dependency": "yt-dlp"},
        ) from exc

    normalized = normalize_media_url(url)
    video_id = douyin_video_id(normalized)
    if video_id:
        normalized = f"https://www.douyin.com/video/{video_id}"
    youtube_source = is_youtube_url(normalized)
    page = urlsplit(normalized)
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    direct_media_source = page.netloc.lower().endswith(
        (".douyinvod.com", ".douyinvod.com.cn")
    )
    if youtube_source:
        output_template = "%(title).150B [%(id)s] [%(height)sp].%(ext)s"
    elif direct_media_source:
        # Generic extraction uses the full CDN URL as its title. On Windows,
        # signed Douyin URLs can therefore exceed the path limit or contain
        # query characters that are invalid in a filename.
        output_template = "douyin-direct [%(id).48B].%(ext)s"
    else:
        output_template = "%(title).150B [%(id)s].%(ext)s"
    options: dict[str, Any] = {
        "outtmpl": str(output_dir / output_template),
        "noplaylist": True,
        "windowsfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "logger": QuietYtdlpLogger(),
        "js_runtimes": {"node": {}},
        "concurrent_fragment_downloads": 8,
        "fragment_retries": 10,
        "retries": 10,
        "http_chunk_size": 10 * 1024 * 1024,
        "merge_output_format": "mp4",
        "remuxvideo": "mp4",
        "http_headers": {
            "Referer": normalized,
            "Origin": f"{page.scheme}://{page.netloc}",
        },
    }
    last_progress_write = 0.0

    def progress(update: dict[str, Any]) -> None:
        nonlocal last_progress_write
        if not job_id:
            return
        now = time.time()
        state = str(update.get("status") or "downloading")
        if state == "downloading" and now - last_progress_write < 0.4:
            return
        last_progress_write = now
        downloaded = int(update.get("downloaded_bytes") or 0)
        total = int(
            update.get("total_bytes")
            or update.get("total_bytes_estimate")
            or 0
        )
        write_json_atomic(
            DIRECT_DOWNLOAD_STATUS_FILE,
            {
                "state": "downloading" if state == "downloading" else state,
                "job_id": job_id,
                "url": normalized,
                "downloaded_bytes": downloaded,
                "total_bytes": total,
                "progress_percent": (
                    round(downloaded * 100 / total, 2) if total else None
                ),
                "speed_bps": update.get("speed"),
                "eta_seconds": update.get("eta"),
                "filename": update.get("filename"),
                "timestamp": now,
            },
        )

    if job_id:
        options["progress_hooks"] = [progress]
    if youtube_source:
        options["extractor_args"] = {
            "youtube": {
                "player_client": [
                    "tv_downgraded",
                    "web_safari",
                    "android_vr",
                ]
            }
        }
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    temporary_browser_profile: tempfile.TemporaryDirectory[str] | None = None
    if cookie_file:
        cookie_path = Path(cookie_file).expanduser().resolve()
        if not cookie_path.is_file():
            raise CliError(
                "指定的 cookies.txt 不存在。",
                code="COOKIE_FILE_MISSING",
                details={"path": str(cookie_path)},
            )
        options["cookiefile"] = str(cookie_path)
    elif browser_cookies:
        options["cookiesfrombrowser"] = (browser_cookies,)
    elif use_app_session:
        app_profile = BROWSER_PROFILE_DIR
        local_state = app_profile / "Local State"
        cookie_candidates = sorted(
            app_profile.rglob("Cookies"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if local_state.is_file() and cookie_candidates:
            temporary_browser_profile = tempfile.TemporaryDirectory(
                prefix="media_cli_app_session_"
            )
            temporary_root = Path(temporary_browser_profile.name)
            temporary_profile = temporary_root / "Profile"
            temporary_network = temporary_profile / "Network"
            temporary_network.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_state, temporary_root / "Local State")
            shutil.copy2(cookie_candidates[0], temporary_network / "Cookies")
            options["cookiesfrombrowser"] = ("chrome", str(temporary_profile))

    info: dict[str, Any] | None = None
    last_error: Exception | None = None
    profiles = youtube_format_selectors(quality, format_selector)
    try:
        for selector in profiles:
            try:
                with yt_dlp.YoutubeDL({**options, "format": selector}) as downloader:
                    extracted = downloader.extract_info(normalized, download=True)
                    info = extracted if isinstance(extracted, dict) else {}
                break
            except Exception as exc:
                last_error = exc
                if "Requested format is not available" not in str(exc):
                    break
    finally:
        if temporary_browser_profile is not None:
            temporary_browser_profile.cleanup()
    if info is None:
        if last_error is not None:
            translated = ytdlp_cli_error(last_error, "DIRECT_DOWNLOAD_FAILED")
            if translated.details is None:
                translated.details = {}
            if isinstance(translated.details, dict):
                translated.details.update({"url": normalized, "video_id": video_id})
            raise translated from last_error
        raise CliError(
            "yt-dlp 未返回可下载内容",
            code="DIRECT_DOWNLOAD_FAILED",
            details={"url": normalized, "video_id": video_id},
        )

    output_match_info = info
    if direct_media_source:
        # A generic CDN extractor derives its id from the signed query string,
        # while the Windows-safe template intentionally truncates that value.
        # Match the fresh file by timestamp and media suffix instead.
        output_match_info = {**info, "id": ""}
    created, media_files = find_direct_download_outputs(
        output_dir,
        output_match_info,
        started_at,
        youtube_source=youtube_source,
    )
    if not media_files:
        raise CliError(
            "下载器返回成功，但没有找到生成的视频文件。",
            code="DIRECT_OUTPUT_NOT_FOUND",
            details={"url": normalized, "created": [str(path) for path in created]},
        )
    return {
        "platform": "youtube" if youtube_source else (
            "douyin" if video_id else (page.hostname or "unknown")
        ),
        "extractor": str(info.get("extractor_key") or info.get("extractor") or ""),
        "id": str(info.get("id") or video_id),
        "title": str(info.get("title") or ""),
        "description": str(info.get("description") or ""),
        "webpage_url": str(info.get("webpage_url") or normalized),
        "timestamp": info.get("timestamp"),
        "upload_date": info.get("upload_date"),
        "release_timestamp": info.get("release_timestamp"),
        "duration": info.get("duration"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "repost_count": info.get("repost_count"),
        "uploader": str(info.get("uploader") or ""),
        "uploader_id": str(info.get("uploader_id") or ""),
        "channel_id": str(info.get("channel_id") or ""),
        "format_id": str(info.get("format_id") or ""),
        "ext": str(info.get("ext") or media_files[-1].suffix.lstrip(".")),
        "quality": quality,
        "tags": [
            str(tag)
            for tag in (info.get("tags") or [])
            if isinstance(tag, (str, int, float))
        ],
        "output": str(media_files[-1]),
        "created": [str(path) for path in created],
    }


def command_doctor(_args: argparse.Namespace) -> dict[str, Any]:
    dependencies: dict[str, bool] = {}
    for name in ("yt_dlp", "imageio_ffmpeg"):
        try:
            __import__(name)
            dependencies[name] = True
        except ImportError:
            dependencies[name] = False
    firefox = find_firefox_executable()
    return {
        "ok": ELECTRON_EXE.is_file() and (ELECTRON_APP / "main.js").is_file(),
        "app": APP_NAME,
        "version": APP_VERSION,
        "app_dir": str(APP_DIR),
        "data_dir": str(DATA_DIR),
        "python": sys.executable,
        "dependencies": dependencies,
        "process_control": {
            "available": os.name == "nt",
            "backend": "windows-native" if os.name == "nt" else "unsupported",
        },
        "firefox": {
            "available": firefox is not None,
            "executable": str(firefox) if firefox else "",
            "youtube_note": "先在 Firefox 登录 YouTube 并完全退出 Firefox，再使用登录状态下载。",
        },
        "electron": {
            "executable": str(ELECTRON_EXE),
            "available": ELECTRON_EXE.is_file(),
            "app_available": (ELECTRON_APP / "main.js").is_file(),
            "running": browser_running(),
            "pids": electron_pids(),
        },
        "browser_status": read_json(BROWSER_STATUS_FILE, {}),
        "page_state": public_page_state(),
        "download_status": read_json(DOWNLOAD_STATUS_FILE, {}),
    }


def command_normalize(args: argparse.Namespace) -> dict[str, Any]:
    normalized = normalize_media_url(args.url)
    return {"ok": bool(re.match(r"^https?://", normalized, flags=re.IGNORECASE)), "url": normalized}


def command_metadata(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": True,
        "metadata": metadata_with_ytdlp(
            args.url,
            args.cookie_file,
            getattr(args, "browser_cookies", None),
        ),
    }


def command_browser_open(args: argparse.Namespace) -> dict[str, Any]:
    result = ensure_browser(args.url, visible=args.show, timeout=args.timeout)
    if args.wait_media:
        result["page_state"] = public_page_state(wait_for_media(result["url"], args.wait_media))
    return {"ok": True, **result}


def command_current(_args: argparse.Namespace) -> dict[str, Any]:
    state = current_page_state()
    return {"ok": bool(state), "page_state": public_page_state(state)}


def command_search(args: argparse.Namespace) -> dict[str, Any]:
    query = str(args.query or "").strip()
    if not query:
        raise CliError("搜索词不能为空。", code="EMPTY_QUERY")
    search_url = f"https://www.douyin.com/search/{quote(query)}?type=video"
    ensure_browser(search_url, visible=args.show_browser, timeout=args.browser_timeout)
    deadline = time.time() + args.search_timeout
    last_state: dict[str, Any] = {}
    while time.time() < deadline:
        state = current_page_state()
        if state:
            last_state = state
        fresh = time.time() - float(state.get("timestamp") or 0) <= 5
        matches = page_matches(search_url, str(state.get("page_url") or ""))
        results = state.get("douyin_search_results") or []
        if fresh and matches and isinstance(results, list) and results:
            filtered = [
                item for item in results
                if isinstance(item, dict) and int(item.get("likes") or 0) >= args.min_likes
            ][:args.limit]
            return {
                "ok": True,
                "query": query,
                "url": search_url,
                "total_visible": len(results),
                "matched": len(filtered),
                "min_likes": args.min_likes,
                "results": filtered,
            }
        time.sleep(0.25)
    raise CliError(
        "等待抖音搜索结果超时；页面可能要求登录或验证。",
        code="SEARCH_WAIT_TIMEOUT",
        details={
            "query": query,
            "timeout_seconds": args.search_timeout,
            "page_state": public_page_state(last_state),
            "search_result_count": last_state.get("douyin_search_result_count", 0),
            "search_debug": last_state.get("douyin_search_debug", {}),
        },
    )


def command_profile(args: argparse.Namespace) -> dict[str, Any]:
    normalized = normalize_media_url(args.url)
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise CliError("主页地址无效。", code="INVALID_PROFILE_URL") from exc
    if not (
        (parsed.hostname or "").casefold() in {"douyin.com", "www.douyin.com"}
        and parsed.path.startswith("/user/")
    ):
        raise CliError(
            "请输入完整的抖音用户主页地址。",
            code="INVALID_PROFILE_URL",
            details={"url": normalized},
        )

    ensure_browser(normalized, visible=args.show_browser, timeout=args.browser_timeout)
    results: dict[str, dict[str, Any]] = {}
    rounds: list[dict[str, Any]] = []
    unchanged_rounds = 0
    last_scroll_y = -1

    for round_index in range(args.max_scrolls + 1):
        deadline = time.time() + args.profile_timeout
        state: dict[str, Any] = {}
        while time.time() < deadline:
            state = current_page_state()
            fresh = time.time() - float(state.get("timestamp") or 0) <= 5
            if fresh and page_matches(normalized, str(state.get("page_url") or "")):
                break
            time.sleep(0.25)
        else:
            raise CliError(
                "等待抖音主页数据超时；页面可能要求登录或验证。",
                code="PROFILE_WAIT_TIMEOUT",
                details={"url": normalized, "page_state": public_page_state(state)},
            )

        before_count = len(results)
        for item in state.get("douyin_profile_results") or []:
            if not isinstance(item, dict):
                continue
            video_id = str(item.get("video_id") or "")
            if not video_id:
                continue
            results[video_id] = {
                **results.get(video_id, {}),
                **item,
                "first_seen_round": results.get(video_id, {}).get(
                    "first_seen_round", round_index
                ),
            }
        debug = state.get("douyin_profile_debug") or {}
        scroll_y = int(debug.get("scroll_y") or 0)
        rounds.append(
            {
                "round": round_index,
                "visible": int(state.get("douyin_profile_result_count") or 0),
                "unique": len(results),
                "scroll_y": scroll_y,
                "scroll_height": int(debug.get("scroll_height") or 0),
            }
        )
        if len(results) >= args.limit:
            break
        unchanged_rounds = unchanged_rounds + 1 if len(results) == before_count else 0
        if unchanged_rounds >= args.stop_after_unchanged:
            break
        if round_index >= args.max_scrolls:
            break

        update_browser_command(
            scroll_page_id=time.time_ns(),
            scroll_page_pixels=args.scroll_pixels,
        )
        prior_timestamp = float(state.get("timestamp") or 0)
        wait_deadline = time.time() + args.scroll_wait
        while time.time() < wait_deadline:
            newer = current_page_state()
            if float(newer.get("timestamp") or 0) > prior_timestamp:
                newer_debug = newer.get("douyin_profile_debug") or {}
                newer_scroll_y = int(newer_debug.get("scroll_y") or 0)
                if newer_scroll_y != last_scroll_y:
                    last_scroll_y = newer_scroll_y
                    break
            time.sleep(0.2)
        time.sleep(max(0.0, args.settle_wait))

    ordered = sorted(
        results.values(),
        key=lambda item: (
            int(item.get("first_seen_round") or 0),
            str(item.get("video_id") or ""),
        ),
    )[: args.limit]
    return {
        "ok": True,
        "url": normalized,
        "matched": len(ordered),
        "results": ordered,
        "rounds": rounds,
        "profile_debug": (current_page_state().get("douyin_profile_debug") or {}),
    }


def command_status(_args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": True,
        "browser_running": browser_running(),
        "browser_status": read_json(BROWSER_STATUS_FILE, {}),
        "page_state": public_page_state(),
        "download_status": read_json(DOWNLOAD_STATUS_FILE, {}),
        "direct_download_status": read_json(DIRECT_DOWNLOAD_STATUS_FILE, {}),
    }


def require_rights(args: argparse.Namespace) -> None:
    if not args.confirm_rights:
        raise CliError(
            "下载命令必须显式提供 --confirm-rights。",
            code="RIGHTS_CONFIRMATION_REQUIRED",
        )


def command_download(args: argparse.Namespace) -> dict[str, Any]:
    require_rights(args)
    browser = open_douyin_search_modal(
        args.url,
        visible=args.show_browser,
        browser_timeout=args.browser_timeout,
        search_timeout=args.media_timeout,
    )
    page_state = wait_for_media(browser["url"], args.media_timeout)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else default_output_dir()
    download_id, target, media_kind = queue_current_download(output_dir)
    status = wait_for_download(download_id, args.download_timeout)
    success = status.get("state") == "completed"
    return {
        "ok": success,
        "download_id": download_id,
        "kind": media_kind,
        "requested_output": str(target),
        "output": status.get("output") or str(target),
        "page_state": public_page_state(page_state),
        "download_status": status,
    }


def command_xiaohongshu_download(args: argparse.Namespace) -> dict[str, Any]:
    if not is_xiaohongshu_url(args.url):
        raise CliError(
            "该地址不是受支持的小红书作品或分享链接。",
            code="INVALID_XIAOHONGSHU_URL",
            details={"url": normalize_media_url(args.url)},
        )
    return command_download(args)


def command_download_direct(args: argparse.Namespace) -> dict[str, Any]:
    require_rights(args)
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else default_output_dir()
    )
    job_id = str(time.time_ns())
    write_json_atomic(
        DIRECT_DOWNLOAD_STATUS_FILE,
        {
            "state": "starting",
            "job_id": job_id,
            "url": normalize_media_url(args.url),
            "output_dir": str(output_dir),
            "timestamp": time.time(),
        },
    )
    try:
        result = direct_download_with_ytdlp(
            args.url,
            output_dir,
            args.cookie_file,
            quality=args.quality,
            format_selector=args.format_selector,
            job_id=job_id,
            use_app_session=args.use_app_session,
            browser_cookies=args.browser_cookies,
        )
    except Exception as exc:
        write_json_atomic(
            DIRECT_DOWNLOAD_STATUS_FILE,
            {
                "state": "error",
                "job_id": job_id,
                "url": normalize_media_url(args.url),
                "output_dir": str(output_dir),
                "error": str(exc),
                "timestamp": time.time(),
            },
        )
        raise
    completed = {
        "state": "completed",
        "job_id": job_id,
        "url": normalize_media_url(args.url),
        "output": result.get("output"),
        "title": result.get("title"),
        "timestamp": time.time(),
    }
    write_json_atomic(DIRECT_DOWNLOAD_STATUS_FILE, completed)
    return {"ok": True, "job_id": job_id, "kind": "video", **result}


def command_youtube_metadata(args: argparse.Namespace) -> dict[str, Any]:
    if not is_youtube_url(args.url):
        raise CliError(
            "该地址不是受支持的 YouTube 视频链接。",
            code="NOT_YOUTUBE_URL",
            details={"url": normalize_media_url(args.url)},
        )
    return command_metadata(args)


def command_youtube_download(args: argparse.Namespace) -> dict[str, Any]:
    if not is_youtube_url(args.url):
        raise CliError(
            "该地址不是受支持的 YouTube 视频链接。",
            code="NOT_YOUTUBE_URL",
            details={"url": normalize_media_url(args.url)},
        )
    return command_download_direct(args)


def command_download_current(args: argparse.Namespace) -> dict[str, Any]:
    require_rights(args)
    if not browser_running():
        raise CliError("内置浏览器尚未运行。", code="BROWSER_NOT_RUNNING")
    wait_for_media(None, args.media_timeout)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else default_output_dir()
    download_id, target, media_kind = queue_current_download(output_dir)
    status = wait_for_download(download_id, args.download_timeout)
    success = status.get("state") == "completed"
    return {
        "ok": success,
        "download_id": download_id,
        "kind": media_kind,
        "requested_output": str(target),
        "output": status.get("output") or str(target),
        "download_status": status,
    }


def command_close_browser(args: argparse.Namespace) -> dict[str, Any]:
    if not browser_running():
        return {"ok": True, "already_closed": True}
    shutdown_id = time.time_ns()
    update_browser_command(shutdown_id=shutdown_id, recording=False)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if not browser_running():
            return {"ok": True, "closed": True, "shutdown_id": shutdown_id}
        time.sleep(0.25)
    raise CliError(
        "内置浏览器未在规定时间内关闭。",
        code="BROWSER_CLOSE_TIMEOUT",
        details={"timeout_seconds": args.timeout, "pids": electron_pids()},
    )


def command_cancel_download(args: argparse.Namespace) -> dict[str, Any]:
    status = read_json(DOWNLOAD_STATUS_FILE, {})
    state = str(status.get("state") or "") if isinstance(status, dict) else ""
    download_id = str(status.get("download_id") or "") if isinstance(status, dict) else ""
    if state in TERMINAL_DOWNLOAD_STATES:
        return {
            "ok": True,
            "already_finished": True,
            "download_id": download_id or None,
            "state": state,
        }
    cancel_id = str(time.time_ns())
    update_browser_command(
        media_cancel_id=cancel_id,
        media_cancel_download_id=download_id,
    )
    if not browser_running():
        return {
            "ok": True,
            "cancelled": True,
            "browser_running": False,
            "cancel_id": cancel_id,
        }
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        current = read_json(DOWNLOAD_STATUS_FILE, {})
        if (
            isinstance(current, dict)
            and str(current.get("state") or "") in {"cancelled", "completed", "error"}
        ):
            return {
                "ok": True,
                "cancelled": str(current.get("state") or "") == "cancelled",
                "cancel_id": cancel_id,
                "download_status": current,
            }
        time.sleep(0.1)
    return {
        "ok": True,
        "cancel_requested": True,
        "cancel_id": cancel_id,
        "download_id": download_id or None,
    }


def command_platforms(_args: argparse.Namespace) -> dict[str, Any]:
    return {
        "ok": True,
        "platforms": [platform_payload(platform) for platform in PLATFORMS],
    }


def command_detect(args: argparse.Namespace) -> dict[str, Any]:
    normalized = normalize_media_url(args.url)
    platform = detect_platform(normalized)
    payload = platform_payload(platform)
    return {
        "ok": payload["supported"],
        "url": normalized,
        "platform": payload,
    }


def command_config_get(_args: argparse.Namespace) -> dict[str, Any]:
    return {"ok": True, "config": public_config()}


def command_config_set(args: argparse.Namespace) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    current = load_config()
    if args.default_output_dir is not None:
        target = Path(args.default_output_dir).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        updates["default_output_dir"] = str(target)
    if args.platform_dir is not None:
        platform, raw_path = args.platform_dir
        if platform not in PLATFORMS:
            raise CliError(
                f"不支持的平台：{platform}",
                code="INVALID_PLATFORM",
                details={"allowed": list(PLATFORMS)},
            )
        platform_dirs = dict(current.get("platform_output_dirs") or {})
        platform_dirs[platform] = (
            str(Path(raw_path).expanduser().resolve()) if raw_path.strip() else ""
        )
        if platform_dirs[platform]:
            Path(platform_dirs[platform]).mkdir(parents=True, exist_ok=True)
        updates["platform_output_dirs"] = platform_dirs
    if args.quality is not None:
        updates["quality"] = args.quality
    if args.browser_cookies is not None:
        updates["browser_cookies"] = args.browser_cookies
    if args.show_browser is not None:
        updates["show_browser_when_needed"] = args.show_browser
    if args.accept_terms:
        updates["accepted_terms"] = True
    if not updates:
        raise CliError("没有提供需要修改的设置。", code="NO_CONFIG_CHANGES")
    return {"ok": True, "config": public_config(save_config(updates))}


def command_history_list(args: argparse.Namespace) -> dict[str, Any]:
    rows = list_history(args.limit)
    return {"ok": True, "count": len(rows), "items": rows}


def command_history_clear(args: argparse.Namespace) -> dict[str, Any]:
    if not args.yes:
        raise CliError(
            "清空记录需要显式提供 --yes。",
            code="CONFIRMATION_REQUIRED",
        )
    return {"ok": True, "cleared": clear_history()}


def _history_from_result(
    *,
    platform: str,
    url: str,
    result: dict[str, Any],
    status: str = "completed",
    error: str = "",
) -> dict[str, Any]:
    download_status = result.get("download_status") or {}
    output = result.get("output") or download_status.get("output") or ""
    return append_history(
        {
            "id": result.get("job_id") or result.get("download_id"),
            "platform": platform,
            "title": result.get("title") or download_status.get("title") or "",
            "url": url,
            "output": output,
            "kind": result.get("kind") or "video",
            "status": status,
            "error": error,
        }
    )


def command_auto_download(args: argparse.Namespace) -> dict[str, Any]:
    require_rights(args)
    normalized = normalize_media_url(extract_url(args.url))
    if not re.match(r"^https?://", normalized, flags=re.IGNORECASE):
        raise CliError(
            "必须提供以 http:// 或 https:// 开头的完整网址。",
            code="INVALID_URL",
            details={"url": normalized},
        )
    platform = getattr(args, "platform", None) or detect_platform(normalized)
    if platform == "unknown":
        raise CliError(
            "无法识别该链接。仅支持 YouTube、抖音、小红书、B站和小鹅通。",
            code="UNSUPPORTED_PLATFORM",
            details={"url": normalized},
        )
    config = load_config()
    output_dir = resolve_output_dir(platform, args.output_dir)
    quality = args.quality or str(config.get("quality") or "1080")
    browser_cookies = args.browser_cookies
    if browser_cookies is None:
        configured_browser = str(config.get("browser_cookies") or "none")
        browser_cookies = None if configured_browser == "none" else configured_browser
    show_browser = (
        bool(config.get("show_browser_when_needed", True))
        if args.show_browser is None
        else args.show_browser
    )
    common = {
        "url": normalized,
        "output_dir": str(output_dir),
        "confirm_rights": True,
    }
    try:
        if platform in {"youtube", "bilibili"}:
            inner = argparse.Namespace(
                **common,
                cookie_file=args.cookie_file,
                browser_cookies=browser_cookies,
                use_app_session=args.use_app_session,
                quality=quality,
                format_selector=args.format_selector,
            )
            result = command_download_direct(inner)
        else:
            inner = argparse.Namespace(
                **common,
                show_browser=show_browser,
                browser_timeout=args.browser_timeout,
                media_timeout=args.media_timeout,
                download_timeout=args.download_timeout,
            )
            result = (
                command_xiaohongshu_download(inner)
                if platform == "xiaohongshu"
                else command_download(inner)
            )
    except Exception as exc:
        _history_from_result(
            platform=platform,
            url=normalized,
            result={},
            status="failed",
            error=str(exc),
        )
        raise
    history = _history_from_result(platform=platform, url=normalized, result=result)
    return {
        **result,
        "platform": platform_payload(platform),
        "output_dir": str(output_dir),
        "history": history,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-cli",
        description=(
            "轻舟下载器 JSON CLI：控制 YouTube、抖音、小红书、B站和小鹅通下载。"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    platforms = subparsers.add_parser("platforms", help="列出支持的平台和能力")
    platforms.set_defaults(handler=command_platforms)

    detect = subparsers.add_parser("detect", help="识别分享文本或链接所属平台")
    detect.add_argument("url")
    detect.set_defaults(handler=command_detect)

    config_get = subparsers.add_parser("config-get", help="读取全部非敏感设置")
    config_get.set_defaults(handler=command_config_get)

    config_set = subparsers.add_parser("config-set", help="修改保存目录和下载偏好")
    config_set.add_argument("--default-output-dir")
    config_set.add_argument(
        "--platform-dir",
        nargs=2,
        metavar=("PLATFORM", "PATH"),
        help="设置某平台独立目录；PATH 传空字符串表示恢复默认目录",
    )
    config_set.add_argument(
        "--quality",
        choices=("best", "2160", "1440", "1080", "720", "480", "360"),
    )
    config_set.add_argument(
        "--browser-cookies",
        choices=("none", "firefox", "edge", "chrome"),
    )
    config_set.add_argument(
        "--show-browser",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="需要登录或验证时是否显示内置浏览器",
    )
    config_set.add_argument("--accept-terms", action="store_true")
    config_set.set_defaults(handler=command_config_set)

    history = subparsers.add_parser("history", help="读取结构化下载记录")
    history.add_argument("--limit", type=int, default=100)
    history.set_defaults(handler=command_history_list)

    history_clear = subparsers.add_parser("history-clear", help="清空下载记录（不删除文件）")
    history_clear.add_argument("--yes", action="store_true")
    history_clear.set_defaults(handler=command_history_clear)

    doctor = subparsers.add_parser("doctor", help="检查依赖、浏览器和任务状态")
    doctor.set_defaults(handler=command_doctor)

    normalize = subparsers.add_parser("normalize", help="从分享文本提取并规范化 URL")
    normalize.add_argument("url")
    normalize.set_defaults(handler=command_normalize)

    metadata = subparsers.add_parser("metadata", help="用 yt-dlp 只读获取元数据")
    metadata.add_argument("url")
    metadata_cookie_source = metadata.add_mutually_exclusive_group()
    metadata_cookie_source.add_argument(
        "--cookie-file",
        help="可选：用户明确提供的 Netscape cookies.txt",
    )
    metadata_cookie_source.add_argument(
        "--browser-cookies",
        choices=("firefox", "edge", "chrome"),
        help="显式读取系统浏览器登录态；Windows 推荐 firefox",
    )
    metadata.set_defaults(handler=command_metadata)

    browser_open = subparsers.add_parser("browser-open", help="打开/导航内置登录浏览器")
    browser_open.add_argument("url")
    browser_open.add_argument("--show", action="store_true", help="显示浏览器，供用户登录或验证")
    browser_open.add_argument("--timeout", type=float, default=20, help="浏览器启动超时秒数")
    browser_open.add_argument("--wait-media", type=float, default=0, help="等待当前作品媒体就绪秒数")
    browser_open.set_defaults(handler=command_browser_open)

    current = subparsers.add_parser("current", help="读取内置浏览器当前作品状态")
    current.set_defaults(handler=command_current)

    search = subparsers.add_parser("search", help="搜索抖音视频并返回可批处理的 JSON 候选")
    search.add_argument("query")
    search.add_argument("--min-likes", type=int, default=1000)
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--show-browser", action="store_true")
    search.add_argument("--browser-timeout", type=float, default=20)
    search.add_argument("--search-timeout", type=float, default=30)
    search.set_defaults(handler=command_search)

    profile = subparsers.add_parser(
        "profile",
        help="读取抖音主页并通过页面滚动返回连续作品候选",
    )
    profile.add_argument("url")
    profile.add_argument("--limit", type=int, default=100)
    profile.add_argument("--max-scrolls", type=int, default=30)
    profile.add_argument("--stop-after-unchanged", type=int, default=4)
    profile.add_argument("--scroll-pixels", type=float, default=900)
    profile.add_argument("--show-browser", action="store_true")
    profile.add_argument("--browser-timeout", type=float, default=20)
    profile.add_argument("--profile-timeout", type=float, default=30)
    profile.add_argument("--scroll-wait", type=float, default=4)
    profile.add_argument("--settle-wait", type=float, default=1.5)
    profile.set_defaults(handler=command_profile)

    status = subparsers.add_parser("status", help="读取浏览器、页面和下载任务状态")
    status.set_defaults(handler=command_status)

    auto_download = subparsers.add_parser(
        "auto-download",
        help="自动识别平台、选择保存目录并下载单条作品",
    )
    auto_download.add_argument("url")
    auto_download.add_argument(
        "--platform",
        choices=tuple(PLATFORMS),
        help="手动指定平台；用于定制域名或自动识别失败时兜底",
    )
    auto_download.add_argument("--output-dir")
    auto_download.add_argument(
        "--quality",
        choices=("best", "2160", "1440", "1080", "720", "480", "360"),
    )
    auto_cookie_source = auto_download.add_mutually_exclusive_group()
    auto_cookie_source.add_argument("--cookie-file")
    auto_cookie_source.add_argument(
        "--browser-cookies",
        choices=("firefox", "edge", "chrome"),
    )
    auto_download.add_argument("--use-app-session", action="store_true")
    auto_download.add_argument("--format", dest="format_selector")
    auto_download.add_argument(
        "--show-browser",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    auto_download.add_argument("--browser-timeout", type=float, default=20)
    auto_download.add_argument("--media-timeout", type=float, default=60)
    auto_download.add_argument("--download-timeout", type=float, default=1800)
    auto_download.add_argument("--confirm-rights", action="store_true")
    auto_download.set_defaults(handler=command_auto_download)

    download = subparsers.add_parser("download", help="打开作品并下载当前视频/图文")
    download.add_argument("url")
    download.add_argument("--output-dir")
    download.add_argument("--confirm-rights", action="store_true", help="确认拥有下载或研究权限")
    download.add_argument("--show-browser", action="store_true", help="下载时显示内置浏览器")
    download.add_argument("--browser-timeout", type=float, default=20)
    download.add_argument("--media-timeout", type=float, default=45)
    download.add_argument("--download-timeout", type=float, default=600)
    download.set_defaults(handler=command_download)

    xhs_download = subparsers.add_parser(
        "xiaohongshu-download",
        help="下载小红书视频或完整图文，并返回机器可读 JSON",
    )
    xhs_download.add_argument("url")
    xhs_download.add_argument("--output-dir")
    xhs_download.add_argument(
        "--confirm-rights",
        action="store_true",
        help="确认拥有下载或研究权限",
    )
    xhs_download.add_argument(
        "--show-browser",
        action="store_true",
        help="显示内置浏览器，便于登录或完成人机验证",
    )
    xhs_download.add_argument("--browser-timeout", type=float, default=20)
    xhs_download.add_argument("--media-timeout", type=float, default=45)
    xhs_download.add_argument("--download-timeout", type=float, default=600)
    xhs_download.set_defaults(handler=command_xiaohongshu_download)

    download_direct = subparsers.add_parser(
        "download-direct",
        help="不控制浏览器，直接用软件内置 yt-dlp 方法下载单条作品",
    )
    download_direct.add_argument("url")
    download_direct.add_argument("--output-dir")
    download_direct_cookie_source = download_direct.add_mutually_exclusive_group()
    download_direct_cookie_source.add_argument("--cookie-file")
    download_direct_cookie_source.add_argument(
        "--browser-cookies",
        choices=("firefox", "edge", "chrome"),
        help="显式读取系统浏览器登录态；Windows 推荐 firefox",
    )
    download_direct_cookie_source.add_argument(
        "--use-app-session",
        action="store_true",
        help="复用已导入应用的 Cookie；不能用它登录 Google",
    )
    download_direct.add_argument(
        "--quality",
        choices=("best", "2160", "1440", "1080", "720", "480", "360"),
        default="best",
        help="指定视频清晰度，默认 best；源视频没有该画质时返回错误",
    )
    download_direct.add_argument(
        "--format",
        dest="format_selector",
        help="高级用法：直接传入 yt-dlp 格式选择器",
    )
    download_direct.add_argument(
        "--confirm-rights",
        action="store_true",
        help="确认拥有下载或研究权限",
    )
    download_direct.set_defaults(handler=command_download_direct)

    youtube_metadata = subparsers.add_parser(
        "youtube-metadata",
        help="只读获取单条 YouTube 视频的标题、时长和可用格式",
    )
    youtube_metadata.add_argument("url")
    youtube_metadata_cookie_source = youtube_metadata.add_mutually_exclusive_group()
    youtube_metadata_cookie_source.add_argument(
        "--cookie-file",
        help="可选：用户明确提供的 Netscape cookies.txt",
    )
    youtube_metadata_cookie_source.add_argument(
        "--browser-cookies",
        choices=("firefox", "edge", "chrome"),
        help="显式读取系统浏览器登录态；Windows 推荐 firefox",
    )
    youtube_metadata.set_defaults(handler=command_youtube_metadata)

    youtube_download = subparsers.add_parser(
        "youtube-download",
        help="下载单条 YouTube 视频并合并为 MP4，返回机器可读 JSON",
    )
    youtube_download.add_argument("url")
    youtube_download.add_argument("--output-dir")
    youtube_download_cookie_source = youtube_download.add_mutually_exclusive_group()
    youtube_download_cookie_source.add_argument("--cookie-file")
    youtube_download_cookie_source.add_argument(
        "--browser-cookies",
        choices=("firefox", "edge", "chrome"),
        help="显式读取系统浏览器登录态；Windows 推荐 firefox",
    )
    youtube_download_cookie_source.add_argument(
        "--use-app-session",
        action="store_true",
        help="复用已导入应用的 Cookie；不能用它登录 Google",
    )
    youtube_download.add_argument(
        "--quality",
        choices=("best", "2160", "1440", "1080", "720", "480", "360"),
        default="best",
        help="指定视频清晰度，默认 best；源视频没有该画质时返回错误",
    )
    youtube_download.add_argument(
        "--format",
        dest="format_selector",
        help="高级用法：直接传入 yt-dlp 格式选择器",
    )
    youtube_download.add_argument(
        "--confirm-rights",
        action="store_true",
        help="确认拥有下载或研究权限",
    )
    youtube_download.set_defaults(handler=command_youtube_download)

    download_current = subparsers.add_parser("download-current", help="下载内置浏览器当前作品")
    download_current.add_argument("--output-dir")
    download_current.add_argument("--confirm-rights", action="store_true", help="确认拥有下载或研究权限")
    download_current.add_argument("--media-timeout", type=float, default=15)
    download_current.add_argument("--download-timeout", type=float, default=600)
    download_current.set_defaults(handler=command_download_current)

    close_browser = subparsers.add_parser("close-browser", help="正常关闭内置浏览器")
    close_browser.add_argument("--timeout", type=float, default=10)
    close_browser.set_defaults(handler=command_close_browser)

    cancel_download = subparsers.add_parser("cancel-download", help="取消当前下载任务")
    cancel_download.add_argument("--timeout", type=float, default=5)
    cancel_download.set_defaults(handler=command_cancel_download)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
        emit(result)
        return 0 if result.get("ok", True) else 2
    except CliError as exc:
        emit(
            {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                },
            }
        )
        return 2
    except KeyboardInterrupt:
        emit({"ok": False, "error": {"code": "INTERRUPTED", "message": "用户中断任务。"}})
        return 130
    except Exception as exc:
        emit(
            {
                "ok": False,
                "error": {
                    "code": "UNEXPECTED_ERROR",
                    "message": str(exc),
                    "type": type(exc).__name__,
                },
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
