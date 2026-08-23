from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_vault.config import VaultConfig
from knowledge_vault.extractors import extract
from knowledge_vault.models import InboxItem


class ExtractorTests(unittest.TestCase):
    def test_extracts_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "分析.md"; path.write_text("我的完整分析", encoding="utf-8")
            item = InboxItem(path, Path("分析.md"), "hash", path.stat().st_size, datetime.now(timezone.utc), "text")
            result = extract(item)
            self.assertEqual(result.text, "我的完整分析")
            self.assertEqual(result.status, "ok")

    def test_extracts_word_text_and_visual_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "材料.docx"; path.write_bytes(b"doc")
            item = InboxItem(path, Path(path.name), "hash", path.stat().st_size, datetime.now(timezone.utc), "document")

            def fake_run(command, timeout=60):
                from subprocess import CompletedProcess
                if command[0] == "textutil":
                    return CompletedProcess(command, 0, stdout="完整文字", stderr="")
                if command[0] == "soffice":
                    (Path(command[command.index("--outdir") + 1]) / "材料.pdf").write_bytes(b"pdf")
                    return CompletedProcess(command, 0, stdout="", stderr="")
                if command[0] == "pdfinfo":
                    return CompletedProcess(command, 0, stdout="Pages: 1\n", stderr="")
                target = Path(command[-1]).with_suffix(".png"); target.write_bytes(b"png")
                return CompletedProcess(command, 0, stdout="", stderr="")

            with patch("knowledge_vault.extractors._run", side_effect=fake_run):
                result = extract(item, work_dir=root / "work")
            self.assertEqual(result.text, "完整文字")
            self.assertEqual(len(result.media_paths), 1)

    def test_extracts_scanned_pdf_with_local_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "扫描协议.pdf"; path.write_bytes(b"pdf")
            script = root / "脚本" / "vision_ocr.swift"; script.parent.mkdir(); script.write_text("", encoding="utf-8")
            item = InboxItem(path, Path(path.name), "hash", path.stat().st_size, datetime.now(timezone.utc), "pdf")

            def fake_run(command, timeout=60):
                from subprocess import CompletedProcess
                if command[0] == "pdftotext":
                    return CompletedProcess(command, 0, stdout="\f", stderr="")
                if command[0] == "pdfinfo":
                    return CompletedProcess(command, 0, stdout="Pages: 1\n", stderr="")
                if command[0] == "pdftoppm":
                    Path(command[-1]).with_suffix(".png").write_bytes(b"png")
                    return CompletedProcess(command, 0, stdout="", stderr="")
                self.assertEqual(command[:2], ["swift", str(script)])
                return CompletedProcess(command, 0, stdout="本机 OCR 正文", stderr="")

            with patch("knowledge_vault.extractors._run", side_effect=fake_run):
                result = extract(item, project_root=root, work_dir=root / "work")
            self.assertEqual(result.text, "本机 OCR 正文")
            self.assertEqual(result.status, "ok")
            self.assertEqual(len(result.media_paths), 1)

    def test_video_uses_local_whisper_and_returns_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "说明.mp4"; path.write_bytes(b"video")
            item = InboxItem(path, Path(path.name), "hash", path.stat().st_size, datetime.now(timezone.utc), "video")
            cfg = VaultConfig(root, "Asia/Shanghai", (8, 11, 14, 17, 20, 23), whisper_binary=root / "whisper-cli", whisper_model=root / "small.bin")

            def fake_run(command, timeout=60):
                from subprocess import CompletedProcess
                if command[0] == "ffprobe":
                    return CompletedProcess(command, 0, stdout='{"format":{}}', stderr="")
                if command[0] == "ffmpeg" and "-vn" in command:
                    Path(command[-1]).write_bytes(b"wav")
                elif command[0] == "ffmpeg":
                    target = Path(command[-1].replace("%03d", "001")); target.write_bytes(b"jpg")
                else:
                    prefix = Path(command[command.index("-of") + 1]); prefix.with_suffix(".txt").write_text("本地转写", encoding="utf-8")
                return CompletedProcess(command, 0, stdout="", stderr="")

            with patch("knowledge_vault.extractors._run", side_effect=fake_run):
                result = extract(item, config=cfg, work_dir=root / "work")
            self.assertEqual(result.text, "本地转写")
            self.assertEqual(len(result.media_paths), 1)


if __name__ == "__main__": unittest.main()
