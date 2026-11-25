#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Checking system dependencies (ffmpeg)..."
OS="$(uname -s)"
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  if [[ "$OS" == "Darwin" ]]; then
    if command -v brew >/dev/null 2>&1; then
      echo "==> Installing ffmpeg via Homebrew..."
      brew install ffmpeg
    else
      echo "ERROR: ffmpeg is missing and Homebrew was not found. Install Homebrew from https://brew.sh first." >&2
      exit 1
    fi
  elif [[ "$OS" == "Linux" ]]; then
    if command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y ffmpeg
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y ffmpeg
    elif command -v pacman >/dev/null 2>&1; then
      sudo pacman -S --noconfirm ffmpeg
    else
      echo "ERROR: Please install ffmpeg using your Linux package manager." >&2
      exit 1
    fi
  else
    echo "ERROR: Please install ffmpeg manually for $OS." >&2
    exit 1
  fi
else
  echo "    ffmpeg already installed: $(command -v ffmpeg)"
fi

TARGET="."
if [[ "$OS" != "Darwin" ]]; then
  TARGET=".[pillow]"
fi

echo "==> Installing ccab CLI..."
if command -v uv >/dev/null 2>&1; then
  uv tool install --force --editable "$TARGET"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force -e "$TARGET"
else
  python3 -m pip install --user -e "$TARGET"
fi

echo "==> Verifying ccab installation..."
if command -v ccab >/dev/null 2>&1; then
  ccab --install-deps
  echo "==> Ready! Run 'ccab --help' to get started."
else
  echo "Installed! Make sure ~/.local/bin (or your Python user bin dir) is on PATH, then run: ccab --help"
fi
