"""Build the Windows GUI and CLI commercial delivery folder."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = ROOT / "release"
BUILD_ROOT = ROOT / "build"
SPEC_ROOT = ROOT / "spec"
PRODUCT_NAME = "轻舟下载器_1.0.6"
PRODUCT_DIR = RELEASE_ROOT / PRODUCT_NAME
ZIP_PATH = RELEASE_ROOT / f"{PRODUCT_NAME}.zip"


def checked_remove(path: Path) -> None:
    resolved = path.resolve()
    allowed_parents = {ROOT.resolve(), RELEASE_ROOT.resolve()}
    if resolved.parent not in allowed_parents:
        raise RuntimeError(f"Refusing to clean outside project root: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def run(*arguments: str) -> None:
    environment = os.environ.copy()
    # Commercial builds must be reproducible and must not scan optional packages
    # from the current Windows user's AppData profile.
    environment["PYTHONNOUSERSITE"] = "1"
    subprocess.run(arguments, cwd=ROOT, check=True, env=environment)


def copy_delivery_files() -> None:
    shutil.copytree(ROOT / ".vendor", PRODUCT_DIR / ".vendor")
    shutil.copytree(ROOT / "electron_runtime", PRODUCT_DIR / "electron_runtime")
    shutil.copytree(ROOT / "electron_browser", PRODUCT_DIR / "electron_browser")
    for name in (
        "README.md",
        "详细使用教程.md",
        "CLI使用说明.md",
        "使用声明与免责声明.md",
    ):
        shutil.copy2(ROOT / name, PRODUCT_DIR / name)
    shutil.copy2(ROOT / "assets" / "app-icon.png", PRODUCT_DIR / "产品图标.png")
    shutil.copy2(ROOT / "assets" / "app-icon.ico", PRODUCT_DIR / "app-icon.ico")
    (PRODUCT_DIR / "首次使用.txt").write_text(
        """轻舟下载器 1.0.6

第一次使用：
1. 双击“轻舟下载器.exe”。
2. 阅读并确认“关于与合规”。
3. 在“保存设置”选择默认保存目录。
4. YouTube 用户先安装 Firefox，在 Firefox 登录 YouTube 并完全退出 Firefox。
5. 到“下载中心”粘贴作品链接。

每个平台的逐步教程：
先看软件左侧“使用帮助”，或打开《详细使用教程.md》。

AI / 自动化：
运行 media-cli.exe --help
详细说明见 CLI使用说明.md。

用户数据保存在：
%LOCALAPPDATA%\\QingzhouMediaDownloader

请只处理你拥有合法下载、备份或使用权限的内容。
""",
        encoding="utf-8-sig",
    )


def create_zip() -> None:
    with zipfile.ZipFile(
        ZIP_PATH,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in PRODUCT_DIR.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(RELEASE_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    for target in (BUILD_ROOT, SPEC_ROOT, PRODUCT_DIR):
        checked_remove(target)
    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    for target in (BUILD_ROOT, SPEC_ROOT, PRODUCT_DIR):
        target.mkdir(parents=True, exist_ok=True)

    if not args.skip_tests:
        run(
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(ROOT / "tests"),
            "-v",
        )

    run(sys.executable, str(ROOT / "tools" / "create_icon.py"))
    icon = str(ROOT / "assets" / "app-icon.ico")

    run(
        sys.executable,
        "-s",
        str(ROOT / "tools" / "run_pyinstaller_isolated.py"),
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "轻舟下载器",
        "--icon",
        icon,
        "--distpath",
        str(PRODUCT_DIR),
        "--workpath",
        str(BUILD_ROOT / "gui"),
        "--specpath",
        str(SPEC_ROOT),
        str(ROOT / "app.py"),
    )
    run(
        sys.executable,
        "-s",
        str(ROOT / "tools" / "run_pyinstaller_isolated.py"),
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        "media-cli",
        "--icon",
        icon,
        "--collect-all",
        "imageio_ffmpeg",
        "--paths",
        str(ROOT / ".vendor"),
        "--collect-all",
        "yt_dlp",
        "--collect-all",
        "yt_dlp_ejs",
        "--distpath",
        str(PRODUCT_DIR),
        "--workpath",
        str(BUILD_ROOT / "cli"),
        "--specpath",
        str(SPEC_ROOT),
        str(ROOT / "media_cli.py"),
    )
    copy_delivery_files()
    create_zip()

    files = [path for path in PRODUCT_DIR.rglob("*") if path.is_file()]
    total_bytes = sum(path.stat().st_size for path in files)
    print(f"PRODUCT_DIR={PRODUCT_DIR}")
    print(f"ZIP={ZIP_PATH}")
    print(f"FILES={len(files)}")
    print(f"SIZE_MB={total_bytes / 1024 / 1024:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
