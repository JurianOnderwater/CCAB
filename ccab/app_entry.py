"""Executable entry point: opens CCAB Studio GUI when double-clicked, or runs CLI with arguments."""

from __future__ import annotations

import multiprocessing
import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path


def _find_enclosing_app(executable: Path) -> Path | None:
    for parent in executable.resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def _is_in_applications(app_path: Path) -> bool:
    resolved = app_path.resolve()
    app_roots = (Path("/Applications").resolve(), (Path.home() / "Applications").resolve())
    return any(root in resolved.parents for root in app_roots)


def _copy_app_bundle(src: Path, dst: Path) -> bool:
    if os.access(dst.parent, os.W_OK):
        res = subprocess.run(["/usr/bin/ditto", str(src), str(dst)], capture_output=True)
        return res.returncode == 0
    cmd = f"/usr/bin/ditto {shlex.quote(str(src))} {shlex.quote(str(dst))}"
    apple_script = f'do shell script "{cmd}" with administrator privileges'
    res = subprocess.run(["/usr/bin/osascript", "-e", apple_script], capture_output=True)
    return res.returncode == 0


def _maybe_prompt_copy_to_applications() -> None:
    """On macOS frozen .app launches outside /Applications, offer to copy to /Applications."""
    if platform.system() != "Darwin" or not getattr(sys, "frozen", False):
        return

    app_path = _find_enclosing_app(Path(sys.executable))
    if app_path is None or _is_in_applications(app_path):
        return

    support_dir = Path.home() / "Library" / "Application Support" / "CCAB-Studio"
    skip_marker = support_dir / ".skip_apps_prompt"
    if skip_marker.exists():
        return

    parent_name = app_path.parent.name or str(app_path.parent)
    msg = (
        f"CCAB Studio is currently running from \\\"{parent_name}\\\". "
        "Would you like to copy it to your Applications folder?"
    )
    script = (
        f'button returned of (display dialog "{msg}" '
        'with title "CCAB Studio" '
        'buttons {"Don\'t Ask Again", "Not Now", "Copy to Applications"} '
        'default button "Copy to Applications")'
    )
    res = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
    if res.returncode != 0:
        return

    choice = res.stdout.strip()
    if choice == "Don't Ask Again":
        support_dir.mkdir(parents=True, exist_ok=True)
        skip_marker.touch()
        return

    if choice != "Copy to Applications":
        return

    target = Path("/Applications") / app_path.name
    if not _copy_app_bundle(app_path, target):
        fallback_dir = Path.home() / "Applications"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        target = fallback_dir / app_path.name
        if not _copy_app_bundle(app_path, target):
            return

    subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(target)], capture_output=True)
    subprocess.Popen(["/usr/bin/open", "-n", str(target)])
    sys.exit(0)


def main() -> None:
    multiprocessing.freeze_support()
    # macOS LaunchServices passes -psn_0_... when double-clicking a .app bundle
    args = [arg for arg in sys.argv[1:] if not arg.startswith("-psn_")]
    if args:
        from ccab.cli import main as cli_main

        cli_main(args)
    else:
        _maybe_prompt_copy_to_applications()
        from ccab.gui import launch_gui

        launch_gui(port=8765, open_browser=True)


if __name__ == "__main__":
    main()
