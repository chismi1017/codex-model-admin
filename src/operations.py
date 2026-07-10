from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from backups import BackupManager, DEFAULT_BACKUP_ROOT
from stores import DEFAULT_CATALOG, DEFAULT_DB


DEFAULT_CODEX_CONFIG = Path("/root/.codex/config.toml")
DEFAULT_CODEX_HOME = Path("/root/.codex")
CODEX_OFFICIAL_PROVIDER_ID = "codex-official"
DEFAULT_REASONING_LEVELS = [
    {"effort": "low", "description": "Fast responses with lighter reasoning"},
    {"effort": "medium", "description": "Balances speed and reasoning depth"},
    {"effort": "high", "description": "Greater reasoning depth for complex work"},
]


def set_toml_string(text: str, key: str, value: str) -> str:
    replacement = f'{key} = "{value}"'
    pattern = rf'^\s*{re.escape(key)}\s*=\s*"[^"]*"\s*$'
    if re.search(pattern, text, flags=re.MULTILINE):
        return re.sub(pattern, replacement, text, count=1, flags=re.MULTILINE)
    return replacement + "\n" + text


def extract_toml_string(text: str, key: str) -> str:
    match = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"', text, flags=re.MULTILINE)
    return match.group(1) if match else ""


def slug_to_profile_name(model: str) -> str:
    value = model.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "custom-model"


def build_codex_catalog_model(
    model: str,
    display_name: str,
    context_window: int,
    template: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = dict(template or {})
    base.update(
        {
            "slug": model,
            "display_name": display_name,
            "description": base.get("description") or f"{display_name} routed through cc-switch.",
            "default_reasoning_level": base.get("default_reasoning_level") or "high",
            "supported_reasoning_levels": base.get("supported_reasoning_levels") or list(DEFAULT_REASONING_LEVELS),
            "shell_type": base.get("shell_type") or "shell_command",
            "visibility": "list",
            "supported_in_api": True,
            "priority": base.get("priority", 100),
            "additional_speed_tiers": base.get("additional_speed_tiers") or [],
            "service_tiers": base.get("service_tiers") or [],
            "availability_nux": None,
            "upgrade": None,
            "base_instructions": base.get("base_instructions")
            or "You are Codex, a coding agent. Follow the user's instructions and use tools carefully.",
            "model_messages": base.get("model_messages") or {},
            "supports_reasoning_summaries": base.get("supports_reasoning_summaries", True),
            "default_reasoning_summary": base.get("default_reasoning_summary") or "none",
            "support_verbosity": base.get("support_verbosity", True),
            "default_verbosity": base.get("default_verbosity") or "low",
            "apply_patch_tool_type": base.get("apply_patch_tool_type") or "freeform",
            "web_search_tool_type": base.get("web_search_tool_type") or "text_and_image",
            "truncation_policy": {"mode": "tokens", "limit": context_window},
            "supports_parallel_tool_calls": base.get("supports_parallel_tool_calls", True),
            "supports_image_detail_original": base.get("supports_image_detail_original", True),
            "context_window": context_window,
            "max_context_window": max(int(base.get("max_context_window") or 0), context_window),
            "effective_context_window_percent": base.get("effective_context_window_percent", 100),
            "experimental_supported_tools": base.get("experimental_supported_tools") or [],
            "input_modalities": base.get("input_modalities") or ["text"],
            "supports_search_tool": base.get("supports_search_tool", False),
            "use_responses_lite": base.get("use_responses_lite", False),
        }
    )
    return base


class CodexConfigManager:
    def __init__(self, config_path: Path = DEFAULT_CODEX_CONFIG) -> None:
        self.config_path = Path(config_path)

    def set_default_model(self, model: str) -> None:
        text = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        updated = set_toml_string(text, "model", model).rstrip() + "\n"
        self.config_path.write_text(updated, encoding="utf-8")
        try:
            self.config_path.chmod(0o600)
        except OSError:
            pass

    def set_base_url(self, base_url: str) -> None:
        text = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        updated = set_toml_string(text, "base_url", base_url).rstrip() + "\n"
        self.config_path.write_text(updated, encoding="utf-8")
        try:
            self.config_path.chmod(0o600)
        except OSError:
            pass


class CatalogManager:
    def __init__(self, catalog_path: Path = DEFAULT_CATALOG) -> None:
        self.catalog_path = Path(catalog_path)

    def load(self) -> Dict[str, Any]:
        if not self.catalog_path.exists():
            return {"models": []}
        value = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"models": []}

    def save(self, catalog: Dict[str, Any]) -> None:
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            self.catalog_path.chmod(0o600)
        except OSError:
            pass

    def add_model(self, model: str, display_name: str, context_window: int, template: Optional[str] = None) -> None:
        catalog = self.load()
        models = catalog.setdefault("models", [])
        if not isinstance(models, list):
            models = []
            catalog["models"] = models
        base = build_codex_catalog_model(model, display_name, context_window, self._template_model(models, template))
        catalog["models"] = [entry for entry in models if (entry.get("slug") or entry.get("model")) != model]
        catalog["models"].append(base)
        self.save(catalog)

    def delete_model(self, model: str) -> None:
        catalog = self.load()
        models = catalog.get("models", [])
        if isinstance(models, list):
            catalog["models"] = [entry for entry in models if (entry.get("slug") or entry.get("model")) != model]
        self.save(catalog)

    def _template_model(self, models: List[Dict[str, Any]], template: Optional[str]) -> Dict[str, Any]:
        if template:
            for entry in models:
                if (entry.get("slug") or entry.get("model")) == template:
                    return dict(entry)
            raise ValueError(f"template model not found: {template}")
        if models:
            return dict(models[0])
        return {
            "slug": "",
            "display_name": "",
            "context_window": 128000,
        }


