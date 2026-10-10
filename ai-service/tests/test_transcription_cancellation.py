"""Stop between provider calls without loading Whisper or making network calls."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class TranscriptionCancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        whisper = types.ModuleType("whisper")
        audio = types.ModuleType("pydub")
        audio.AudioSegment = Mock()
        with patch.dict(sys.modules, {"whisper": whisper, "pydub": audio}):
            spec = importlib.util.spec_from_file_location("guarded_transcriber_test", Path(__file__).resolve().parents[1] / "core/transcriber.py")
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def test_cancel_prevents_the_next_transcription_chunk(self):
        check = Mock(side_effect=[None, RuntimeError("cancelled")])
        with patch.object(self.module, "transcribe_chunk", return_value="text") as transcribe:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.module.transcribe_all(["one.wav", "two.wav"], ensure_active=check)
            self.assertEqual(transcribe.call_count, 1)

    def test_cancel_prevents_the_next_sarvam_piece(self):
        class Audio:
            def __len__(self): return 60000
            def __getitem__(self, _): return self
            def export(self, *_args, **_kwargs): pass
        check = Mock(side_effect=[None, RuntimeError("cancelled")])
        with patch.object(self.module, "SARVAM_API_KEY", "test-only"), \
             patch.object(self.module.AudioSegment, "from_wav", return_value=Audio()), \
             patch.object(self.module, "_send_to_sarvam", return_value="text") as send, \
             patch.object(self.module.os.path, "exists", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.module.transcribe_chunk_sarvam("test.wav", ensure_active=check)
            self.assertEqual(send.call_count, 1)
