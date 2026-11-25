"""Caption discovery, conversion (.sbv/.vtt -> .srt), and SRT parsing."""

from __future__ import annotations

import re
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
CAPTION_EXTS = {".srt", ".sbv", ".vtt"}

SBV_TS_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*,\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*$"
)
VTT_TS_RE = re.compile(
    r"^\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{1,3})\s+-->\s+(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{1,3})(.*)$"
)
SRT_TS_RE = re.compile(
    r"^\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def clean_text(text: str) -> str:
    """Strip UTF-8 BOMs, replace non-breaking spaces, normalize newlines, and trim trailing line spaces."""
    text = text.replace("\ufeff", "").replace("\u00a0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n"))


def format_srt_ts(hours: str, minutes: str, seconds: str, millis: str) -> str:
    """Format timestamp components as HH:MM:SS,mmm."""
    return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d},{millis.ljust(3, '0')[:3]}"


def sbv_to_srt(sbv_path: Path, srt_path: Path) -> None:
    """Convert an .sbv caption file to .srt."""
    content = clean_text(sbv_path.read_text(encoding="utf-8-sig"))
    blocks = re.split(r"\n\s*\n+", content.strip())
    srt_cues: list[str] = []

    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip() != ""]
        if not lines:
            continue
        m = SBV_TS_RE.match(lines[0])
        if not m:
            continue
        start_ts = format_srt_ts(m.group(1), m.group(2), m.group(3), m.group(4))
        end_ts = format_srt_ts(m.group(5), m.group(6), m.group(7), m.group(8))
        cue_text = "\n".join(line.strip() for line in lines[1:] if line.strip())
        if not cue_text:
            continue
        idx = len(srt_cues) + 1
        srt_cues.append(f"{idx}\n{start_ts} --> {end_ts}\n{cue_text}")

    if not srt_cues:
        raise ValueError(f"No valid cues found in {sbv_path}")

    srt_path.write_text("\n\n".join(srt_cues) + "\n", encoding="utf-8")


def vtt_to_srt(vtt_path: Path, srt_path: Path) -> None:
    """Convert a .vtt caption file to .srt."""
    content = clean_text(vtt_path.read_text(encoding="utf-8-sig"))
    blocks = re.split(r"\n\s*\n+", content.strip())
    srt_cues: list[str] = []

    for block in blocks:
        lines = block.split("\n")
        if not lines:
            continue
        first = lines[0].strip()
        if first.startswith("WEBVTT") or first.startswith("NOTE") or first.startswith("STYLE"):
            continue
        ts_idx = 0
        if not VTT_TS_RE.match(lines[0]) and len(lines) > 1 and VTT_TS_RE.match(lines[1]):
            ts_idx = 1
        m = VTT_TS_RE.match(lines[ts_idx])
        if not m:
            continue
        start_ts = format_srt_ts(m.group(1) or "00", m.group(2), m.group(3), m.group(4))
        end_ts = format_srt_ts(m.group(5) or "00", m.group(6), m.group(7), m.group(8))
        cue_lines = [re.sub(r"<[^>]+>", "", line).strip() for line in lines[ts_idx + 1 :]]
        cue_text = "\n".join(line for line in cue_lines if line)
        if not cue_text:
            continue
        idx = len(srt_cues) + 1
        srt_cues.append(f"{idx}\n{start_ts} --> {end_ts}\n{cue_text}")

    if not srt_cues:
        raise ValueError(f"No valid cues found in {vtt_path}")

    srt_path.write_text("\n\n".join(srt_cues) + "\n", encoding="utf-8")


def _ts_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(srt_path: Path) -> list[tuple[float, float, str]]:
    """Parse an .srt file into (start_seconds, end_seconds, text) tuples."""
    content = clean_text(srt_path.read_text(encoding="utf-8-sig"))
    blocks = re.split(r"\n\s*\n+", content.strip())
    cues: list[tuple[float, float, str]] = []

    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip() != ""]
        if len(lines) < 2:
            continue
        ts_idx = 1 if lines[0].strip().isdigit() else 0
        if ts_idx >= len(lines):
            continue
        m = SRT_TS_RE.match(lines[ts_idx])
        if not m:
            continue
        start = _ts_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _ts_to_seconds(m.group(5), m.group(6), m.group(7), m.group(8))
        text = "\n".join(lines[ts_idx + 1 :]).strip()
        if text:
            cues.append((start, end, text))

    return cues


def find_caption_for_stem(
    captions_dir: Path, stem: str, prefer_source: bool = False
) -> Path | None:
    """Find a caption file matching stem. When prefer_source is True, prefer .sbv/.vtt over .srt."""
    candidates: dict[str, Path] = {}
    for entry in sorted(captions_dir.iterdir()):
        if entry.name.startswith(".") or not entry.is_file():
            continue
        ext = entry.suffix.lower()
        if entry.stem == stem and ext in CAPTION_EXTS:
            candidates[ext] = entry

    order = (".sbv", ".vtt", ".srt") if prefer_source else (".srt", ".sbv", ".vtt")
    for ext in order:
        if ext in candidates:
            return candidates[ext]
    return None
