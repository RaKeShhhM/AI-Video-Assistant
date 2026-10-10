"""Configuration checks run in fresh interpreters and never read real .env keys."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
KEYS = ["AI_ENV_FILE", "AI_SERVICE_SECRET", "EXPRESS_INTERNAL_URL", "MISTRAL_API_KEY", "SARVAM_API_KEY", "WHISPER_MODEL", "SARVAM_STT_MODEL", "UPLOAD_DIR", "DOWNLOAD_DIR", "VECTOR_DB_DIR", "MAX_UPLOAD_MB", "MAX_MEDIA_SECONDS", "MAX_AI_JOBS", "MAX_AI_ASKS"]
SCRIPT = r'''
import os, sys, types
from unittest.mock import Mock
from settings import load_settings
settings = load_settings()
assert settings.whisper_model == os.environ.get("EXPECTED_MODEL", "tiny.en")
assert settings.sarvam_model == "test-model:v1"
assert settings.max_media_seconds == 123
assert "test-provider-key" not in repr(settings)
assert "test-sarvam-key" not in repr(settings)
assert "test-only-secret" not in repr(settings)
# Import actual consumers after bootstrap, replacing heavyweight dependencies.
for name, exports in {
    "whisper": ["load_model"], "pydub": ["AudioSegment"],
    "utils.youtube_network": ["RestrictedYoutubeDL"],
    "utils.chroma_local": ["local_chroma_client"],
    "langchain_chroma": ["Chroma"],
    "langchain_community.embeddings": ["HuggingFaceEmbeddings"],
    "langchain_text_splitters": ["RecursiveCharacterTextSplitter"],
    "langchain_core.documents": ["Document"],
}.items():
    module = types.ModuleType(name)
    for export in exports: setattr(module, export, Mock())
    sys.modules[name] = module
errors = types.ModuleType("chromadb.errors")
errors.NotFoundError = type("NotFoundError", (Exception,), {})
sys.modules["chromadb.errors"] = errors
from core import transcriber, vector_store
from utils import audio_processor, media_limits
assert transcriber.WHISPER_MODEL == settings.whisper_model
assert transcriber.SARVAM_API_KEY == "test-sarvam-key"
assert transcriber.SARVAM_MODEL == settings.sarvam_model
assert audio_processor.DOWNLOAD_DIR == str(settings.download_dir)
assert vector_store.CHROMA_BASE_DIR == str(settings.vector_db_dir)
assert media_limits.MAX_SECONDS == 123
assert not settings.vector_db_dir.exists()
assert not settings.upload_dir.exists()
print("configuration reached consumers")
'''

class SettingsTests(unittest.TestCase):
    def run_config(self, directory, changes=None, script=SCRIPT):
        root = Path(directory)
        config = root / "settings.env"
        config.write_text("\n".join([
            "AI_SERVICE_SECRET=test-only-secret-0123456789abcdef0123456789",
            "EXPRESS_INTERNAL_URL=http://gateway:5000",
            "MISTRAL_API_KEY=test-provider-key", "SARVAM_API_KEY=test-sarvam-key",
            "WHISPER_MODEL=tiny.en", "SARVAM_STT_MODEL=test-model:v1",
            "MAX_MEDIA_SECONDS=123",
            f"UPLOAD_DIR={root.as_posix()}/uploads",
            f"DOWNLOAD_DIR={root.as_posix()}/downloads",
            f"VECTOR_DB_DIR={root.as_posix()}/vectors",
        ]))
        env = {key: value for key, value in os.environ.items() if key not in KEYS}
        env.update({"AI_ENV_FILE": str(config), "PYTHONPATH": str(BASE), **(changes or {})})
        return subprocess.run([sys.executable, "-c", script], cwd=root, env=env,
            capture_output=True, text=True, timeout=15)

    def test_dotenv_only_values_reach_consumers_from_unrelated_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_config(directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_injected_environment_overrides_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_config(directory, {"WHISPER_MODEL": "base", "EXPECTED_MODEL": "base"})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_configuration_fails_before_models_or_storage(self):
        for key, value in [("AI_SERVICE_SECRET", ""), ("MISTRAL_API_KEY", "your_key"), ("SARVAM_API_KEY", "your_key"), ("WHISPER_MODEL", "unknown"), ("MAX_AI_JOBS", "abc"), ("MAX_AI_ASKS", "99"), ("UPLOAD_DIR", "")]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                result = self.run_config(directory, {key: value}, 'from settings import load_settings; load_settings()')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)
                self.assertNotIn("your_key", result.stderr)
                self.assertFalse((Path(directory) / "downloads").exists())

    def test_optional_sarvam_and_separate_storage_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_config(directory, {"SARVAM_API_KEY": ""}, 'from settings import load_settings; assert load_settings().sarvam_api_key is None')
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.run_config(directory, {"VECTOR_DB_DIR": str(Path(directory) / "uploads" / "vectors")}, 'from settings import load_settings; load_settings()')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("non-nested", result.stderr)
