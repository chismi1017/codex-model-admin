import json
import tempfile
import unittest
from pathlib import Path

from settings import SettingsManager, normalize_language


class SettingsManagerTests(unittest.TestCase):
    def test_load_defaults_to_chinese_when_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = SettingsManager(Path(tmp) / "settings.json").load()

        self.assertEqual(settings.language, "zh")

    def test_save_and_load_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            manager = SettingsManager(path)

            manager.set_language("en")
            settings = manager.load()
            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(settings.language, "en")
        self.assertEqual(data["language"], "en")

    def test_normalize_language_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            normalize_language("fr")


if __name__ == "__main__":
    unittest.main()
