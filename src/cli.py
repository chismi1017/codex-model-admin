from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import List, Optional

from backups import BackupManager, DEFAULT_BACKUP_ROOT
from commands import Runner
from installers import InstallerManager
from operations import (
    CatalogManager,
    CodexConfigManager,
    ModelManager,
    ProviderManager,
    backup_for_write,
    build_codex_catalog_model,
    extract_toml_string,
)
from preflight import PreflightManager
from proxy import DEFAULT_CODEX_CONFIG, DEFAULT_SERVICE, DEFAULT_SERVICE_PATH, ProxyManager
from rendering import render_backups, render_doctor, render_models, render_providers, render_proxy_status
from stores import DEFAULT_CATALOG, DEFAULT_DB, ModelStore, ProviderStore


def write_text(text: str, stream=None) -> None:
    stream = stream or sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
                stream.write(text)
                return
            except (OSError, UnicodeEncodeError, ValueError):
                pass
        stream.write(text.encode("ascii", errors="replace").decode("ascii"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-model-admin")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("doctor", help="检查本机运行环境")

    install = subcommands.add_parser("install", help="安装缺失组件")
    install.add_argument("target", choices=["codex", "cc-switch", "all"])
    install.add_argument("-y", "--yes", action="store_true", help="确认安装")

    provider = subcommands.add_parser("provider", help="管理供应商")
    provider_subcommands = provider.add_subparsers(dest="provider_command", required=True)
    provider_list = provider_subcommands.add_parser("list", help="列出供应商")
    provider_list.add_argument("--db", default=str(DEFAULT_DB), help="cc-switch SQLite 数据库路径")
    provider_add = provider_subcommands.add_parser("add", help="新增供应商")
    provider_add.add_argument("provider_id")
    provider_add.add_argument("--name", required=True)
    provider_add.add_argument("--base-url", required=True)
    provider_add.add_argument("--api-key", required=True)
    provider_add.add_argument("--default-model", required=True)
    provider_add.add_argument("--context-window", type=int, default=128000)
    provider_add.add_argument("--api-format", default="responses", choices=["responses", "chat"])
    provider_add.add_argument("--switch", action="store_true")
    provider_add.add_argument("--db", default=str(DEFAULT_DB))
    provider_add.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    provider_add.add_argument("--config", default=str(DEFAULT_CODEX_CONFIG))
    provider_add.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    provider_add.add_argument("--yes", action="store_true")
    provider_update = provider_subcommands.add_parser("update", help="修改供应商")
    provider_update.add_argument("provider_id")
    provider_update.add_argument("--new-id")
    provider_update.add_argument("--name", required=True)
    provider_update.add_argument("--base-url", required=True)
    provider_update.add_argument("--api-key", required=True)
    provider_update.add_argument("--default-model", required=True)
    provider_update.add_argument("--context-window", type=int, default=128000)
    provider_update.add_argument("--api-format", default="responses", choices=["responses", "chat"])
    provider_update.add_argument("--sync-current", action="store_true")
    provider_update.add_argument("--restart", action="store_true")
    provider_update.add_argument("--db", default=str(DEFAULT_DB))
    provider_update.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    provider_update.add_argument("--config", default=str(DEFAULT_CODEX_CONFIG))
    provider_update.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    provider_update.add_argument("--yes", action="store_true")
    provider_delete = provider_subcommands.add_parser("delete", help="删除供应商")
    provider_delete.add_argument("provider_id")
    provider_delete.add_argument("--db", default=str(DEFAULT_DB))
    provider_delete.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    provider_delete.add_argument("--force", action="store_true")
    provider_delete.add_argument("--yes", action="store_true")
    provider_switch = provider_subcommands.add_parser("switch", help="切换供应商")
    provider_switch.add_argument("provider_id")
    provider_switch.add_argument("--db", default=str(DEFAULT_DB))
    provider_switch.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    provider_switch.add_argument("--config", default=str(DEFAULT_CODEX_CONFIG))
    provider_switch.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    provider_switch.add_argument("--no-restart", action="store_true")
    provider_switch.add_argument("--yes", action="store_true")

    model = subcommands.add_parser("model", help="管理模型")
    model_subcommands = model.add_subparsers(dest="model_command", required=True)
    model_list = model_subcommands.add_parser("list", help="列出模型")
    model_list.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="Codex model catalog 路径")
    model_list.add_argument("--db", default=str(DEFAULT_DB), help="cc-switch SQLite 数据库路径")
    model_sync = model_subcommands.add_parser("sync-current", help="同步当前供应商模型到 Codex catalog")
    model_sync.add_argument("--db", default=str(DEFAULT_DB))
    model_sync.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    model_sync.add_argument("--config", default=str(DEFAULT_CODEX_CONFIG))
    model_sync.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    model_sync.add_argument("--yes", action="store_true")
    model_set_default = model_subcommands.add_parser("set-default", help="设置默认模型")
    model_set_default.add_argument("model")
    model_set_default.add_argument("--config", default=str(DEFAULT_CODEX_CONFIG))
    model_set_default.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    model_set_default.add_argument("--yes", action="store_true")
    model_add = model_subcommands.add_parser("add", help="新增模型")
    model_add.add_argument("model")
    model_add.add_argument("--display-name")
    model_add.add_argument("--context-window", type=int, default=128000)
    model_add.add_argument("--provider-id", default="example-provider")
    model_add.add_argument("--db", default=str(DEFAULT_DB))
    model_add.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    model_add.add_argument("--config", default=str(DEFAULT_CODEX_CONFIG))
    model_add.add_argument("--codex-home", default="/root/.codex")
    model_add.add_argument("--template")
    model_add.add_argument("--profile", nargs="?", const="")
    model_add.add_argument("--default", action="store_true")
    model_add.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    model_add.add_argument("--yes", action="store_true")
    model_delete = model_subcommands.add_parser("delete", help="删除模型")
    model_delete.add_argument("model")
    model_delete.add_argument("--provider-id", default="example-provider")
    model_delete.add_argument("--db", default=str(DEFAULT_DB))
    model_delete.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    model_delete.add_argument("--config", default=str(DEFAULT_CODEX_CONFIG))
    model_delete.add_argument("--codex-home", default="/root/.codex")
    model_delete.add_argument("--profile")
    model_delete.add_argument("--force", action="store_true")
    model_delete.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    model_delete.add_argument("--yes", action="store_true")

    backup = subcommands.add_parser("backup", help="管理备份")
    backup_subcommands = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_subcommands.add_parser("create", help="创建备份")
    backup_create.add_argument("--reason", default="manual", help="备份原因")
    backup_create.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT), help="备份根目录")
    backup_create.add_argument("--source", action="append", help="额外指定备份源；出现时覆盖默认源")
    backup_list = backup_subcommands.add_parser("list", help="列出备份")
    backup_list.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT), help="备份根目录")
    backup_restore = backup_subcommands.add_parser("restore", help="恢复备份")
    backup_restore.add_argument("backup_id")
    backup_restore.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT), help="备份根目录")
    backup_restore.add_argument("-y", "--yes", action="store_true", help="确认恢复")
    backup_delete = backup_subcommands.add_parser("delete", help="删除备份")
    backup_delete.add_argument("backup_id", nargs="+")
    backup_delete.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT), help="备份根目录")
    backup_delete.add_argument("-y", "--yes", action="store_true", help="确认删除")

    proxy = subcommands.add_parser("proxy", help="管理代理")
    proxy_subcommands = proxy.add_subparsers(dest="proxy_command", required=True)
    proxy_status = proxy_subcommands.add_parser("status", help="查看代理状态")
    proxy_status.add_argument("--service", default=DEFAULT_SERVICE)
    proxy_set = proxy_subcommands.add_parser("set", help="设置代理监听地址和端口")
    proxy_set.add_argument("--listen-address", required=True)
    proxy_set.add_argument("--listen-port", required=True, type=int)
    proxy_set.add_argument("--service", default=DEFAULT_SERVICE)
    proxy_set.add_argument("--service-path", default=str(DEFAULT_SERVICE_PATH))
    proxy_set.add_argument("--config", default=str(DEFAULT_CODEX_CONFIG))
    proxy_set.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    proxy_set.add_argument("--restart", action="store_true")
    proxy_set.add_argument("--yes", action="store_true")
    proxy_restart = proxy_subcommands.add_parser("restart", help="重启代理")
    proxy_restart.add_argument("--service", default=DEFAULT_SERVICE)
    proxy_restart.add_argument("--yes", action="store_true")
    proxy_logs = proxy_subcommands.add_parser("logs", help="查看代理日志")
    proxy_logs.add_argument("--service", default=DEFAULT_SERVICE)
    proxy_logs.add_argument("-n", "--lines", type=int, default=100)
    proxy_test = proxy_subcommands.add_parser("test", help="测试代理")
    proxy_test.add_argument("--model", required=True)
    proxy_test.add_argument("--base-url", default="http://127.0.0.1:15721/v1")

    tui = subcommands.add_parser("tui", help="启动交互菜单")
    tui.add_argument("--lang", choices=["zh", "en"], help="临时指定 TUI 语言")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runner = Runner()

    if args.command == "doctor":
        results = PreflightManager(runner).check_all()
        write_text(render_doctor(results))
        return 0

    if args.command == "install":
        if not args.yes:
            print("需要确认：请添加 --yes 执行安装。")
            return 2
        installer = InstallerManager(runner)
        if args.target == "codex":
            print(installer.install_codex(confirm=True))
            return 0
        if args.target == "cc-switch":
            print(installer.install_cc_switch(confirm=True))
            return 0
        print("codex:", installer.install_codex(confirm=True))
        print("cc-switch:", installer.install_cc_switch(confirm=True))
        return 0

    if args.command == "provider" and args.provider_command == "list":
        providers = ProviderStore(Path(args.db)).list_providers()
        write_text(render_providers(providers))
        return 0

    if args.command == "provider" and args.provider_command == "add":
        if not args.yes:
            print("需要确认：请添加 --yes 执行新增供应商。")
            return 2
        provider_manager = ProviderManager(Path(args.db))
        sources = [Path(args.db), Path(args.catalog), Path(args.config), provider_manager.provider_snapshot_path(args.provider_id)]
        backup_id = backup_for_write(sources, f"before-provider-add-{args.provider_id}", Path(args.backup_root))
        provider_manager.add_provider(
            provider_id=args.provider_id,
            name=args.name,
            base_url=args.base_url,
            api_key=args.api_key,
            default_model=args.default_model,
            context_window=args.context_window,
            api_format=args.api_format,
            switch=args.switch,
        )
        if args.switch:
            CatalogManager(Path(args.catalog)).add_model(args.default_model, args.default_model, args.context_window)
            CodexConfigManager(Path(args.config)).set_default_model(args.default_model)
            _sync_codex_proxy_config(runner, Path(args.config))
        print(f"供应商已新增: {args.provider_id}")
        print(f"备份: {backup_id}")
        return 0

    if args.command == "provider" and args.provider_command == "update":
        if not args.yes:
            print("需要确认：请添加 --yes 执行修改供应商。")
            return 2
        provider_manager = ProviderManager(Path(args.db))
        try:
            provider_manager.ensure_switchable(args.provider_id)
        except ValueError as exc:
            print(exc)
            return 2
        new_provider_id = args.new_id or args.provider_id
        try:
            old_snapshot_path = provider_manager.provider_snapshot_path(args.provider_id)
            new_snapshot_path = provider_manager.provider_snapshot_path(new_provider_id)
        except ValueError as exc:
            print(exc)
            return 2
        sources = [Path(args.db), Path(args.catalog), Path(args.config), old_snapshot_path]
        if new_snapshot_path != old_snapshot_path:
            sources.append(new_snapshot_path)
        was_current = provider_manager.is_current_provider(args.provider_id)
        backup_id = backup_for_write(sources, f"before-provider-update-{args.provider_id}", Path(args.backup_root))
        try:
            settings = provider_manager.update_provider(
                provider_id=args.provider_id,
                new_provider_id=new_provider_id,
                name=args.name,
                base_url=args.base_url,
                api_key=args.api_key,
                default_model=args.default_model,
                context_window=args.context_window,
                api_format=args.api_format,
            )
        except ValueError as exc:
            print(exc)
            return 2
        if args.sync_current and was_current:
            _write_catalog_from_provider_settings(Path(args.catalog), settings)
            config_text = str(settings.get("config") or "")
            default_model = extract_toml_string(config_text, "model")
            if default_model:
                CodexConfigManager(Path(args.config)).set_default_model(default_model)
            _sync_codex_proxy_config(runner, Path(args.config))
            if args.restart:
                ProxyManager(runner).restart()
        if new_provider_id != args.provider_id:
            print(f"供应商已修改: {args.provider_id} -> {new_provider_id}")
        else:
            print(f"供应商已修改: {args.provider_id}")
        print(f"备份: {backup_id}")
        return 0

    if args.command == "provider" and args.provider_command == "delete":
        if not args.yes:
            print("需要确认：请添加 --yes 执行删除供应商。")
            return 2
        provider_manager = ProviderManager(Path(args.db))
        try:
            provider_manager.ensure_deletable(args.provider_id)
        except ValueError as exc:
            print(exc)
            return 2
        backup_id = backup_for_write(
            [Path(args.db), provider_manager.provider_snapshot_path(args.provider_id)],
            f"before-provider-delete-{args.provider_id}",
            Path(args.backup_root),
        )
        try:
            provider_manager.delete_provider(args.provider_id, force=args.force)
        except ValueError as exc:
            print(exc)
            return 2
        print(f"供应商已删除: {args.provider_id}")
        print(f"备份: {backup_id}")
        return 0

    if args.command == "provider" and args.provider_command == "switch":
        if not args.yes:
            print("需要确认：请添加 --yes 执行切换供应商。")
            return 2
        provider_manager = ProviderManager(Path(args.db))
        try:
            provider_manager.ensure_switchable(args.provider_id)
        except ValueError as exc:
            print(exc)
            return 2
        sources = [Path(args.db), Path(args.catalog), Path(args.config), provider_manager.provider_snapshot_path(args.provider_id)]
        backup_id = backup_for_write(sources, f"before-provider-switch-{args.provider_id}", Path(args.backup_root))
        settings = provider_manager.switch_provider(args.provider_id)
        _write_catalog_from_provider_settings(Path(args.catalog), settings)
        config_text = str(settings.get("config") or "")
        default_model = extract_toml_string(config_text, "model")
        if default_model:
            CodexConfigManager(Path(args.config)).set_default_model(default_model)
        _sync_codex_proxy_config(runner, Path(args.config))
        if not args.no_restart:
            ProxyManager(runner).restart()
        print(f"供应商已切换: {args.provider_id}")
        print(f"备份: {backup_id}")
        return 0

    if args.command == "model" and args.model_command == "list":
        models = ModelStore(Path(args.catalog)).list_models()
        warning = _model_catalog_sync_warning(Path(args.db), Path(args.catalog))
        if warning:
            write_text(warning + "\n")
        write_text(render_models(models))
        return 0

    if args.command == "model" and args.model_command == "sync-current":
        if not args.yes:
            print("需要确认：请添加 --yes 执行同步当前供应商模型。")
            return 2
        try:
            provider_id, model_count, backup_id = _sync_current_provider_catalog(
                Path(args.db),
                Path(args.catalog),
                Path(args.config),
                Path(args.backup_root),
            )
            _sync_codex_proxy_config(runner, Path(args.config))
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
            print(exc)
            return 2
        print(f"已同步当前供应商模型到 Codex catalog: {provider_id} ({model_count} 个模型)")
        print(f"备份: {backup_id}")
        return 0

    if args.command == "model" and args.model_command == "set-default":
        if not args.yes:
            print("需要确认：请添加 --yes 执行设置默认模型。")
            return 2
        backup_id = backup_for_write([Path(args.config)], f"before-model-set-default-{args.model}", Path(args.backup_root))
        CodexConfigManager(Path(args.config)).set_default_model(args.model)
        _sync_codex_proxy_config(runner, Path(args.config))
        print(f"默认模型已设置: {args.model}")
        print(f"备份: {backup_id}")
        return 0

    if args.command == "model" and args.model_command == "add":
        if not args.yes:
            print("需要确认：请添加 --yes 执行新增模型。")
            return 2
        provider_manager = ProviderManager(Path(args.db))
        sources = [Path(args.db), Path(args.catalog), Path(args.config), provider_manager.provider_snapshot_path(args.provider_id)]
        backup_id = backup_for_write(sources, f"before-model-add-{args.model}", Path(args.backup_root))
        manager = ModelManager(
            provider_manager,
            CatalogManager(Path(args.catalog)),
            CodexConfigManager(Path(args.config)),
            codex_home=Path(args.codex_home),
        )
        manager.add(
            args.provider_id,
            args.model,
            args.display_name or args.model,
            args.context_window,
            template=args.template,
            profile=args.profile,
            make_default=args.default,
        )
        if args.default:
            _sync_codex_proxy_config(runner, Path(args.config))
        print(f"模型已新增: {args.model}")
        print(f"备份: {backup_id}")
        return 0

    if args.command == "model" and args.model_command == "delete":
        if not args.yes:
            print("需要确认：请添加 --yes 执行删除模型。")
            return 2
        provider_manager = ProviderManager(Path(args.db))
        sources = [Path(args.db), Path(args.catalog), Path(args.config), provider_manager.provider_snapshot_path(args.provider_id)]
        backup_id = backup_for_write(sources, f"before-model-delete-{args.model}", Path(args.backup_root))
        manager = ModelManager(
            provider_manager,
            CatalogManager(Path(args.catalog)),
            CodexConfigManager(Path(args.config)),
            codex_home=Path(args.codex_home),
        )
        manager.delete(args.provider_id, args.model, profile=args.profile, force=args.force)
        print(f"模型已删除: {args.model}")
        print(f"备份: {backup_id}")
        return 0

    if args.command == "backup" and args.backup_command == "create":
        sources = [Path(item) for item in args.source] if args.source else None
        manifest = BackupManager(backup_root=Path(args.backup_root), sources=sources).create(args.reason)
        print(f"备份已创建: {manifest.backup_id}")
        print(f"路径: {manifest.backup_root}")
        print(f"文件数: {sum(1 for entry in manifest.files if entry.exists)}")
        return 0

    if args.command == "backup" and args.backup_command == "list":
        backups = BackupManager(backup_root=Path(args.backup_root)).list_backups()
        write_text(render_backups(backups))
        return 0

    if args.command == "backup" and args.backup_command == "restore":
        if not args.yes:
            print("需要确认：请添加 --yes 执行恢复。")
            return 2
        result = BackupManager(backup_root=Path(args.backup_root)).restore(args.backup_id, confirm=True)
        print(result)
        return 0

    if args.command == "backup" and args.backup_command == "delete":
        if not args.yes:
            print("需要确认：请添加 --yes 执行删除备份。")
            return 2
        result = BackupManager(backup_root=Path(args.backup_root)).delete(args.backup_id, confirm=True)
        print(result)
        return 0 if result.startswith("deleted:") else 2

    if args.command == "proxy" and args.proxy_command == "status":
        write_text(render_proxy_status(ProxyManager(runner, service_name=args.service).status()))
        return 0

    if args.command == "proxy" and args.proxy_command == "set":
        if not args.yes:
            print("需要确认：请添加 --yes 执行代理设置。")
            return 2
        sources = [Path(args.service_path), Path(args.config)]
        backup_id = backup_for_write(sources, "before-proxy-set", Path(args.backup_root))
        ProxyManager(
            runner,
            service_name=args.service,
            service_path=Path(args.service_path),
            codex_config=Path(args.config),
        ).configure(args.listen_address, args.listen_port, restart=args.restart)
        print(f"代理已设置: {args.listen_address}:{args.listen_port}")
        print(f"备份: {backup_id}")
        return 0

    if args.command == "proxy" and args.proxy_command == "restart":
        if not args.yes:
            print("需要确认：请添加 --yes 执行重启代理。")
            return 2
        result = ProxyManager(runner, service_name=args.service).restart()
        print(result.stdout or result.stderr or "代理已重启")
        return 0

    if args.command == "proxy" and args.proxy_command == "logs":
        write_text(ProxyManager(runner, service_name=args.service).logs(args.lines))
        return 0

    if args.command == "proxy" and args.proxy_command == "test":
        result = ProxyManager(runner).test(args.model, args.base_url)
        write_text(result.stdout or result.stderr)
        return 0 if result.returncode == 0 else result.returncode

    if args.command == "tui":
        from tui import run_tui

        return run_tui(language=args.lang)

    parser.print_help()
    return 2


