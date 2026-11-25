#!/usr/bin/env python3
"""Build standalone CCAB-Studio executable bundles via PyInstaller."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _norm_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine or "unknown"


def _ensure_build_env(root: Path) -> None:
    """Re-exec under uv with build deps if PyInstaller or imageio_ffmpeg is missing."""
    import importlib.util

    if importlib.util.find_spec("PyInstaller") and importlib.util.find_spec("imageio_ffmpeg"):
        return
    if os.environ.get("CCAB_BUILD_BOOTSTRAPPED") == "1":
        raise SystemExit("Missing PyInstaller or imageio-ffmpeg. Install with: pip install '.[static-ffmpeg]' pyinstaller")

    uv = shutil.which("uv")
    if uv:
        env = os.environ.copy()
        env["CCAB_BUILD_BOOTSTRAPPED"] = "1"
        cmd = [
            uv,
            "run",
            "--with",
            "pyinstaller",
            "--with",
            "pillow",
            "--with",
            "imageio-ffmpeg",
            "--with-editable",
            str(root),
            "python",
            str(Path(__file__).resolve()),
        ]
        raise SystemExit(subprocess.run(cmd, cwd=root, env=env).returncode)
    raise SystemExit("PyInstaller not installed in this Python env. Install with: pip install '.[static-ffmpeg]' pyinstaller")


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    _ensure_build_env(root)
    dist_dir = root / "dist"
    dist_dir.mkdir(exist_ok=True)

    system = platform.system()
    arch = _norm_arch()
    static_src = root / "ccab" / "static"
    icon_png = static_src / "icon-512.png"
    entry = root / "ccab" / "app_entry.py"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        "CCAB-Studio",
        "--add-data",
        f"{static_src}{os.pathsep}ccab/static",
        "--collect-data",
        "imageio_ffmpeg",
        "--collect-binaries",
        "imageio_ffmpeg",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "PIL.Image",
        "--hidden-import",
        "PIL.ImageDraw",
        "--hidden-import",
        "PIL.ImageFont",
        "--icon",
        str(icon_png),
    ]

    if system == "Darwin":
        cmd.append("--windowed")
    else:
        cmd.append("--onefile")

    cmd.append(str(entry))
    print("[BUILD]", " ".join(cmd))
    subprocess.run(cmd, cwd=root, check=True)

    if system == "Darwin":
        app_path = dist_dir / "CCAB-Studio.app"
        zip_path = dist_dir / f"CCAB-Studio-macos-{arch}.zip"
        subprocess.run(
            ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app_path), str(zip_path)],
            check=True,
        )
        print(f"[DONE] {app_path}")
        print(f"[DONE] {zip_path}")
    elif system == "Windows":
        exe_src = dist_dir / "CCAB-Studio.exe"
        exe_dst = dist_dir / f"CCAB-Studio-windows-{arch}.exe"
        shutil.copy2(exe_src, exe_dst)
        print(f"[DONE] {exe_dst}")
    else:
        bin_src = dist_dir / "CCAB-Studio"
        bin_dst = dist_dir / f"ccab-studio-linux-{arch}"
        shutil.copy2(bin_src, bin_dst)
        bin_dst.chmod(0o755)
        print(f"[DONE] {bin_dst}")


if __name__ == "__main__":
    main()
