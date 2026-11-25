"""CLI entrypoint for ccab."""

from __future__ import annotations

import argparse
from pathlib import Path

from ccab import __version__
from ccab.captions import (
    find_caption_for_stem,
    parse_srt,
    sbv_to_srt,
    vtt_to_srt,
)
from ccab.deps import check_deps, ensure_deps
from ccab.render import (
    PRESETS,
    CaptionStyle,
    build_style,
    burn_video,
    format_style_summary,
    render_preview_frame,
)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccab",
        description="Match captions to videos, convert .sbv/.vtt to .srt, and burn styled captions.",
    )
    parser.add_argument("--version", action="version", version=f"ccab {__version__}")

    paths = parser.add_argument_group("Input / Output")
    paths.add_argument("--video-dir", type=Path, default=Path("Video"), help="Directory of input videos (default: ./Video)")
    paths.add_argument("--captions-dir", type=Path, default=Path("Captions"), help="Directory of caption files (default: ./Captions)")
    paths.add_argument("--output-dir", type=Path, default=Path("Output"), help="Directory for burned videos (default: ./Output)")
    paths.add_argument("--video", type=Path, help="Single input video file (overrides --video-dir)")
    paths.add_argument("--captions", type=Path, help="Single caption file for --video (optional; defaults to stem match)")
    paths.add_argument("--output", type=Path, help="Output file path for single-video mode")

    actions = parser.add_argument_group("Workflow & Dependencies")
    actions.add_argument("--gui", action="store_true", help="Launch the local drag-and-drop CCAB Studio web GUI")
    actions.add_argument("--port", type=int, default=8765, help="Port for --gui (default: 8765)")
    actions.add_argument("--no-browser", action="store_true", help="With --gui, do not auto-open a browser tab")
    actions.add_argument("--force-convert", action="store_true", help="Re-convert .sbv/.vtt to .srt even if .srt already exists")
    actions.add_argument("--preview", type=float, metavar="SECONDS", help="Render a single styled preview frame at SECONDS into Output/<stem>_preview.png")
    actions.add_argument("--dry-run", action="store_true", help="Show matched files, conversions, and resolved style without encoding")
    actions.add_argument("--list-presets", action="store_true", help="List built-in caption style presets and exit")
    actions.add_argument("--install-deps", action="store_true", help="Check and automatically install ffmpeg and rendering dependencies, then exit")
    actions.add_argument("--no-auto-install", action="store_true", help="Do not automatically install missing dependencies")

    look = parser.add_argument_group("Caption Look & Background")
    look.add_argument("--preset", choices=sorted(PRESETS), default="pill", help="Style preset (default: pill)")
    look.add_argument("--font", help="Font family name or 'system' (e.g. 'Helvetica Neue', 'Avenir Next')")
    look.add_argument("--font-size", type=float, help="Font size in pixels (0 = auto based on video height)")
    look.add_argument("--font-weight", choices=["regular", "medium", "semibold", "bold", "heavy"], help="Font weight")
    look.add_argument("--text-color", help="Text color name or hex (#FFFFFF, yellow, etc.)")
    look.add_argument("--stroke-color", help="Outline stroke color name or hex")
    look.add_argument("--stroke-width", type=float, help="Outline stroke width in pixels (0 = disabled)")
    look.add_argument("--bg-style", choices=["pill", "box", "full-bar", "none"], help="Background shape behind captions")
    look.add_argument("--bg-color", help="Background color name or hex (#000000, #0F172A, etc.)")
    look.add_argument("--bg-opacity", type=float, help="Background opacity from 0.0 to 1.0")
    look.add_argument("--bg-radius", type=float, help="Corner radius in pixels for 'pill' background")
    look.add_argument("--pad-x", type=float, help="Horizontal background padding in pixels")
    look.add_argument("--pad-y", type=float, help="Vertical background padding in pixels")
    look.add_argument("--shadow", dest="shadow", action="store_true", default=None, help="Enable text drop shadow")
    look.add_argument("--no-shadow", dest="shadow", action="store_false", help="Disable text drop shadow")
    look.add_argument("--position", choices=["bottom", "top", "center"], help="Vertical placement on frame")
    look.add_argument("--margin-y", type=float, help="Vertical margin from edge in pixels (0 = auto)")
    look.add_argument("--max-width", type=float, help="Max text width as fraction of frame width (0.1 to 1.0)")
    look.add_argument("--line-spacing", type=float, help="Extra spacing between wrapped lines in pixels")
    look.add_argument("--uppercase", action="store_true", default=None, help="Render caption text in UPPERCASE")

    enc = parser.add_argument_group("Encoding")
    enc.add_argument("--crf", type=int, help="x264 CRF quality (lower = higher quality, default: 18)")
    enc.add_argument("--encode-preset", help="x264 speed preset (ultrafast..veryslow, default: medium)")

    return parser


