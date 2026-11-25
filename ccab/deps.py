"""Dependency detection and automatic installation for ffmpeg, ffprobe, and renderer."""

from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import sys


def _has_pillow() -> bool:
    return importlib.util.find_spec("PIL") is not None


def _has_swift() -> bool:
    if platform.system() != "Darwin" or shutil.which("swift") is None:
        return False
    # Avoid triggering macOS's Xcode CLT install popup on /usr/bin/swift stubs
    res = subprocess.run(["xcode-select", "-p"], capture_output=True)
    return res.returncode == 0


def _find_ffmpeg() -> str | None:
    system_bin = shutil.which("ffmpeg")
    if system_bin:
        return system_bin
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def check_deps() -> dict[str, str | None]:
    """Return paths/availability for ffmpeg, ffprobe, and the caption renderer."""
    system = platform.system()
    if system == "Darwin":
        renderer: str | None = "swift" if _has_swift() else ("pillow" if _has_pillow() else None)
    else:
        renderer = "pillow" if _has_pillow() else None

    return {
        "ffmpeg": _find_ffmpeg(),
        "ffprobe": shutil.which("ffprobe"),
        "renderer": renderer,
    }


def _install_ffmpeg() -> None:
    system = platform.system()
    if system == "Darwin":
        brew = shutil.which("brew")
        if brew:
            print("[DEPS] Installing ffmpeg via Homebrew...")
            subprocess.run([brew, "install", "ffmpeg"], check=True)
            return
        print("[DEPS] Installing bundled static ffmpeg via imageio-ffmpeg (no Xcode CLT required)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "imageio-ffmpeg>=0.5"], check=True)
        return
    elif system == "Linux":
        if shutil.which("apt-get"):
            print("[DEPS] Installing ffmpeg via apt-get...")
            subprocess.run(["sudo", "apt-get", "update"], check=True)
            subprocess.run(["sudo", "apt-get", "install", "-y", "ffmpeg"], check=True)
        elif shutil.which("dnf"):
            print("[DEPS] Installing ffmpeg via dnf...")
            subprocess.run(["sudo", "dnf", "install", "-y", "ffmpeg"], check=True)
        elif shutil.which("pacman"):
            print("[DEPS] Installing ffmpeg via pacman...")
            subprocess.run(["sudo", "pacman", "-S", "--noconfirm", "ffmpeg"], check=True)
        elif shutil.which("apk"):
            print("[DEPS] Installing ffmpeg via apk...")
            subprocess.run(["sudo", "apk", "add", "ffmpeg"], check=True)
        else:
            raise RuntimeError("No supported package manager found to install ffmpeg.")
    elif system == "Windows":
        if shutil.which("winget"):
            print("[DEPS] Installing ffmpeg via winget...")
            subprocess.run(["winget", "install", "-e", "--id", "Gyan.FFmpeg"], check=True)
        elif shutil.which("choco"):
            print("[DEPS] Installing ffmpeg via Chocolatey...")
            subprocess.run(["choco", "install", "ffmpeg", "-y"], check=True)
        else:
            raise RuntimeError("Neither winget nor choco found to install ffmpeg.")
    else:
        raise RuntimeError(f"Unsupported OS '{system}' for automatic ffmpeg install.")


def _install_pillow() -> None:
    print("[DEPS] Installing Pillow via pip...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pillow>=10.0"], check=True)


def ensure_deps(auto_install: bool = True) -> dict[str, str]:
    """Verify required tools exist, optionally installing missing dependencies automatically."""
    status = check_deps()

    if not status["ffmpeg"]:
        if not auto_install:
            raise RuntimeError(
                "Missing ffmpeg. Install ffmpeg or re-run without --no-auto-install."
            )
        _install_ffmpeg()
        status = check_deps()
        if not status["ffmpeg"]:
            raise RuntimeError("ffmpeg still not found after installation.")

    if not status["renderer"]:
        if platform.system() == "Darwin":
            if not auto_install:
                raise RuntimeError("Missing Swift/AppKit or Pillow for caption rendering.")
            _install_pillow()
        else:
            if not auto_install:
                raise RuntimeError(
                    "Missing Pillow for caption rendering. Install with: pip install pillow"
                )
            _install_pillow()
        status = check_deps()
        if not status["renderer"]:
            raise RuntimeError("No caption renderer available after installation.")

    return {
        "ffmpeg": status["ffmpeg"] or "",
        "ffprobe": status["ffprobe"] or "",
        "renderer": status["renderer"] or "",
    }
