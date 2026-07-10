from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DB = Path("/root/.cc-switch/cc-switch.db")
DEFAULT_CATALOG = Path("/root/.codex/cc-switch-model-catalog.json")


@dataclass(frozen=True)
class ProviderInfo:
    provider_id: str
    name: str
    base_url: str
    default_model: str
    api_format: str
    model_count: Optional[int]
    current: bool
    read_only: bool = False


@dataclass(frozen=True)
class ModelInfo:
    slug: str
    display_name: str
    context_window: Optional[int]


def _extract_toml_string(text: str, key: str) -> str:
    match = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"', text, flags=re.MULTILINE)
    return match.group(1) if match else ""


class ProviderStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        self.db_path = Path(db_path)

    def list_providers(self) -> List[ProviderInfo]:
        if not self.db_path.exists():
            raise FileNotFoundError(f"cc-switch database not found: {self.db_path}")

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "select id, name, settings_config, is_current from providers where app_type='codex' order by is_current desc, name"
            ).fetchall()
        finally:
            conn.close()

        providers: List[ProviderInfo] = []
        for row in rows:
            provider_id = str(row["id"] or "")
            settings = self._load_settings(row["settings_config"])
            config_text = str(settings.get("config") or "")
            model_catalog = settings.get("modelCatalog")
            if isinstance(model_catalog, dict):
                models = model_catalog.get("models", [])
                model_count = len(models) if isinstance(models, list) else 0
            else:
                model_count = None
            providers.append(
                ProviderInfo(
                    provider_id=provider_id,
                    name=str(row["name"] or provider_id or ""),
                    base_url=_extract_toml_string(config_text, "base_url"),
                    default_model=_extract_toml_string(config_text, "model"),
                    api_format=_extract_toml_string(config_text, "wire_api"),
                    model_count=model_count,
                    current=bool(row["is_current"]),
                    read_only=provider_id == "codex-official",
                )
            )
        return providers

    def _load_settings(self, raw: Any) -> Dict[str, Any]:
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}


class ModelStore:
    def __init__(self, catalog_path: Path = DEFAULT_CATALOG) -> None:
        self.catalog_path = Path(catalog_path)

    def list_models(self) -> List[ModelInfo]:
        if not self.catalog_path.exists():
            raise FileNotFoundError(f"Codex model catalog not found: {self.catalog_path}")

        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        entries = catalog.get("models", [])
        if not isinstance(entries, list):
            return []

        models: List[ModelInfo] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            slug = str(entry.get("slug") or entry.get("model") or "")
            if not slug:
                continue
            models.append(
                ModelInfo(
                    slug=slug,
                    display_name=str(entry.get("display_name") or entry.get("displayName") or slug),
                    context_window=entry.get("context_window") or entry.get("contextWindow"),
                )
            )
        return models