def _resolve_style(args: argparse.Namespace) -> CaptionStyle:
    overrides: dict[str, object] = {
        "font": args.font,
        "font_size": args.font_size,
        "font_weight": args.font_weight,
        "text_color": args.text_color,
        "stroke_color": args.stroke_color,
        "stroke_width": args.stroke_width,
        "bg_style": args.bg_style,
        "bg_color": args.bg_color,
        "bg_opacity": args.bg_opacity,
        "bg_radius": args.bg_radius,
        "pad_x": args.pad_x,
        "pad_y": args.pad_y,
        "shadow": args.shadow,
        "position": args.position,
        "margin_y": args.margin_y,
        "max_width": args.max_width,
        "line_spacing": args.line_spacing,
        "uppercase": args.uppercase,
        "crf": args.crf,
        "encode_preset": args.encode_preset,
    }
    return build_style(args.preset, overrides)


def _prepare_srt(caption_path: Path, srt_dest: Path, dry_run: bool) -> Path:
    ext = caption_path.suffix.lower()
    if ext == ".srt":
        return caption_path
    print(f"[CONVERT] {caption_path.name} -> {srt_dest.name}")
    if not dry_run:
        if ext == ".sbv":
            sbv_to_srt(caption_path, srt_dest)
        elif ext == ".vtt":
            vtt_to_srt(caption_path, srt_dest)
        else:
            raise ValueError(f"Unsupported caption format: {ext}")
    return srt_dest


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_presets:
        for name, cfg in PRESETS.items():
            print(f"{name:10s} -> {cfg}")
        return

    if args.gui:
        from ccab.gui import launch_gui

        launch_gui(port=args.port, open_browser=not args.no_browser)
        return

    if args.install_deps:
        deps = ensure_deps(auto_install=True)
        print(f"[OK] ffmpeg={deps['ffmpeg']} ffprobe={deps['ffprobe']} renderer={deps['renderer']}")
        return

    style = _resolve_style(args)

    if args.dry_run:
        print("[STYLE]")
        print(format_style_summary(style))
        print("[DEPS]", check_deps())

    renderer = "swift"
    if not args.dry_run:
        deps = ensure_deps(auto_install=not args.no_auto_install)
        renderer = deps["renderer"]

    # Collect (video_path, caption_path, srt_path, output_path) jobs
    jobs: list[tuple[Path, Path, Path]] = []
    if args.video:
        video_path = args.video
        if not video_path.is_file():
            raise SystemExit(f"Video file not found: {video_path}")
        if args.captions:
            caption_path = args.captions
        else:
            found = find_caption_for_stem(
                args.captions_dir, video_path.stem, prefer_source=args.force_convert
            )
            if found is None:
                raise SystemExit(f"No matching caption found for {video_path.name} in {args.captions_dir}")
            caption_path = found
        args.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = args.output or (args.output_dir / f"{video_path.stem}.mp4")
        jobs.append((video_path, caption_path, out_path))
    else:
        if not args.video_dir.is_dir() or not args.captions_dir.is_dir():
            raise SystemExit(
                f"Expected '{args.video_dir}' and '{args.captions_dir}' directories to exist."
            )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        videos = [
            p
            for p in sorted(args.video_dir.iterdir())
            if not p.name.startswith(".") and p.is_file() and p.suffix.lower() in VIDEO_EXTS
        ]
        if not videos:
            print(f"No videos found in {args.video_dir}/.")
            return
        for video_path in videos:
            caption_path = find_caption_for_stem(
                args.captions_dir, video_path.stem, prefer_source=args.force_convert
            )
            if caption_path is None:
                print(f"[SKIP] No matching caption found for {video_path.name}")
                continue
            jobs.append((video_path, caption_path, args.output_dir / f"{video_path.stem}.mp4"))

    for video_path, caption_path, output_path in jobs:
        srt_target = caption_path.parent / f"{video_path.stem}.srt"
        if caption_path.suffix.lower() == ".srt":
            print(f"[MATCH] {video_path.name} <-> {caption_path.name}")
        srt_path = _prepare_srt(caption_path, srt_target, dry_run=args.dry_run)

        if args.dry_run:
            target = (
                output_path.parent / f"{video_path.stem}_preview.png"
                if args.preview is not None
                else output_path
            )
            print(f"[PLAN] {video_path.name} + {srt_path.name} -> {target}")
            continue

        cues = parse_srt(srt_path)
        if args.preview is not None:
            preview_path = output_path.parent / f"{video_path.stem}_preview.png"
            print(f"[PREVIEW] {video_path.name} @ {args.preview:.2f}s -> {preview_path}")
            render_preview_frame(video_path, cues, args.preview, preview_path, style, renderer)
            print(f"[DONE] {preview_path}")
        else:
            print(f"[BURN] {video_path.name} + {srt_path.name} -> {output_path}")
            burn_video(video_path, cues, output_path, style, renderer)
            print(f"[DONE] {output_path}")


if __name__ == "__main__":
    main()
