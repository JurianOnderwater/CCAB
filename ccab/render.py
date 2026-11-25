"""Caption styling presets, PNG cue rendering (Swift/AppKit & Pillow), and ffmpeg burning."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

NAMED_COLORS: dict[str, str] = {
    "white": "#FFFFFF",
    "black": "#000000",
    "yellow": "#FFD700",
    "red": "#EF4444",
    "green": "#22C55E",
    "blue": "#3B82F6",
    "cyan": "#06B6D4",
    "magenta": "#EC4899",
    "orange": "#F97316",
    "gray": "#6B7280",
    "grey": "#6B7280",
}

PRESETS: dict[str, dict[str, object]] = {
    "pill": {
        "bg_style": "pill",
        "bg_color": "#000000",
        "bg_opacity": 0.68,
        "bg_radius": 10.0,
        "text_color": "#FFFFFF",
        "stroke_width": 0.0,
        "shadow": True,
    },
    "box": {
        "bg_style": "box",
        "bg_color": "#000000",
        "bg_opacity": 0.78,
        "bg_radius": 0.0,
        "text_color": "#FFFFFF",
        "stroke_width": 0.0,
        "shadow": False,
    },
    "bar": {
        "bg_style": "full-bar",
        "bg_color": "#000000",
        "bg_opacity": 0.72,
        "bg_radius": 0.0,
        "text_color": "#FFFFFF",
        "stroke_width": 0.0,
        "shadow": False,
    },
    "outline": {
        "bg_style": "none",
        "text_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 4.0,
        "shadow": True,
    },
    "yellow": {
        "bg_style": "none",
        "text_color": "#FFD700",
        "stroke_color": "#000000",
        "stroke_width": 4.0,
        "shadow": True,
    },
    "minimal": {
        "bg_style": "none",
        "text_color": "#FFFFFF",
        "stroke_width": 0.0,
        "shadow": True,
    },
}


@dataclass
class CaptionStyle:
    preset: str = "pill"
    font: str = "system"
    font_size: float = 0.0
    font_weight: str = "semibold"
    text_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: float = 0.0
    bg_style: str = "pill"
    bg_color: str = "#000000"
    bg_opacity: float = 0.68
    bg_radius: float = 10.0
    pad_x: float = 20.0
    pad_y: float = 10.0
    shadow: bool = True
    position: str = "bottom"
    margin_y: float = 0.0
    max_width: float = 0.84
    line_spacing: float = 4.0
    uppercase: bool = False
    crf: int = 18
    encode_preset: str = "medium"


def parse_color(value: str) -> tuple[float, float, float]:
    """Parse a color name or hex string (#RGB / #RRGGBB) into normalized RGB floats."""
    raw = value.strip().lower()
    hex_str = NAMED_COLORS.get(raw, raw)
    if hex_str.startswith("#"):
        hex_str = hex_str[1:]
    if len(hex_str) == 3:
        hex_str = "".join(c * 2 for c in hex_str)
    if not re.fullmatch(r"[0-9a-f]{6}", hex_str):
        raise ValueError(f"Invalid color '{value}'. Use a color name or #RRGGBB hex.")
    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    return r, g, b


def build_style(preset: str, overrides: dict[str, object]) -> CaptionStyle:
    """Create a CaptionStyle from a named preset plus explicit CLI overrides."""
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset '{preset}'. Choose from: {', '.join(PRESETS)}")
    kwargs: dict[str, object] = {"preset": preset}
    kwargs.update(PRESETS[preset])
    for key, val in overrides.items():
        if val is not None:
            kwargs[key] = val
    style = CaptionStyle(**kwargs)  # type: ignore[arg-type]
    # Validate colors early
    parse_color(style.text_color)
    parse_color(style.stroke_color)
    parse_color(style.bg_color)
    return style


SWIFT_RENDER_SOURCE = r"""
import AppKit
import Foundation

struct RGB: Decodable {
    let r: CGFloat
    let g: CGFloat
    let b: CGFloat
}

struct StyleConfig: Decodable {
    let font: String
    let fontSize: CGFloat
    let fontWeight: String
    let textColor: RGB
    let strokeColor: RGB
    let strokeWidth: CGFloat
    let bgStyle: String
    let bgColor: RGB
    let bgOpacity: CGFloat
    let bgRadius: CGFloat
    let padX: CGFloat
    let padY: CGFloat
    let shadow: Bool
    let position: String
    let marginY: CGFloat
    let maxWidth: CGFloat
    let lineSpacing: CGFloat
    let uppercase: Bool
}

struct Cue: Decodable {
    let index: Int
    let text: String
    let output: String
}

struct Manifest: Decodable {
    let width: Int
    let height: Int
    let style: StyleConfig
    let cues: [Cue]
}

func nsWeight(_ name: String) -> NSFont.Weight {
    switch name.lowercased() {
    case "regular": return .regular
    case "medium": return .medium
    case "semibold": return .semibold
    case "bold": return .bold
    case "heavy": return .heavy
    default: return .semibold
    }
}

guard CommandLine.arguments.count == 2 else {
    fputs("Usage: render_subs.swift <manifest.json>\n", stderr)
    exit(1)
}

let manifestURL = URL(fileURLWithPath: CommandLine.arguments[1])
let data = try Data(contentsOf: manifestURL)
let manifest = try JSONDecoder().decode(Manifest.self, from: data)

let width = manifest.width
let height = manifest.height
let cgW = CGFloat(width)
let cgH = CGFloat(height)
let st = manifest.style

let fontSize = st.fontSize > 0 ? st.fontSize : max(22.0, cgH * 0.042)
let weight = nsWeight(st.fontWeight)
let font: NSFont
if st.font.lowercased() == "system" {
    font = NSFont.systemFont(ofSize: fontSize, weight: weight)
} else if let custom = NSFont(name: st.font, size: fontSize) {
    font = custom
} else {
    font = NSFont.systemFont(ofSize: fontSize, weight: weight)
}

let paragraph = NSMutableParagraphStyle()
paragraph.alignment = .center
paragraph.lineBreakMode = .byWordWrapping
paragraph.lineSpacing = st.lineSpacing

let textNSColor = NSColor(
    calibratedRed: st.textColor.r,
    green: st.textColor.g,
    blue: st.textColor.b,
    alpha: 1.0
)
let strokeNSColor = NSColor(
    calibratedRed: st.strokeColor.r,
    green: st.strokeColor.g,
    blue: st.strokeColor.b,
    alpha: 1.0
)
let bgNSColor = NSColor(
    calibratedRed: st.bgColor.r,
    green: st.bgColor.g,
    blue: st.bgColor.b,
    alpha: st.bgOpacity
)

var fillAttrs: [NSAttributedString.Key: Any] = [
    .font: font,
    .foregroundColor: textNSColor,
    .paragraphStyle: paragraph,
]

var strokeAttrs: [NSAttributedString.Key: Any] = [
    .font: font,
    .foregroundColor: textNSColor,
    .strokeColor: strokeNSColor,
    .strokeWidth: st.strokeWidth * 2.0,
    .paragraphStyle: paragraph,
]

if st.shadow {
    let sh = NSShadow()
    sh.shadowColor = NSColor(white: 0.0, alpha: 0.85)
    sh.shadowBlurRadius = 3.0
    sh.shadowOffset = NSSize(width: 0.0, height: -1.5)
    if st.strokeWidth > 0 {
        strokeAttrs[.shadow] = sh
    } else {
        fillAttrs[.shadow] = sh
    }
}

let maxTextWidth = cgW * max(0.1, min(1.0, st.maxWidth))
let marginY = st.marginY > 0 ? st.marginY : cgH * 0.055

for cue in manifest.cues {
    guard let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: width,
        pixelsHigh: height,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else {
        fputs("Failed to allocate bitmap for cue \(cue.index)\n", stderr)
        exit(1)
    }

    guard let ctx = NSGraphicsContext(bitmapImageRep: rep) else {
        fputs("Failed to create graphics context for cue \(cue.index)\n", stderr)
        exit(1)
    }

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = ctx

    NSColor.clear.setFill()
    NSRect(x: 0, y: 0, width: cgW, height: cgH).fill()

    let displayText = st.uppercase ? cue.text.uppercased() : cue.text
    let measureStr = NSAttributedString(string: displayText, attributes: fillAttrs)
    let measured = measureStr.boundingRect(
        with: NSSize(width: maxTextWidth, height: cgH * 0.6),
        options: [.usesLineFragmentOrigin, .usesFontLeading]
    )

    let textW = ceil(measured.width)
    let textH = ceil(measured.height)
    let boxH = textH + st.padY * 2.0

    let boxY: CGFloat
    switch st.position.lowercased() {
    case "top":
        boxY = cgH - marginY - boxH
    case "center":
        boxY = (cgH - boxH) / 2.0
    default:
        boxY = marginY
    }

    if st.bgStyle != "none" && st.bgOpacity > 0.0 {
        let boxRect: NSRect
        let radius: CGFloat
        if st.bgStyle == "full-bar" {
            boxRect = NSRect(x: 0, y: boxY, width: cgW, height: boxH)
            radius = 0.0
        } else if st.bgStyle == "box" {
            let boxW = min(cgW * 0.94, textW + st.padX * 2.0)
            boxRect = NSRect(x: (cgW - boxW) / 2.0, y: boxY, width: boxW, height: boxH)
            radius = 0.0
        } else {
            let boxW = min(cgW * 0.94, textW + st.padX * 2.0)
            boxRect = NSRect(x: (cgW - boxW) / 2.0, y: boxY, width: boxW, height: boxH)
            radius = st.bgRadius
        }
        let path = NSBezierPath(roundedRect: boxRect, xRadius: radius, yRadius: radius)
        bgNSColor.setFill()
        path.fill()
    }

    let textRect = NSRect(
        x: (cgW - maxTextWidth) / 2.0,
        y: boxY + st.padY - measured.origin.y,
        width: maxTextWidth,
        height: textH + 4.0
    )

    if st.strokeWidth > 0 {
        let strokeStr = NSAttributedString(string: displayText, attributes: strokeAttrs)
        strokeStr.draw(with: textRect, options: [.usesLineFragmentOrigin, .usesFontLeading])
    }
    measureStr.draw(with: textRect, options: [.usesLineFragmentOrigin, .usesFontLeading])

    NSGraphicsContext.restoreGraphicsState()

    guard let pngData = rep.representation(using: .png, properties: [:]) else {
        fputs("Failed to encode PNG for cue \(cue.index)\n", stderr)
        exit(1)
    }
    try pngData.write(to: URL(fileURLWithPath: cue.output))
}
"""


def _style_to_swift_dict(style: CaptionStyle) -> dict[str, object]:
    tr, tg, tb = parse_color(style.text_color)
    sr, sg, sb = parse_color(style.stroke_color)
    br, bg, bb = parse_color(style.bg_color)
    return {
        "font": style.font,
        "fontSize": style.font_size,
        "fontWeight": style.font_weight,
        "textColor": {"r": tr, "g": tg, "b": tb},
        "strokeColor": {"r": sr, "g": sg, "b": sb},
        "strokeWidth": style.stroke_width,
        "bgStyle": style.bg_style,
        "bgColor": {"r": br, "g": bg, "b": bb},
        "bgOpacity": max(0.0, min(1.0, style.bg_opacity)),
        "bgRadius": style.bg_radius,
        "padX": style.pad_x,
        "padY": style.pad_y,
        "shadow": style.shadow,
        "position": style.position,
        "marginY": style.margin_y,
        "maxWidth": style.max_width,
        "lineSpacing": style.line_spacing,
        "uppercase": style.uppercase,
    }


def render_cues_swift(
    width: int,
    height: int,
    cues: list[tuple[float, float, str]],
    style: CaptionStyle,
    tmp_dir: Path,
) -> list[Path]:
    """Render styled caption PNGs via macOS Swift/AppKit."""
    swift_script = tmp_dir / "render_subs.swift"
    manifest_path = tmp_dir / "manifest.json"
    swift_script.write_text(SWIFT_RENDER_SOURCE, encoding="utf-8")

    png_paths: list[Path] = []
    cue_entries: list[dict[str, object]] = []
    for i, (_, _, text) in enumerate(cues):
        png_path = tmp_dir / f"cue_{i:04d}.png"
        png_paths.append(png_path)
        cue_entries.append({"index": i, "text": text, "output": str(png_path)})

    manifest = {
        "width": width,
        "height": height,
        "style": _style_to_swift_dict(style),
        "cues": cue_entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    subprocess.run(["/usr/bin/swift", str(swift_script), str(manifest_path)], check=True)
    return png_paths


def render_cues_pillow(
    width: int,
    height: int,
    cues: list[tuple[float, float, str]],
    style: CaptionStyle,
    tmp_dir: Path,
) -> list[Path]:
    """Render styled caption PNGs via Pillow (cross-platform fallback)."""
    from PIL import Image, ImageDraw, ImageFont

    font_size = int(style.font_size if style.font_size > 0 else max(22, height * 0.042))
    font_candidates = (
        [style.font]
        if style.font.lower() != "system"
        else [
            "DejaVuSans-Bold.ttf",
            "LiberationSans-Bold.ttf",
            "Arial Bold.ttf",
            "Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    )
    font_obj: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.load_default()
    for candidate in font_candidates:
        try:
            font_obj = ImageFont.truetype(candidate, font_size)
            break
        except OSError:
            continue

    tr, tg, tb = (int(c * 255) for c in parse_color(style.text_color))
    sr, sg, sb = (int(c * 255) for c in parse_color(style.stroke_color))
    br, bg, bb = (int(c * 255) for c in parse_color(style.bg_color))
    ba = int(max(0.0, min(1.0, style.bg_opacity)) * 255)

    max_text_width = int(width * max(0.1, min(1.0, style.max_width)))
    margin_y = int(style.margin_y if style.margin_y > 0 else height * 0.055)

    def wrap_text(draw: ImageDraw.ImageDraw, text: str) -> str:
        wrapped_lines: list[str] = []
        for raw_line in text.split("\n"):
            words = raw_line.split()
            if not words:
                wrapped_lines.append("")
                continue
            curr = words[0]
            for word in words[1:]:
                test = f"{curr} {word}"
                bbox = draw.textbbox((0, 0), test, font=font_obj)
                if bbox[2] - bbox[0] <= max_text_width:
                    curr = test
                else:
                    wrapped_lines.append(curr)
                    curr = word
            wrapped_lines.append(curr)
        return "\n".join(wrapped_lines)

    png_paths: list[Path] = []
    for i, (_, _, text) in enumerate(cues):
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        display_text = wrap_text(draw, text.upper() if style.uppercase else text)
        spacing = int(style.line_spacing)
        bbox = draw.multiline_textbbox(
            (0, 0), display_text, font=font_obj, spacing=spacing, align="center"
        )
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        box_h = int(text_h + style.pad_y * 2)

        if style.position == "top":
            box_y = margin_y
        elif style.position == "center":
            box_y = (height - box_h) // 2
        else:
            box_y = height - margin_y - box_h

        if style.bg_style != "none" and ba > 0:
            if style.bg_style == "full-bar":
                rect = [0, box_y, width, box_y + box_h]
                draw.rectangle(rect, fill=(br, bg, bb, ba))
            else:
                box_w = min(int(width * 0.94), int(text_w + style.pad_x * 2))
                box_x = (width - box_w) // 2
                rect = [box_x, box_y, box_x + box_w, box_y + box_h]
                if style.bg_style == "pill" and style.bg_radius > 0:
                    draw.rounded_rectangle(rect, radius=style.bg_radius, fill=(br, bg, bb, ba))
                else:
                    draw.rectangle(rect, fill=(br, bg, bb, ba))

        text_x = (width - text_w) // 2 - bbox[0]
        text_y = int(box_y + style.pad_y) - bbox[1]

        if style.shadow:
            draw.multiline_text(
                (text_x + 1, text_y + 2),
                display_text,
                font=font_obj,
                fill=(0, 0, 0, 200),
                spacing=spacing,
                align="center",
            )

        draw.multiline_text(
            (text_x, text_y),
            display_text,
            font=font_obj,
            fill=(tr, tg, tb, 255),
            spacing=spacing,
            align="center",
            stroke_width=int(style.stroke_width),
            stroke_fill=(sr, sg, sb, 255),
        )

        png_path = tmp_dir / f"cue_{i:04d}.png"
        img.save(png_path, format="PNG")
        png_paths.append(png_path)

    return png_paths


def probe_video_metadata(video_path: Path) -> tuple[int, int, float]:
    """Return (width, height, duration_seconds) using ffprobe or ffmpeg stderr fallback."""
    from ccab.deps import check_deps

    deps = check_deps()
    ffprobe = deps.get("ffprobe")
    if ffprobe:
        res = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(res.stdout)
        stream = info["streams"][0]
        duration = float(info.get("format", {}).get("duration") or 0.0)
        return int(stream["width"]), int(stream["height"]), duration

    ffmpeg = deps.get("ffmpeg") or "ffmpeg"
    res = subprocess.run([ffmpeg, "-i", str(video_path)], capture_output=True, text=True)
    m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})\b", res.stderr)
    if not m:
        raise RuntimeError(f"Could not determine video dimensions for {video_path}")
    dur_m = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", res.stderr)
    duration = (
        int(dur_m.group(1)) * 3600 + int(dur_m.group(2)) * 60 + float(dur_m.group(3))
        if dur_m
        else 0.0
    )
    return int(m.group(1)), int(m.group(2)), duration


def probe_video_dimensions(video_path: Path) -> tuple[int, int]:
    """Return (width, height) of the primary video stream using ffprobe or ffmpeg fallback."""
    width, height, _ = probe_video_metadata(video_path)
    return width, height


def extract_waveform_peaks(video_path: Path, buckets: int = 1200) -> list[float]:
    """Extract normalized mono audio peak amplitudes (0.0..1.0) using ffmpeg."""
    import array
    import sys

    from ccab.deps import check_deps

    ffmpeg = check_deps().get("ffmpeg") or "ffmpeg"
    res = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "8000",
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
    )
    if res.returncode != 0 or len(res.stdout) < 2:
        return []

    raw = res.stdout[: len(res.stdout) - (len(res.stdout) % 2)]
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()

    total = len(samples)
    if total == 0:
        return []

    count = max(1, min(buckets, total))
    peaks: list[int] = []
    for i in range(count):
        start_idx = (i * total) // count
        end_idx = ((i + 1) * total) // count
        if end_idx <= start_idx:
            end_idx = min(total, start_idx + 1)
        chunk = samples[start_idx:end_idx]
        hi = max(chunk)
        lo = min(chunk)
        peaks.append(max(abs(hi), abs(lo)))

    max_peak = max(peaks)
    if max_peak <= 0:
        return [0.0] * count
    return [round(p / max_peak, 4) for p in peaks]


def _render_pngs(
    width: int,
    height: int,
    cues: list[tuple[float, float, str]],
    style: CaptionStyle,
    renderer: str,
    tmp_dir: Path,
) -> list[Path]:
    if renderer == "swift":
        return render_cues_swift(width, height, cues, style, tmp_dir)
    return render_cues_pillow(width, height, cues, style, tmp_dir)


def burn_video(
    video_path: Path,
    cues: list[tuple[float, float, str]],
    output_path: Path,
    style: CaptionStyle,
    renderer: str,
) -> None:
    """Burn rendered caption PNGs into video_path using ffmpeg's overlay filter."""
    if not cues:
        raise ValueError("No caption cues provided.")

    width, height = probe_video_dimensions(video_path)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        png_paths = _render_pngs(width, height, cues, style, renderer, tmp_dir)

        filter_lines: list[str] = []
        prev_label = "0:v"
        for i, (start, end, _) in enumerate(cues):
            in_idx = i + 1
            out_label = f"v{i + 1}"
            filter_lines.append(
                f"[{prev_label}][{in_idx}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[{out_label}]"
            )
            prev_label = out_label

        from ccab.deps import check_deps

        ffmpeg_bin = check_deps().get("ffmpeg") or "ffmpeg"
        cmd = [ffmpeg_bin, "-y", "-i", str(video_path)]
        for png_path in png_paths:
            cmd.extend(["-i", str(png_path)])
        cmd.extend(
            [
                "-filter_complex",
                ";\n".join(filter_lines),
                "-map",
                f"[{prev_label}]",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-crf",
                str(style.crf),
                "-preset",
                style.encode_preset,
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        subprocess.run(cmd, check=True)


def render_preview_frame(
    video_path: Path,
    cues: list[tuple[float, float, str]],
    timestamp: float,
    output_png: Path,
    style: CaptionStyle,
    renderer: str,
) -> None:
    """Render a single preview PNG frame at timestamp with the active (or first) cue overlaid."""
    if not cues:
        raise ValueError("No caption cues provided.")

    width, height = probe_video_dimensions(video_path)
    selected_cue = next(
        ((s, e, t) for (s, e, t) in cues if s <= timestamp <= e),
        cues[0],
    )

    with tempfile.TemporaryDirectory() as tmp:
        from ccab.deps import check_deps

        ffmpeg_bin = check_deps().get("ffmpeg") or "ffmpeg"
        tmp_dir = Path(tmp)
        png_paths = _render_pngs(width, height, [selected_cue], style, renderer, tmp_dir)
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-i",
                str(png_paths[0]),
                "-filter_complex",
                "[0:v][1:v]overlay=0:0",
                "-frames:v",
                "1",
                str(output_png),
            ],
            check=True,
        )


def format_style_summary(style: CaptionStyle) -> str:
    return json.dumps(asdict(style), indent=2)