def _write_catalog_from_provider_settings(catalog_path: Path, settings: dict) -> None:
    entries = settings.get("modelCatalog", {}).get("models", [])
    models = []
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model = entry.get("model")
            if not model:
                continue
            context = entry.get("contextWindow") or entry.get("context_window") or 128000
            models.append(build_codex_catalog_model(model, entry.get("displayName") or model, int(context)))
    CatalogManager(catalog_path).save({"models": models})


def _sync_codex_proxy_config(runner: Runner, config_path: Path) -> None:
    proxy_manager = ProxyManager(runner, codex_config=config_path)
    proxy_manager.enable_route()
    proxy_manager.sync_codex_base_url()


def _current_provider_id(db_path: Path) -> str:
    providers = ProviderStore(db_path).list_providers()
    for provider in providers:
        if provider.current and not provider.read_only:
            return provider.provider_id
    raise ValueError("未找到当前可管理供应商。")


def _provider_model_ids(settings: dict) -> set[str]:
    entries = settings.get("modelCatalog", {}).get("models", [])
    if not isinstance(entries, list):
        return set()
    result = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("model") or entry.get("id") or "").strip()
        if model_id:
            result.add(model_id)
    return result


def _catalog_model_ids(catalog_path: Path) -> set[str]:
    return {model.slug for model in ModelStore(catalog_path).list_models()}


