from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess

from .config import VaultConfig
from .models import ExtractedContent, InboxItem


class _TextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}: self._ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._ignored: self._ignored -= 1

    def handle_data(self, data):
        if not self._ignored and data.strip(): self.parts.append(data.strip())


def _run(command: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)


def _representative_pages(total: int, maximum: int = 12) -> list[int]:
    if total <= maximum:
        return list(range(1, total + 1))
    fixed = [1, 2, 3, 4, total - 1, total]
    remaining = maximum - len(fixed)
    middle = [round(5 + index * (total - 7) / max(1, remaining - 1)) for index in range(remaining)]
    return sorted(set(max(1, min(total, value)) for value in fixed + middle))[:maximum]


def _render_pdf_pages(path: Path, output: Path) -> tuple[Path, ...]:
    output.mkdir(parents=True, exist_ok=True)
    info = _run(["pdfinfo", str(path)])
    match = re.search(r"^Pages:\s+(\d+)", info.stdout, re.MULTILINE)
    total = int(match.group(1)) if match else 1
    rendered: list[Path] = []
    for page in _representative_pages(total):
        target = output / f"page-{page:04d}"
        result = _run(["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-png", "-r", "120", str(path), str(target)], timeout=120)
        png = target.with_suffix(".png")
        if result.returncode == 0 and png.exists():
            rendered.append(png)
    return tuple(rendered)


def _normalized_image(path: Path, output: Path) -> tuple[Path, ...]:
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        return (path,)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{path.stem}.jpg"
    result = _run(["sips", "-s", "format", "jpeg", str(path), "--out", str(target)], timeout=120)
    return (target,) if result.returncode == 0 and target.exists() else ()


def _ocr_images(paths: tuple[Path, ...], project_root: Path | None) -> tuple[str, str]:
    """Return locally recognized text for image-only documents.

    Vision OCR stays local.  A page which cannot be recognized is retained as
    visual evidence, while the caller receives the usable text from the other
    pages rather than treating an image-only PDF as an empty document.
    """
    if project_root is None:
        return "", "project_root required for Vision OCR"
    script = project_root / "脚本" / "vision_ocr.swift"
    parts: list[str] = []
    errors: list[str] = []
    for page in paths:
        result = _run(["swift", str(script), str(page)], timeout=120)
        if result.returncode == 0 and result.stdout.strip():
            parts.append(result.stdout.strip())
        elif result.returncode != 0:
            errors.append(result.stderr.strip() or f"Vision OCR failed: {page.name}")
    if parts:
        return "\n\n".join(parts), ""
    return "", errors[0] if errors else "Vision OCR produced no text"


def _transcribe(path: Path, output: Path, config: VaultConfig) -> tuple[str, str]:
    output.mkdir(parents=True, exist_ok=True)
    prefix = output / "transcript"
    result = _run([
        str(config.whisper_binary), "-m", str(config.whisper_model), "-f", str(path),
        "-l", "auto", "-otxt", "-of", str(prefix),
    ], timeout=1800)
    transcript = prefix.with_suffix(".txt")
    if result.returncode != 0 or not transcript.exists():
        return "", result.stderr.strip() or "whisper-cli failed"
    return transcript.read_text(encoding="utf-8", errors="replace"), ""


def extract(
    item: InboxItem,
    project_root: Path | None = None,
    *,
    config: VaultConfig | None = None,
    work_dir: Path | None = None,
) -> ExtractedContent:
    path = item.path
    suffix = path.suffix.lower()
    try:
        if item.kind == "text":
            raw = path.read_text(encoding="utf-8", errors="replace")
            if suffix in {".html", ".htm"}:
                parser = _TextHTMLParser(); parser.feed(raw)
                raw = "\n".join(parser.parts)
            return ExtractedContent(path, item.kind, text=raw)
        if item.kind == "pdf":
            result = _run(["pdftotext", "-layout", str(path), "-"])
            if result.returncode != 0:
                return ExtractedContent(path, item.kind, status="deferred", reason=result.stderr.strip() or "pdftotext failed")
            media = _render_pdf_pages(path, (work_dir or path.parent / ".extracted") / path.stem) if work_dir else ()
            text = result.stdout.strip()
            if not text:
                text, error = _ocr_images(media, project_root)
                if not text:
                    return ExtractedContent(path, item.kind, media_paths=media, status="deferred", reason=error)
            return ExtractedContent(path, item.kind, text=text, media_paths=media)
        if item.kind == "document":
            text_result = _run(["textutil", "-convert", "txt", "-stdout", str(path)], timeout=120)
            if text_result.returncode != 0:
                return ExtractedContent(path, item.kind, status="deferred", reason=text_result.stderr.strip() or "textutil failed")
            media: tuple[Path, ...] = ()
            if work_dir:
                converted = work_dir / path.stem
                converted.mkdir(parents=True, exist_ok=True)
                pdf_result = _run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(converted), str(path)], timeout=180)
                pdf = converted / f"{path.stem}.pdf"
                if pdf_result.returncode == 0 and pdf.exists():
                    media = _render_pdf_pages(pdf, converted / "pages")
            return ExtractedContent(path, item.kind, text=text_result.stdout, media_paths=media)
        if item.kind == "image":
            if project_root is None:
                return ExtractedContent(path, item.kind, status="deferred", reason="project_root required for Vision OCR")
            script = project_root / "脚本" / "vision_ocr.swift"
            result = _run(["swift", str(script), str(path)], timeout=120)
            if result.returncode != 0:
                return ExtractedContent(path, item.kind, status="deferred", reason=result.stderr.strip() or "Vision OCR failed")
            media = _normalized_image(path, (work_dir or path.parent / ".extracted") / path.stem)
            return ExtractedContent(path, item.kind, text=result.stdout, media_paths=media)
        if item.kind in {"audio", "video"}:
            result = _run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)])
            metadata = json.loads(result.stdout) if result.returncode == 0 and result.stdout else {}
            if config is None or work_dir is None:
                return ExtractedContent(path, item.kind, metadata=metadata, status="deferred", reason="local Whisper configuration required")
            item_work = work_dir / path.stem
            item_work.mkdir(parents=True, exist_ok=True)
            audio = path
            media: tuple[Path, ...] = ()
            if item.kind == "video":
                audio = item_work / "audio.wav"
                audio_result = _run(["ffmpeg", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(audio)], timeout=600)
                if audio_result.returncode != 0:
                    return ExtractedContent(path, item.kind, metadata=metadata, status="deferred", reason=audio_result.stderr.strip()[-500:] or "ffmpeg audio failed")
                frames = item_work / "frames"
                frames.mkdir(parents=True, exist_ok=True)
                frame_result = _run([
                    "ffmpeg", "-y", "-i", str(path), "-vf", "select='eq(pict_type,I)',scale=1280:-2",
                    "-vsync", "vfr", "-frames:v", "12", str(frames / "frame-%03d.jpg"),
                ], timeout=600)
                if frame_result.returncode == 0:
                    media = tuple(sorted(frames.glob("frame-*.jpg")))[:12]
            text, error = _transcribe(audio, item_work, config)
            if error:
                return ExtractedContent(path, item.kind, metadata=metadata, media_paths=media, status="deferred", reason=error)
            return ExtractedContent(path, item.kind, text=text, metadata=metadata, media_paths=media)
        return ExtractedContent(path, item.kind, metadata={"size": item.size}, status="deferred", reason="unsupported binary type")
    except Exception as exc:
        return ExtractedContent(path, item.kind, status="deferred", reason=str(exc))
