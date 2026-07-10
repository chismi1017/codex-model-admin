from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Optional


DEFAULT_SETTINGS_PATH = Path("/root/.config/codex-model-admin/settings.json")
LANGUAGE_ALIASES = {
    "zh": "zh",
    "cn": "zh",
    "chinese": "zh",
    "中文": "zh",
    "en": "en",
    "english": "en",
}


@dataclass(frozen=True)
class AppSettings:
    language: str = "zh"


def normalize_language(value: str) -> str:
    language = LANGUAGE_ALIASES.get(str(value or "").strip().lower())
    if not language:
        raise ValueError(f"unsupported language: {value}")
    return language


def default_settings_path() -> Path:
    return Path(os.environ.get("CODEX_MODEL_ADMIN_SETTINGS", DEFAULT_SETTINGS_PATH))


class SettingsManager:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else default_settings_path()

    def load(self) -> AppSettings:
        if not self.path.exists():
            return AppSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return AppSettings()
        if not isinstance(data, dict):
            return AppSettings()
        try:
            language = normalize_language(str(data.get("language") or "zh"))
        except ValueError:
            language = "zh"
        return AppSettings(language=language)

    def save(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"language": normalize_language(settings.language)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def set_language(self, language: str) -> AppSettings:
        settings = AppSettings(language=normalize_language(language))
        self.save(settings)
        return settings
