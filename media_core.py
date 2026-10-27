"""Shared configuration, platform detection, and history utilities."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


APP_NAME = "轻舟下载器"
APP_ID = "QingzhouMediaDownloader"
APP_VERSION = "1.0.6"
CODE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)


def _data_dir() -> Path:
    overridden = os.environ.get("QINGZHOU_DATA_DIR", "").strip()
    if overridden:
        return Path(overridden).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_ID


DATA_DIR = _data_dir()
CONFIG_FILE = DATA_DIR / "config.json"
HISTORY_FILE = DATA_DIR / "history.json"
STATE_DIR = DATA_DIR / "state"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"


def find_firefox_executable() -> Path | None:
    roots = [
        os.environ.get("PROGRAMFILES", "").strip(),
        os.environ.get("PROGRAMFILES(X86)", "").strip(),
        os.environ.get("LOCALAPPDATA", "").strip(),
    ]
    candidates = [
        Path(root) / "Mozilla Firefox" / "firefox.exe"
        for root in roots
        if root
    ]
    return next((path for path in candidates if path.is_file()), None)


def format_bytes(value: Any) -> str:
    try:
        size = max(0.0, float(value or 0))
    except (TypeError, ValueError):
        size = 0.0
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if size < 1024 or candidate == units[-1]:
            break
        size /= 1024
    return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"


def format_duration(value: Any) -> str:
    try:
        seconds = max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        seconds = 0
    if seconds >= 3600:
        return f"{seconds // 3600}小时{(seconds % 3600) // 60}分{seconds % 60}秒"
    if seconds >= 60:
        return f"{seconds // 60}分{seconds % 60}秒"
    return f"{seconds}秒"


def progress_payload(status: dict[str, Any]) -> dict[str, Any]:
    state = str(status.get("state") or "").casefold()
    downloaded = status.get("downloaded_bytes")
    if downloaded is None:
        downloaded = status.get("received_bytes")
    total = status.get("total_bytes")
    speed = status.get("speed_bps")
    if speed is None:
        speed = status.get("transfer_rate_bps")
    eta = status.get("eta_seconds")
    try:
        downloaded_number = max(0.0, float(downloaded or 0))
        total_number = max(0.0, float(total or 0))
        speed_number = max(0.0, float(speed or 0))
        eta_number = max(0.0, float(eta or 0))
    except (TypeError, ValueError):
        downloaded_number = total_number = speed_number = eta_number = 0.0

    raw_percent = status.get("progress_percent")
    try:
        percent = float(raw_percent) if raw_percent is not None else None
    except (TypeError, ValueError):
        percent = None
    if (percent is None or percent <= 0) and total_number > 0:
        percent = downloaded_number * 100 / total_number
    if state in {"completed", "finished"} and (percent is None or percent < 100):
        percent = 100.0
    if percent is not None:
        percent = max(0.0, min(100.0, percent))

    stage_names = {
        "starting": "正在建立下载连接…",
        "downloading": "正在下载…",
        "finished": "正在合并并整理文件…",
        "postprocessing": "正在合并并整理文件…",
        "completed": "下载完成",
        "cancelled": "下载已取消",
        "error": "下载失败",
    }
    raw_detail = str(status.get("detail") or "").strip()
    stage = stage_names.get(state, "正在准备下载…")
    pieces: list[str] = []
    if raw_detail:
        pieces.append(raw_detail)
    if downloaded_number > 0:
        size_text = format_bytes(downloaded_number)
        if total_number > 0:
            size_text += f" / {format_bytes(total_number)}"
        pieces.append(size_text)
    if speed_number > 0:
        pieces.append(f"{format_bytes(speed_number)}/s")
    if eta_number > 0:
        pieces.append(f"剩余约 {format_duration(eta_number)}")
    return {
        "state": state,
        "stage": stage,
        "detail": "　·　".join(pieces) or "正在获取文件大小和速度…",
        "percent": percent,
        "determinate": percent is not None,
    }

PLATFORMS: dict[str, dict[str, Any]] = {
    "youtube": {
        "name": "YouTube",
        "hosts": (
            "youtube.com",
            "youtu.be",
        ),
        "supports": ("视频", "Shorts", "直播回放"),
        "engine": "direct",
        "home": "https://www.youtube.com/",
    },
    "douyin": {
        "name": "抖音",
        "hosts": ("douyin.com", "iesdouyin.com"),
        "supports": ("单条视频",),
        "engine": "browser",
        "home": "https://www.douyin.com/",
    },
    "xiaohongshu": {
        "name": "小红书",
        "hosts": ("xiaohongshu.com", "xhslink.com"),
        "supports": ("视频笔记", "完整图文"),
        "engine": "browser",
        "home": "https://www.xiaohongshu.com/explore",
    },
    "bilibili": {
        "name": "哔哩哔哩",
        "hosts": ("bilibili.com", "b23.tv"),
        "supports": ("单视频", "分P视频"),
        "engine": "direct",
        "home": "https://www.bilibili.com/",
    },
    "xiaoetong": {
        "name": "小鹅通",
        "hosts": (
            "xiaoeknow.com",
            "xiaoe-tech.com",
            "xiaoeknow.cn",
            "xiaoe-tech.cn",
            "xet.citv.cn",
            "xet.pomoho.com",
        ),
        "supports": ("已购课程视频",),
        "engine": "browser",
        "home": "https://study.xiaoe-tech.com/t_l/learnIndex#/muti_index",
    },
}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "default_output_dir": str(Path.home() / "Downloads" / APP_NAME),
    "platform_output_dirs": {platform: "" for platform in PLATFORMS},
    "quality": "1080",
    "browser_cookies": "none",
    "show_browser_when_needed": True,
    "accepted_terms": False,
}


def ensure_data_dirs() -> None:
    for directory in (DATA_DIR, STATE_DIR, BROWSER_PROFILE_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_config() -> dict[str, Any]:
    ensure_data_dirs()
    raw = read_json(CONFIG_FILE, {})
    config = {
        **DEFAULT_CONFIG,
        **(raw if isinstance(raw, dict) else {}),
    }
    overrides = dict(DEFAULT_CONFIG["platform_output_dirs"])
    if isinstance(config.get("platform_output_dirs"), dict):
        overrides.update(
            {
                key: str(value or "")
                for key, value in config["platform_output_dirs"].items()
                if key in PLATFORMS
            }
        )
    config["platform_output_dirs"] = overrides
    return config


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    merged = {**load_config(), **config}
    platform_dirs = dict(DEFAULT_CONFIG["platform_output_dirs"])
    platform_dirs.update(merged.get("platform_output_dirs") or {})
    merged["platform_output_dirs"] = platform_dirs
    write_json_atomic(CONFIG_FILE, merged)
    return merged


def public_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    source = config or load_config()
    return {
        "version": int(source.get("version") or 1),
        "default_output_dir": str(source.get("default_output_dir") or ""),
        "platform_output_dirs": dict(source.get("platform_output_dirs") or {}),
        "quality": str(source.get("quality") or "1080"),
        "browser_cookies": str(source.get("browser_cookies") or "none"),
        "show_browser_when_needed": bool(source.get("show_browser_when_needed", True)),
        "accepted_terms": bool(source.get("accepted_terms", False)),
        "data_dir": str(DATA_DIR),
    }


def extract_url(value: str) -> str:
    match = re.search(r"https?://[^\s]+", str(value or ""), flags=re.IGNORECASE)
    if not match:
        return str(value or "").strip()
    return match.group(0).rstrip(",.;，。；!！？?)]}）】》\"'")


def detect_platform(value: str) -> str:
    candidate = extract_url(value)
    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return "unknown"
    for platform, metadata in PLATFORMS.items():
        if any(host == suffix or host.endswith("." + suffix) for suffix in metadata["hosts"]):
            return platform
    # Xiaoetong storefronts can use a merchant's custom domain. Their lesson
    # player URLs still carry a distinctive /video/v_* path plus course IDs.
    # Requiring both signals avoids classifying an arbitrary `course_id` query
    # parameter as Xiaoetong.
    if parsed.scheme in {"http", "https"} and host:
        path = parsed.path.casefold()
        query = parsed.query.casefold()
        xiaoe_player_path = bool(
            re.search(r"/(?:[^/]+/)*video/v_[a-z0-9_-]+", path)
        )
        xiaoe_course_query = bool(
            re.search(
                r"(?:^|&)(?:product_id|course_id|sub_course_id)="
                r"(?:course|term|resource)_[a-z0-9_-]+",
                query,
            )
        )
        if xiaoe_player_path and xiaoe_course_query:
            return "xiaoetong"
    return "unknown"


def platform_payload(platform: str) -> dict[str, Any]:
    metadata = PLATFORMS.get(platform)
    if not metadata:
        return {
            "id": "unknown",
            "name": "未识别平台",
            "supported": False,
            "engine": "unknown",
            "supports": [],
        }
    return {
        "id": platform,
        "name": metadata["name"],
        "supported": True,
        "engine": metadata["engine"],
        "supports": list(metadata["supports"]),
        "home": metadata["home"],
    }


def resolve_output_dir(platform: str, explicit: str | None = None) -> Path:
    if explicit:
        target = Path(explicit).expanduser()
    else:
        config = load_config()
        override = str((config.get("platform_output_dirs") or {}).get(platform) or "")
        target = Path(override or str(config["default_output_dir"])).expanduser()
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def append_history(entry: dict[str, Any]) -> dict[str, Any]:
    ensure_data_dirs()
    rows = read_json(HISTORY_FILE, [])
    if not isinstance(rows, list):
        rows = []
    record = {
        "id": str(entry.get("id") or time.time_ns()),
        "timestamp": float(entry.get("timestamp") or time.time()),
        "platform": str(entry.get("platform") or "unknown"),
        "title": str(entry.get("title") or ""),
        "url": str(entry.get("url") or ""),
        "output": str(entry.get("output") or ""),
        "kind": str(entry.get("kind") or "video"),
        "status": str(entry.get("status") or "completed"),
        "error": str(entry.get("error") or ""),
    }
    rows.insert(0, record)
    write_json_atomic(HISTORY_FILE, rows[:500])
    return record


def list_history(limit: int = 100) -> list[dict[str, Any]]:
    rows = read_json(HISTORY_FILE, [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows[: max(0, limit)] if isinstance(row, dict)]


def clear_history() -> int:
    count = len(list_history(10000))
    write_json_atomic(HISTORY_FILE, [])
    return count
