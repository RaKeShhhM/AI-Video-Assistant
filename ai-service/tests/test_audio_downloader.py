import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.audio_processor import download_youtube_audio


class AudioDownloaderTests(unittest.TestCase):
    def test_enables_node_and_returns_final_postprocessed_path(self):
        with tempfile.TemporaryDirectory() as directory:
            final_path = Path(directory) / "audio.wav"
            final_path.touch()
            with patch("utils.audio_processor.RestrictedYoutubeDL") as factory:
                downloader = factory.return_value.__enter__.return_value

                def extract_info(url, download, ie_key):
                    options = factory.call_args.args[0]
                    options["post_hooks"][0](str(final_path))
                    # Pre-conversion metadata must not determine the WAV path.
                    return {"ext": "mp4"}

                downloader.extract_info.side_effect = extract_info
                result = download_youtube_audio("https://www.youtube.com/watch?v=BaW_jenozKc")

                self.assertEqual(result, str(final_path))
                options = factory.call_args.args[0]
                self.assertIn("node", options["js_runtimes"])
                self.assertTrue(options["noplaylist"])
                downloader.extract_info.assert_called_once_with(
                    "https://www.youtube.com/watch?v=BaW_jenozKc", download=True, ie_key="Youtube"
                )

    def test_missing_output_fails_instead_of_returning_guessed_path(self):
        with patch("utils.audio_processor.RestrictedYoutubeDL"):
            with self.assertRaisesRegex(RuntimeError, "did not produce an audio file"):
                download_youtube_audio("https://www.youtube.com/watch?v=BaW_jenozKc")

    def test_download_errors_are_not_hidden(self):
        with patch("utils.audio_processor.RestrictedYoutubeDL") as factory:
            downloader = factory.return_value.__enter__.return_value
            downloader.extract_info.side_effect = RuntimeError("HTTP Error 403")
            with self.assertRaisesRegex(RuntimeError, "HTTP Error 403"):
                download_youtube_audio("https://www.youtube.com/watch?v=BaW_jenozKc")


if __name__ == "__main__":
    unittest.main()