class ProviderManager:
    def __init__(self, db_path: Path = DEFAULT_DB, provider_root: Optional[Path] = None) -> None:
        self.db_path = Path(db_path)
        self.provider_root = Path(provider_root) if provider_root is not None else self.db_path.parent

    def provider_snapshot_path(self, provider_id: str) -> Path:
        name = f"{provider_id}-provider.json"
        if Path(name).name != name:
            raise ValueError(f"invalid provider id for snapshot: {provider_id}")
        return self.provider_root / name

    def write_provider_snapshot(self, provider_id: str, settings: Dict[str, Any]) -> Path:
        path = self.provider_snapshot_path(provider_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def load_settings(self, provider_id: str) -> Dict[str, Any]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "select settings_config from providers where app_type='codex' and id=?",
                (provider_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ValueError(f"provider not found: {provider_id}")
        try:
            value = json.loads(row["settings_config"] or "{}")
        except json.JSONDecodeError:
            value = {}
        return value if isinstance(value, dict) else {}

    def save_settings(self, provider_id: str, settings: Dict[str, Any]) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "update providers set settings_config=? where app_type='codex' and id=?",
                (json.dumps(settings, ensure_ascii=False), provider_id),
            )
            conn.commit()
        finally:
            conn.close()
        self.write_provider_snapshot(provider_id, settings)

    def is_current_provider(self, provider_id: str) -> bool:
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "select is_current from providers where app_type='codex' and id=?",
                (provider_id,),
            ).fetchone()
        finally:
            conn.close()
        return bool(row and row[0])

    def add_provider(
        self,
        provider_id: str,
        name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        context_window: int,
        api_format: str = "responses",
        switch: bool = False,
    ) -> None:
        settings = self._build_settings(name, base_url, api_key, default_model, context_window, api_format)
        conn = sqlite3.connect(str(self.db_path))
        try:
            columns = [row[1] for row in conn.execute("pragma table_info(providers)").fetchall()]
            values: Dict[str, Any] = {
                "id": provider_id,
                "app_type": "codex",
                "name": name,
                "settings_config": json.dumps(settings, ensure_ascii=False),
                "website_url": "",
                "category": "",
                "created_at": int(time.time()),
                "sort_index": 0,
                "notes": "",
                "icon": "",
                "icon_color": "",
                "meta": "{}",
                "is_current": 1 if switch else 0,
                "in_failover_queue": 0,
                "cost_multiplier": "1.0",
                "limit_daily_usd": None,
                "limit_monthly_usd": None,
                "provider_type": "custom",
            }
            if switch:
                conn.execute("update providers set is_current=0 where app_type='codex'")
            insert_columns = [column for column in columns if column in values]
            placeholders = ", ".join("?" for _ in insert_columns)
            conn.execute(
                f"insert or replace into providers ({', '.join(insert_columns)}) values ({placeholders})",
                [values[column] for column in insert_columns],
            )
            conn.commit()
        finally:
            conn.close()
        self.write_provider_snapshot(provider_id, settings)

    def delete_provider(self, provider_id: str, force: bool = False) -> None:
        self.ensure_deletable(provider_id)
        snapshot_path = self.provider_snapshot_path(provider_id)
        conn = sqlite3.connect(str(self.db_path))
        try:
            current = conn.execute(
                "select is_current from providers where app_type='codex' and id=?",
                (provider_id,),
            ).fetchone()
            if current and current[0] and not force:
                raise ValueError("refusing to delete current provider without force")
            conn.execute("delete from providers where app_type='codex' and id=?", (provider_id,))
            conn.commit()
        finally:
            conn.close()
        if snapshot_path.exists():
            snapshot_path.unlink()

    def update_provider(
        self,
        provider_id: str,
        name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        context_window: int,
        api_format: str = "responses",
        new_provider_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_provider_id = (new_provider_id or provider_id).strip()
        if not target_provider_id:
            raise ValueError("provider id is required")
        if target_provider_id == CODEX_OFFICIAL_PROVIDER_ID:
            raise ValueError("OpenAI Official 是 Codex 官方动态 provider，不可用作自定义 provider ID。")
        self.provider_snapshot_path(target_provider_id)

        settings = self.load_settings(provider_id)
        self.ensure_switchable(provider_id, settings)
        updated = dict(settings)
        config_text = str(updated.get("config") or self._build_settings(name, base_url, api_key, default_model, context_window, api_format)["config"])
        config_text = set_toml_string(config_text, "model", default_model)
        config_text = set_toml_string(config_text, "base_url", base_url)
        config_text = set_toml_string(config_text, "wire_api", api_format)

        auth = dict(updated.get("auth") if isinstance(updated.get("auth"), dict) else {})
        auth["OPENAI_API_KEY"] = api_key

        model_catalog = dict(updated.get("modelCatalog") if isinstance(updated.get("modelCatalog"), dict) else {})
        models = model_catalog.get("models", [])
        if not isinstance(models, list):
            models = []
        next_models: List[Dict[str, Any]] = []
        found_default = False
        for entry in models:
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            model_id = str(item.get("model") or item.get("id") or "").strip()
            if model_id == default_model:
                item["model"] = default_model
                item["displayName"] = str(item.get("displayName") or default_model)
                item["contextWindow"] = context_window
                found_default = True
            next_models.append(item)
        if not found_default:
            next_models.append({"model": default_model, "displayName": default_model, "contextWindow": context_window})
        model_catalog["models"] = next_models

        updated["auth"] = auth
        updated["config"] = config_text
        updated["modelCatalog"] = model_catalog
        updated["name"] = name

        conn = sqlite3.connect(str(self.db_path))
        try:
            if target_provider_id != provider_id:
                existing = conn.execute(
                    "select 1 from providers where app_type='codex' and id=?",
                    (target_provider_id,),
                ).fetchone()
                if existing:
                    raise ValueError(f"provider already exists: {target_provider_id}")
            result = conn.execute(
                "update providers set id=?, name=?, settings_config=? where app_type='codex' and id=?",
                (target_provider_id, name, json.dumps(updated, ensure_ascii=False), provider_id),
            )
            conn.commit()
        finally:
            conn.close()
        if result.rowcount == 0:
            raise ValueError(f"provider not found: {provider_id}")
        self.write_provider_snapshot(target_provider_id, updated)
        if target_provider_id != provider_id:
            old_snapshot_path = self.provider_snapshot_path(provider_id)
            if old_snapshot_path.exists():
                old_snapshot_path.unlink()
        return updated

    def switch_provider(self, provider_id: str) -> Dict[str, Any]:
        settings = self.load_settings(provider_id)
        self.ensure_switchable(provider_id, settings)
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("update providers set is_current=0 where app_type='codex'")
            conn.execute(
                "update providers set is_current=1 where app_type='codex' and id=?",
                (provider_id,),
            )
            conn.commit()
        finally:
            conn.close()
        self.write_provider_snapshot(provider_id, settings)
        return settings

    def ensure_deletable(self, provider_id: str) -> None:
        if provider_id == CODEX_OFFICIAL_PROVIDER_ID:
            raise ValueError("OpenAI Official 是 Codex 官方动态 provider，不可由本工具删除。")

    def ensure_switchable(self, provider_id: str, settings: Optional[Dict[str, Any]] = None) -> None:
        if provider_id == CODEX_OFFICIAL_PROVIDER_ID:
            raise ValueError("OpenAI Official 是 Codex 官方动态 provider，不可由本工具切换；请使用专门的恢复官方模式。")
        value = self.load_settings(provider_id) if settings is None else settings
        if not isinstance(value.get("modelCatalog"), dict):
            raise ValueError("provider 缺少 modelCatalog，不能通过本工具切换。")

    def add_model_to_provider(self, provider_id: str, model: str, display_name: str, context_window: int) -> None:
        settings = self.load_settings(provider_id)
        model_catalog = settings.setdefault("modelCatalog", {})
        models = model_catalog.setdefault("models", [])
        model_catalog["models"] = [entry for entry in models if entry.get("model") != model]
        model_catalog["models"].append(
            {"model": model, "displayName": display_name, "contextWindow": context_window}
        )
        self.save_settings(provider_id, settings)

    def delete_model_from_provider(self, provider_id: str, model: str) -> None:
        settings = self.load_settings(provider_id)
        model_catalog = settings.setdefault("modelCatalog", {})
        models = model_catalog.setdefault("models", [])
        model_catalog["models"] = [entry for entry in models if entry.get("model") != model]
        self.save_settings(provider_id, settings)

    def _build_settings(
        self,
        name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        context_window: int,
        api_format: str,
    ) -> Dict[str, Any]:
        config = "\n".join(
            [
                'model_provider = "custom"',
                f'model = "{default_model}"',
                'model_reasoning_effort = "high"',
                "disable_response_storage = true",
                'model_catalog_json = "cc-switch-model-catalog.json"',
                "",
                "[model_providers]",
                "[model_providers.custom]",
                'name = "custom"',
                "use_response_api = false",
                "requires_openai_auth = true",
                f'base_url = "{base_url}"',
                f'wire_api = "{api_format}"',
                "",
            ]
        )
        return {
            "auth": {"OPENAI_API_KEY": api_key},
            "config": config,
            "modelCatalog": {
                "models": [
                    {
                        "model": default_model,
                        "displayName": default_model,
                        "contextWindow": context_window,
                    }
                ]
            },
            "name": name,
        }


class ModelManager:
    def __init__(
        self,
        provider_manager: ProviderManager,
        catalog_manager: CatalogManager,
        config_manager: CodexConfigManager,
        codex_home: Path = DEFAULT_CODEX_HOME,
    ) -> None:
        self.provider_manager = provider_manager
        self.catalog_manager = catalog_manager
        self.config_manager = config_manager
        self.codex_home = Path(codex_home)

    def set_default(self, model: str) -> None:
        self.config_manager.set_default_model(model)

    def add(
        self,
        provider_id: str,
        model: str,
        display_name: str,
        context_window: int,
        template: Optional[str] = None,
        profile: Optional[str] = None,
        make_default: bool = False,
    ) -> None:
        self.provider_manager.add_model_to_provider(provider_id, model, display_name, context_window)
        self.catalog_manager.add_model(model, display_name, context_window, template)
        if profile is not None:
            self.write_profile(profile or slug_to_profile_name(model), model)
        if make_default:
            self.set_default(model)

    def delete(self, provider_id: str, model: str, profile: Optional[str] = None, force: bool = False) -> None:
        catalog = self.catalog_manager.load()
        models = catalog.get("models", [])
        if isinstance(models, list) and len(models) <= 1 and not force:
            raise ValueError("refusing to delete the last model without force")
        self.provider_manager.delete_model_from_provider(provider_id, model)
        self.catalog_manager.delete_model(model)
        if profile:
            profile_path = self.codex_home / f"{profile}.config.toml"
            if profile_path.exists():
                profile_path.unlink()

    def write_profile(self, profile_name: str, model: str) -> Path:
        self.codex_home.mkdir(parents=True, exist_ok=True)
        profile_path = self.codex_home / f"{profile_name}.config.toml"
        profile_path.write_text(f'model = "{model}"\n', encoding="utf-8")
        try:
            profile_path.chmod(0o600)
        except OSError:
            pass
        return profile_path


def backup_for_write(paths: Iterable[Path], reason: str, backup_root: Path = DEFAULT_BACKUP_ROOT) -> str:
    manifest = BackupManager(backup_root=backup_root, sources=list(paths)).create(reason)
    return manifest.backup_id
