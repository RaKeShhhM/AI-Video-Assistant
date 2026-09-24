import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.audio_processor import process_input
from utils.video_source import normalize_youtube_url, validate_video_source

CASES = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/video_sources.json").read_text())


class VideoSourceTests(unittest.TestCase):
    def test_accepted_urls(self):
        for value in CASES["valid"]:
            with self.subTest(value=value):
                self.assertEqual(normalize_youtube_url(value), CASES["canonical"])

    def test_rejected_urls(self):
        for value in CASES["invalid"] + ["x" * 2049]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_youtube_url(value)

    def test_exactly_one_source(self):
        for url, upload in [(None, None), (CASES["canonical"], object()), ("", object())]:
            with self.assertRaises(ValueError):
                validate_video_source(url, upload)
        self.assertEqual(validate_video_source(None, object()), ("upload", None))

    def test_youtube_source_never_becomes_a_local_path(self):
        with patch("utils.audio_processor.convert_to_wav") as convert:
            with self.assertRaises(ValueError):
                process_input("/private/recording.wav", source_type="youtube")
            convert.assert_not_called()

    def test_upload_must_stay_inside_upload_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "uploads"
            root.mkdir()
            external = Path(directory) / "private.wav"
            external.touch()
            internal = root / "generated.wav"
            internal.touch()
            with patch.dict(os.environ, {"UPLOAD_DIR": str(root)}), patch("utils.audio_processor.DOWNLOAD_DIR", directory):
                with patch("utils.audio_processor.convert_to_wav", return_value="converted.wav") as convert:
                    with self.assertRaises(ValueError):
                        process_input(str(external), source_type="upload")
                    convert.assert_not_called()
                    with patch("utils.audio_processor.chunk_audio", return_value=["chunk.wav"]):
                        self.assertEqual(process_input(str(internal), source_type="upload"), ["chunk.wav"])

    def test_source_type_is_required_and_constrained(self):
        with self.assertRaises(TypeError):
            process_input("/private/recording.wav")
        with self.assertRaises(ValueError):
            process_input("/private/recording.wav", source_type="local")
