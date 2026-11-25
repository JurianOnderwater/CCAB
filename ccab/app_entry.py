"""Executable entry point: opens CCAB Studio GUI when double-clicked, or runs CLI with arguments."""

from __future__ import annotations

import multiprocessing
import sys


def main() -> None:
    multiprocessing.freeze_support()
    # macOS LaunchServices passes -psn_0_... when double-clicking a .app bundle
    args = [arg for arg in sys.argv[1:] if not arg.startswith("-psn_")]
    if args:
        from ccab.cli import main as cli_main

        cli_main(args)
    else:
        from ccab.gui import launch_gui

        launch_gui(port=8765, open_browser=True)


if __name__ == "__main__":
    main()
