from __future__ import annotations

import shutil
import unicodedata
from typing import Iterable, Optional

from backups import BackupManifest
from preflight import CheckResult
from proxy import ProxyStatus
from stores import ModelInfo, ProviderInfo


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _display_width(text) <= width:
        return text

    ellipsis = "…"
    limit = max(0, width - _display_width(ellipsis))
    clipped = ""
    used = 0
    for char in text:
        char_width = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        if used + char_width > limit:
            break
        clipped += char
        used += char_width
    return clipped + ellipsis


def _fit(text: object, width: int, align: str = "left") -> str:
    value = _clip(str(text), width)
    padding = " " * max(0, width - _display_width(value))
    return padding + value if align == "right" else value + padding


def _clip_middle(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _display_width(text) <= width:
        return text
    ellipsis = "…"
    ellipsis_width = _display_width(ellipsis)
    if width <= ellipsis_width:
        return _clip(text, width)
    left_budget = (width - ellipsis_width + 1) // 2
    right_budget = width - ellipsis_width - left_budget
    left = _clip(text, left_budget)
    if left.endswith(ellipsis):
        left = left[:-1]
    right = ""
    used = 0
    for char in reversed(text):
        char_width = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        if used + char_width > right_budget:
            break
        right = char + right
        used += char_width
    return left + ellipsis + right


def _fit_middle(text: object, width: int, align: str = "left") -> str:
    value = _clip_middle(str(text), width)
    padding = " " * max(0, width - _display_width(value))
    return padding + value if align == "right" else value + padding


def render_doctor(results: Iterable[CheckResult]) -> str:
    lines = [
        "环境检查 / 安装",
        "────────────────────────────────────────",
        "状态  组件         路径                         版本",
    ]
    for item in results:
        icon = "✅" if item.status == "ok" else "❌"
        path = item.path or "-"
        version = item.version or "-"
        lines.append(f"{icon}  {item.name:<12} {path:<28} {version}")
    return "\n".join(lines) + "\n"


def render_providers(providers: Iterable[ProviderInfo]) -> str:
    columns = [
        ("当前", 4, "left"),
        ("ID", 16, "left"),
        ("名称", 16, "left"),
        ("Base URL", 39, "left"),
        ("模型", 4, "right"),
    ]
    lines = [
        "供应商列表",
        "────────────────────────────────────────",
        "  ".join(_fit(label, width, align) for label, width, align in columns) + "  默认模型 / 状态",
    ]
    for provider in providers:
        marker = "✓" if provider.current else " "
        if provider.read_only:
            base_url = "官方内置"
            model_count = "动态"
            default_model = "系统只读，不可切换"
        else:
            base_url = provider.base_url or "N/A"
            model_count = str(provider.model_count) if provider.model_count is not None else "-"
            default_model = provider.default_model or "-"
        values = [
            (marker, 4, "left"),
            (provider.provider_id, 16, "left"),
            (provider.name, 16, "left"),
            (base_url, 39, "left"),
            (model_count, 4, "right"),
        ]
        lines.append(
            "  ".join(_fit(value, width, align) for value, width, align in values) + f"  {default_model}"
        )
    return "\n".join(lines) + "\n"


def render_models(models: Iterable[ModelInfo], width: Optional[int] = None) -> str:
    model_list = list(models)
    terminal_width = min(width if width is not None else shutil.get_terminal_size((100, 24)).columns, 120)
    context_values = [str(model.context_window) if model.context_window is not None else "-" for model in model_list]
    context_width = max(_display_width("上下文"), *(_display_width(value) for value in context_values), 6)
    content_width = max(32, terminal_width - context_width - 4)
    model_width = max(16, content_width // 2)
    display_width = max(16, content_width - model_width)
    lines = [
        "模型列表",
        "────────────────────────────────────────",
        f"{_fit('模型', model_width)}  {_fit('显示名称', display_width)}  {_fit('上下文', context_width, 'right')}",
    ]
    for model, context in zip(model_list, context_values):
        lines.append(
            f"{_fit_middle(model.slug, model_width)}  "
            f"{_fit_middle(model.display_name, display_width)}  "
            f"{_fit(context, context_width, 'right')}"
        )
    return "\n".join(lines) + "\n"


def render_backups(backups: Iterable[BackupManifest]) -> str:
    lines = [
        "备份列表",
        "────────────────────────────────────────",
        "ID               文件数  原因",
    ]
    for backup in backups:
        existing_count = sum(1 for entry in backup.files if entry.exists)
        lines.append(f"{backup.backup_id:<16} {existing_count:<6} {backup.reason}")
    return "\n".join(lines) + "\n"


def render_proxy_status(status: ProxyStatus) -> str:
    return "\n".join(
        [
            "代理状态",
            "────────────────────────────────────────",
            f"服务:     {status.service}",
            f"状态:     {status.active}",
            f"监听:     {status.listen_address}:{status.listen_port}",
            f"路由:     {status.provider_route}",
            "",
        ]
    )
