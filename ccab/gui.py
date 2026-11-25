"""Local web studio GUI for ccab (drag-and-drop video + captions, live preview, burn)."""

from __future__ import annotations

import argparse
import atexit
import json
import mimetypes
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import uuid
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ccab.captions import CAPTION_EXTS, VIDEO_EXTS, parse_srt, sbv_to_srt, vtt_to_srt, write_srt
from ccab.deps import ensure_deps
from ccab.render import (
    PRESETS,
    CaptionStyle,
    build_style,
    burn_video,
    extract_waveform_peaks,
    probe_video_metadata,
    render_preview_frame,
)

_MEIPASS = getattr(sys, "_MEIPASS", None)
STATIC_DIR = (
    (Path(_MEIPASS) / "ccab" / "static")
    if _MEIPASS
    else (Path(__file__).resolve().parent / "static")
)
STATIC_HTML = STATIC_DIR / "index.html"
STATIC_ASSETS: dict[str, tuple[str, str]] = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json; charset=utf-8"),
    "/sw.js": ("sw.js", "application/javascript; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}


class GuiState:
    """In-memory state and temporary workspace for a single GUI server session."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ccab_gui_")
        self.tmp_dir = Path(self._tmp.name)
        self.lock = threading.Lock()
        self.uploads: dict[str, dict[str, object]] = {}
        self.jobs: dict[str, dict[str, object]] = {}
        atexit.register(self.cleanup)

    def cleanup(self) -> None:
        try:
            self._tmp.cleanup()
        except Exception:
            pass


def _safe_filename(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return cleaned or "file"


def _extract_poster(video_path: Path, poster_path: Path) -> bool:
    from ccab.deps import check_deps

    ffmpeg_bin = check_deps().get("ffmpeg") or "ffmpeg"
    for ts in ("5.0", "0.0"):
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-ss",
                ts,
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(poster_path),
            ],
            capture_output=True,
        )
        if poster_path.is_file() and poster_path.stat().st_size > 0:
            return True
    return False


def _style_from_payload(payload: dict[str, object]) -> CaptionStyle:
    preset = str(payload.get("preset") or "pill")
    allowed_keys = {
        "font",
        "font_size",
        "font_weight",
        "text_color",
        "stroke_color",
        "stroke_width",
        "bg_style",
        "bg_color",
        "bg_opacity",
        "bg_radius",
        "pad_x",
        "pad_y",
        "shadow",
        "position",
        "margin_y",
        "max_width",
        "line_spacing",
        "uppercase",
        "crf",
        "encode_preset",
    }
    overrides = {k: v for k, v in payload.items() if k in allowed_keys and v is not None}
    return build_style(preset, overrides)


def _default_work_dir() -> Path:
    """Return a writable base directory for Output/Captions (Finder .app launches with cwd='/')."""
    cwd = Path.cwd()
    if getattr(sys, "frozen", False) or str(cwd) == "/" or not os.access(cwd, os.W_OK):
        return Path.home() / "Downloads" / "CCAB"
    return cwd


def _parse_cues_payload(raw_cues: object) -> list[tuple[float, float, str]] | None:
    """Validate an optional JSON cues array from the timeline editor."""
    if not isinstance(raw_cues, list) or not raw_cues:
        return None
    parsed: list[tuple[float, float, str]] = []
    for item in raw_cues:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, float(item.get("start") or 0.0))
        end = max(start + 0.05, float(item.get("end") or (start + 0.5)))
        parsed.append((start, end, text))
    parsed.sort(key=lambda c: (c[0], c[1]))
    return parsed or None


def _make_handler(state: GuiState):
    class CcabGuiHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, data: object) -> None:
            self._send_bytes(
                status,
                "application/json; charset=utf-8",
                json.dumps(data, ensure_ascii=False).encode("utf-8"),
            )

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return b""
            return self.rfile.read(length)

        def _read_json(self) -> dict[str, object]:
            raw = self._read_body()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _serve_range_file(self, target: Path, content_type: str) -> None:
            size = target.stat().st_size
            range_header = self.headers.get("Range")
            start = 0
            end = size - 1
            status = HTTPStatus.OK

            if range_header and range_header.startswith("bytes="):
                spec = range_header.removeprefix("bytes=").split(",")[0].strip()
                m = re.fullmatch(r"(\d*)-(\d*)", spec)
                if m:
                    s_str, e_str = m.group(1), m.group(2)
                    if s_str and e_str:
                        start = int(s_str)
                        end = min(int(e_str), size - 1)
                    elif s_str:
                        start = int(s_str)
                    elif e_str:
                        suffix = int(e_str)
                        start = max(0, size - suffix)
                    if start >= size or start > end:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    status = HTTPStatus.PARTIAL_CONTENT

            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            try:
                with target.open("rb") as fh:
                    fh.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = fh.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path == "/" or path == "/index.html":
                html_bytes = STATIC_HTML.read_bytes()
                self._send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", html_bytes)
                return

            if path in STATIC_ASSETS:
                filename, content_type = STATIC_ASSETS[path]
                asset_path = STATIC_DIR / filename
                if not asset_path.is_file():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Asset not found"})
                    return
                body = asset_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                if path == "/sw.js":
                    self.send_header("Service-Worker-Allowed", "/")
                    self.send_header("Cache-Control", "no-cache")
                else:
                    self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/api/presets":
                self._send_json(
                    HTTPStatus.OK,
                    {"presets": PRESETS, "default_style": asdict(CaptionStyle())},
                )
                return

            if path.startswith("/api/poster/"):
                vid_id = path.removeprefix("/api/poster/").strip("/")
                with state.lock:
                    entry = state.uploads.get(vid_id)
                poster = entry.get("poster") if entry else None
                if not isinstance(poster, Path) or not poster.is_file():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Poster not found"})
                    return
                self._send_bytes(HTTPStatus.OK, "image/jpeg", poster.read_bytes())
                return

            if path.startswith("/api/video/"):
                vid_id = path.removeprefix("/api/video/").strip("/")
                with state.lock:
                    entry = state.uploads.get(vid_id)
                video_path = entry.get("path") if (entry and entry.get("kind") == "video") else None
                if not isinstance(video_path, Path) or not video_path.is_file():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Video not found"})
                    return
                ctype = mimetypes.guess_type(video_path.name)[0] or "video/mp4"
                self._serve_range_file(video_path, ctype)
                return

            if path.startswith("/api/waveform/"):
                vid_id = path.removeprefix("/api/waveform/").strip("/")
                with state.lock:
                    entry = state.uploads.get(vid_id)
                if not entry or entry.get("kind") != "video":
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Video not found"})
                    return
                peaks = entry.get("waveform")
                if peaks is None:
                    peaks = extract_waveform_peaks(entry["path"])  # type: ignore[arg-type]
                    with state.lock:
                        entry["waveform"] = peaks
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "duration": float(entry.get("duration") or 0.0),
                        "peaks": peaks,
                    },
                )
                return

            if path.startswith("/api/job/"):
                job_id = path.removeprefix("/api/job/").strip("/")
                with state.lock:
                    job = state.jobs.get(job_id)
                    snapshot = dict(job) if job else None
                if snapshot is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Job not found"})
                    return
                self._send_json(HTTPStatus.OK, snapshot)
                return

            if path.startswith("/api/download/"):
                parts = path.removeprefix("/api/download/").strip("/").split("/")
                if len(parts) != 2:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid download path"})
                    return
                job_id, kind = parts
                with state.lock:
                    job = state.jobs.get(job_id)
                    file_str = (
                        job.get("output_video" if kind == "video" else "output_srt")
                        if job
                        else None
                    )
                if not isinstance(file_str, str) or not Path(file_str).is_file():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "File not ready"})
                    return
                target = Path(file_str)
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                data = target.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
                self.end_headers()
                self.wfile.write(data)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            try:
                if path == "/api/upload":
                    self._handle_upload()
                elif path == "/api/preview":
                    self._handle_preview()
                elif path == "/api/burn":
                    self._handle_burn()
                elif path == "/api/export-srt":
                    self._handle_export_srt()
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def _handle_upload(self) -> None:
            raw_name = urllib.parse.unquote(self.headers.get("X-Filename", "upload"))
            kind = (self.headers.get("X-Kind") or "").strip().lower()
            if kind not in {"video", "subtitle"}:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "X-Kind must be 'video' or 'subtitle'"})
                return

            body = self._read_body()
            if not body:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Empty upload body"})
                return

            uid = uuid.uuid4().hex[:12]
            safe_name = _safe_filename(raw_name)
            ext = Path(safe_name).suffix.lower()
            dest = state.tmp_dir / f"{uid}_{safe_name}"
            dest.write_bytes(body)

            if kind == "subtitle":
                if ext not in CAPTION_EXTS:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": f"Unsupported subtitle format '{ext}'. Expected .srt, .sbv, or .vtt."},
                    )
                    return
                srt_path = dest if ext == ".srt" else (state.tmp_dir / f"{uid}_{Path(safe_name).stem}.srt")
                if ext == ".sbv":
                    sbv_to_srt(dest, srt_path)
                elif ext == ".vtt":
                    vtt_to_srt(dest, srt_path)
                cues = parse_srt(srt_path)
                sample_text = cues[0][2] if cues else ""
                with state.lock:
                    state.uploads[uid] = {
                        "id": uid,
                        "kind": "subtitle",
                        "filename": Path(raw_name).name,
                        "ext": ext,
                        "raw_path": dest,
                        "srt_path": srt_path,
                        "cues": cues,
                    }
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "id": uid,
                        "kind": "subtitle",
                        "filename": Path(raw_name).name,
                        "ext": ext,
                        "needs_convert": ext != ".srt",
                        "cue_count": len(cues),
                        "sample_text": sample_text,
                        "cues": [
                            {"start": round(s, 3), "end": round(e, 3), "text": t}
                            for s, e, t in cues
                        ],
                    },
                )
                return

            if ext not in VIDEO_EXTS:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": f"Unsupported video format '{ext}'."},
                )
                return

            ensure_deps(auto_install=True)
            width, height, duration = probe_video_metadata(dest)
            poster_path = state.tmp_dir / f"{uid}_poster.jpg"
            has_poster = _extract_poster(dest, poster_path)

            with state.lock:
                state.uploads[uid] = {
                    "id": uid,
                    "kind": "video",
                    "filename": Path(raw_name).name,
                    "ext": ext,
                    "path": dest,
                    "width": width,
                    "height": height,
                    "duration": duration,
                    "poster": poster_path if has_poster else None,
                    "waveform": None,
                }
            self._send_json(
                HTTPStatus.OK,
                {
                    "id": uid,
                    "kind": "video",
                    "filename": Path(raw_name).name,
                    "ext": ext,
                    "width": width,
                    "height": height,
                    "duration": round(duration, 3),
                    "poster_url": f"/api/poster/{uid}" if has_poster else None,
                    "video_url": f"/api/video/{uid}",
                    "waveform_url": f"/api/waveform/{uid}",
                },
            )

        def _handle_preview(self) -> None:
            payload = self._read_json()
            video_id = str(payload.get("video_id") or "")
            subtitle_id = str(payload.get("subtitle_id") or "")
            sample_text = str(payload.get("sample_text") or "Sample burned-in caption preview")
            timestamp = float(payload.get("timestamp") or 5.0)
            style = _style_from_payload(dict(payload.get("style") or {}))

            with state.lock:
                vid = state.uploads.get(video_id)
                sub = state.uploads.get(subtitle_id)

            if not vid or vid.get("kind") != "video":
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Drop a video file first for exact frame preview."})
                return

            deps = ensure_deps(auto_install=True)
            edited_cues = _parse_cues_payload(payload.get("cues"))
            cues = edited_cues or (sub.get("cues") if (sub and sub.get("kind") == "subtitle") else None)
            if not isinstance(cues, list) or not cues:
                cues = [(0.0, 9999.0, sample_text)]

            preview_png = state.tmp_dir / f"preview_{uuid.uuid4().hex[:8]}.png"
            render_preview_frame(
                vid["path"],  # type: ignore[arg-type]
                cues,
                timestamp,
                preview_png,
                style,
                deps["renderer"],
            )
            self._send_bytes(HTTPStatus.OK, "image/png", preview_png.read_bytes())

        def _handle_export_srt(self) -> None:
            payload = self._read_json()
            cues = _parse_cues_payload(payload.get("cues"))
            if not cues:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "No valid cues to export."})
                return
            raw_name = str(payload.get("filename") or "aligned.srt")
            stem = Path(_safe_filename(raw_name)).stem or "aligned"
            tmp_srt = state.tmp_dir / f"export_{uuid.uuid4().hex[:8]}.srt"
            write_srt(cues, tmp_srt)
            data = tmp_srt.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-subrip; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{stem}.srt"')
            self.end_headers()
            self.wfile.write(data)

        def _handle_burn(self) -> None:
            payload = self._read_json()
            video_id = str(payload.get("video_id") or "")
            subtitle_id = str(payload.get("subtitle_id") or "")
            style = _style_from_payload(dict(payload.get("style") or {}))
            out_dir_raw = str(payload.get("output_dir") or "").strip()
            edited_cues = _parse_cues_payload(payload.get("cues"))

            with state.lock:
                vid = state.uploads.get(video_id)
                sub = state.uploads.get(subtitle_id)

            if not vid or vid.get("kind") != "video":
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Please drop a video file."})
                return
            if not sub or sub.get("kind") != "subtitle":
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Please drop a subtitle file."})
                return

            base_dir = _default_work_dir()
            output_dir = Path(out_dir_raw).expanduser() if out_dir_raw else (base_dir / "Output")
            captions_dir = base_dir / "Captions"
            output_dir.mkdir(parents=True, exist_ok=True)
            captions_dir.mkdir(parents=True, exist_ok=True)

            video_stem = Path(str(vid["filename"])).stem
            output_video = output_dir / f"{video_stem}.mp4"
            output_srt = captions_dir / f"{video_stem}.srt"

            job_id = uuid.uuid4().hex[:12]
            with state.lock:
                state.jobs[job_id] = {
                    "job_id": job_id,
                    "status": "running",
                    "message": "Preparing dependencies and rendering captions...",
                    "output_video": str(output_video),
                    "output_srt": str(output_srt),
                }

            def _worker() -> None:
                try:
                    deps = ensure_deps(auto_install=True)
                    cues = edited_cues or sub["cues"]
                    if edited_cues:
                        write_srt(edited_cues, output_srt)
                        with state.lock:
                            sub["cues"] = edited_cues
                            write_srt(edited_cues, sub["srt_path"])  # type: ignore[arg-type]
                    else:
                        shutil.copy2(sub["srt_path"], output_srt)  # type: ignore[arg-type]
                    with state.lock:
                        state.jobs[job_id]["message"] = f"Burning {len(cues)} cues into {output_video.name}..."  # type: ignore[arg-type]
                    burn_video(
                        vid["path"],  # type: ignore[arg-type]
                        cues,  # type: ignore[arg-type]
                        output_video,
                        style,
                        deps["renderer"],
                    )
                    with state.lock:
                        state.jobs[job_id].update(
                            {
                                "status": "done",
                                "message": f"Saved to {output_video} and {output_srt}.",
                                "download_video": f"/api/download/{job_id}/video",
                                "download_srt": f"/api/download/{job_id}/srt",
                            }
                        )
                except Exception as exc:
                    with state.lock:
                        state.jobs[job_id].update(
                            {
                                "status": "error",
                                "message": str(exc),
                            }
                        )

            threading.Thread(target=_worker, daemon=True).start()
            self._send_json(HTTPStatus.OK, {"job_id": job_id})

    return CcabGuiHandler


def start_server(port: int = 8765) -> tuple[ThreadingHTTPServer, GuiState]:
    """Start the CCAB GUI HTTP server on 127.0.0.1."""
    state = GuiState()
    handler = _make_handler(state)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    return server, state


def launch_gui(port: int = 8765, open_browser: bool = True) -> None:
    """Launch the local CCAB Studio web GUI and block until interrupted."""
    server, state = start_server(port=port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print(f"CCAB Studio running at {url} (Press Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down CCAB Studio...")
    finally:
        server.server_close()
        state.cleanup()


def install_launch_agent(port: int = 8765) -> Path:
    """Install and start a macOS LaunchAgent so the installed PWA always has its local backend."""
    if platform.system() != "Darwin":
        raise RuntimeError("--install-service is currently supported on macOS only.")

    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = agents_dir / "com.ccab.studio.plist"
    log_path = Path.home() / "Library" / "Logs" / "ccab-studio.log"

    ccab_gui_bin = shutil.which("ccab-gui")
    if ccab_gui_bin:
        prog_args = [ccab_gui_bin, "--no-browser", "--port", str(port)]
    else:
        prog_args = [sys.executable, "-m", "ccab.gui", "--no-browser", "--port", str(port)]

    plist_data = {
        "Label": "com.ccab.studio",
        "ProgramArguments": prog_args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(Path.cwd()),
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"),
        },
    }
    plist_path.write_bytes(plistlib.dumps(plist_data))

    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/com.ccab.studio"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=True)
    print(f"[OK] Installed LaunchAgent: {plist_path}")
    print(f"[OK] CCAB Studio running in background at http://127.0.0.1:{port}/")
    return plist_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ccab-gui", description="Launch the CCAB Studio drag-and-drop GUI.")
    parser.add_argument("--port", type=int, default=8765, help="Local port to bind (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    parser.add_argument(
        "--install-service",
        action="store_true",
        help="Install a macOS background LaunchAgent so the installed PWA icon works anytime",
    )
    args = parser.parse_args(argv)
    if args.install_service:
        install_launch_agent(port=args.port)
        return
    launch_gui(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