def _model_catalog_sync_warning(db_path: Path, catalog_path: Path) -> str:
    try:
        provider_id = _current_provider_id(db_path)
        settings = ProviderManager(db_path).load_settings(provider_id)
        provider_models = _provider_model_ids(settings)
        catalog_models = _catalog_model_ids(catalog_path)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError, sqlite3.Error):
        return ""
    if provider_models and provider_models != catalog_models:
        return (
            f"警告：当前供应商 {provider_id} 有 {len(provider_models)} 个模型，"
            f"Codex catalog 有 {len(catalog_models)} 个模型；请执行 model sync-current --yes 同步。"
        )
    return ""


def _sync_current_provider_catalog(
    db_path: Path,
    catalog_path: Path,
    config_path: Path,
    backup_root: Path,
) -> tuple[str, int, str]:
    provider_id = _current_provider_id(db_path)
    provider_manager = ProviderManager(db_path)
    settings = provider_manager.load_settings(provider_id)
    provider_manager.ensure_switchable(provider_id, settings)
    model_count = len(_provider_model_ids(settings))
    backup_id = backup_for_write(
        [catalog_path, config_path, provider_manager.provider_snapshot_path(provider_id)],
        f"before-model-sync-current-{provider_id}",
        backup_root,
    )
    provider_manager.write_provider_snapshot(provider_id, settings)
    _write_catalog_from_provider_settings(catalog_path, settings)
    config_text = str(settings.get("config") or "")
    default_model = extract_toml_string(config_text, "model")
    if default_model:
        CodexConfigManager(config_path).set_default_model(default_model)
    return provider_id, model_count, backup_id


if __name__ == "__main__":
    raise SystemExit(main())
