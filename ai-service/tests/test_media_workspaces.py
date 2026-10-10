"""Concurrent fixtures exercise real conversion without network/providers."""
import io
import tempfile
import threading
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from utils.audio_processor import MediaWorkspace, process_input, download_youtube_audio

URL = "https://www.youtube.com/watch?v=BaW_jenozKc"

def audio_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 1600)
    return output.getvalue()

class WorkspaceTests(unittest.TestCase):
    def test_concurrent_identical_sources_and_repeated_job_ids_are_isolated(self):
        for ids in [("a" * 24, "b" * 24), ("a" * 24, "a" * 24)]:
            with self.subTest(ids=ids), tempfile.TemporaryDirectory() as directory:
                barrier = threading.Barrier(2)
                class Downloader:
                    def __init__(self, options): self.options = options
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def extract_info(self, url, **kwargs):
                        path = Path(self.options["outtmpl"].replace("%(ext)s", "wav"))
                        path.write_bytes(audio_bytes())
                        barrier.wait(timeout=10)
                        self.options["post_hooks"][0](str(path))
                        return {"title": "identical title", "ext": "webm"}
                with patch("utils.audio_processor.DOWNLOAD_DIR", directory), patch("utils.audio_processor.RestrictedYoutubeDL", Downloader):
                    works = [MediaWorkspace(job_id) for job_id in ids]
                    try:
                        with ThreadPoolExecutor(max_workers=2) as pool:
                            futures = [pool.submit(process_input, URL, source_type="youtube", workspace=work) for work in works]
                            chunks = [f.result(timeout=30) for f in futures]
                        self.assertNotEqual(works[0].path, works[1].path)
                        for job_id, work, result in zip(ids, works, chunks):
                            self.assertIn(f"work-{job_id}-", work.path.name)
                            self.assertTrue((work.path / "source.wav").is_file())
                            self.assertTrue((work.path / "audio.wav").is_file())
                            self.assertEqual(Path(result[0]).parent, work.path)
                            with wave.open(result[0], "rb") as audio:
                                self.assertEqual(audio.readframes(audio.getnframes()), b"\x01\x00" * 1600)
                        retained = Path(chunks[1][0]).read_bytes()
                        works[0].cleanup()
                        self.assertFalse(works[0].path.exists())
                        self.assertEqual(Path(chunks[1][0]).read_bytes(), retained)
                        self.assertTrue((works[1].path / "source.wav").exists())
                        works[0].cleanup()
                    finally:
                        for work in works: work.cleanup()
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_partial_failures_remove_whole_owned_workspace_only(self):
        for stage in ["download_youtube_audio", "convert_to_wav", "chunk_audio"]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory, patch("utils.audio_processor.DOWNLOAD_DIR", directory):
                failed, other = MediaWorkspace("a" * 24), MediaWorkspace("b" * 24)
                sentinel = other.path / "keep.wav"
                sentinel.write_bytes(b"keep")
                def download(*args, **kwargs):
                    path = failed.path / "source.wav"
                    path.write_bytes(audio_bytes())
                    return str(path)
                def fail(*args, **kwargs):
                    (failed.path / "partial.part").write_bytes(b"partial")
                    raise RuntimeError("injected failure")
                with patch("utils.audio_processor.download_youtube_audio", side_effect=download), patch("utils.audio_processor." + stage, side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, "injected failure"):
                        process_input(URL, source_type="youtube", workspace=failed)
                self.assertFalse(failed.path.exists())
                self.assertEqual(sentinel.read_bytes(), b"keep")
                failed.cleanup()
                other.cleanup()

    def test_job_ids_and_downloader_output_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as directory, patch("utils.audio_processor.DOWNLOAD_DIR", directory):
            with self.assertRaises(ValueError): MediaWorkspace("../../escape")
            work = MediaWorkspace("a" * 24)
            outside = Path(directory) / "private.wav"
            outside.write_bytes(audio_bytes())
            with patch("utils.audio_processor.RestrictedYoutubeDL") as factory:
                def extract(*args, **kwargs):
                    factory.call_args.args[0]["post_hooks"][0](str(outside))
                factory.return_value.__enter__.return_value.extract_info.side_effect = extract
                with self.assertRaisesRegex(ValueError, "escaped"):
                    download_youtube_audio(URL, output_dir=work.path)
            work.cleanup()
            self.assertTrue(outside.exists())
    def test_pipeline_cleans_workspace_after_success_and_late_failure(self):
        import importlib.util
        import sys
        import types
        from unittest.mock import Mock
        modules = {}
        exports = {
            "core.transcriber": ["transcribe_all"],
            "core.summarizer": ["summarize", "generate_title"],
            "core.extractor": ["extract_action_items", "extract_key_decisions", "extract_questions"],
            "core.rag_engine": ["build_rag_chain"],
        }
        for name, names in exports.items():
            module = types.ModuleType(name)
            for export in names: setattr(module, export, Mock(return_value="fixture result"))
            modules[name] = module
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location("workspace_test_pipeline", Path(__file__).resolve().parents[1] / "pipeline.py")
            pipeline = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(pipeline)
        for failure in [False, "transcription", "indexing"]:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                uploads = root / "uploads"
                uploads.mkdir()
                source = uploads / "generated.wav"
                source.write_bytes(audio_bytes())
                downloads = root / "downloads"
                seen = []
                def transcribe(chunks, language):
                    workspace = Path(chunks[0]).parent
                    seen.append(workspace)
                    self.assertTrue((workspace / "source.wav").is_file())
                    if failure == "transcription": raise RuntimeError("injected transcription failure")
                    return "fixture transcript"
                with patch.dict("os.environ", {"UPLOAD_DIR": str(uploads)}), patch("utils.audio_processor.DOWNLOAD_DIR", str(downloads)), patch.object(pipeline, "transcribe_all", side_effect=transcribe), patch.object(pipeline, "_post_callback") as callback, patch.object(pipeline.traceback, "print_exc"), patch("builtins.print"):
                    with patch.object(pipeline, "build_rag_chain", side_effect=RuntimeError("indexing failed") if failure == "indexing" else None):
                        succeeded = pipeline.run_pipeline_job("a" * 24, str(source), "upload", "english")
                    self.assertEqual(succeeded, not failure)
                self.assertTrue(seen)
                self.assertFalse(seen[0].exists())
                self.assertFalse(source.exists())
                self.assertEqual(list(downloads.iterdir()), [])
                self.assertEqual(callback.call_args.args[0]["status"], "failed" if failure else "completed")
