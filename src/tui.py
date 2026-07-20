from __future__ import annotations

import contextlib
from dataclasses import dataclass
import hashlib
import io
import json
import os
import re
import select
import shutil
import sys
import unicodedata
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - Windows fallback
    termios = None
    tty = None

from backups import BackupManager, BackupManifest
from cli import main, write_text
from i18n import menu_groups, t
from operations import DEFAULT_CODEX_CONFIG, ProviderManager, extract_toml_string
from settings import SettingsManager, normalize_language
from stores import ModelInfo, ModelStore, ProviderStore


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
ESC_SEQUENCE_TIMEOUT_SECONDS = 0.15
FOCUS_FORM_FIELD_START_ROW = 6
MODEL_SELECTOR_MAX_ROWS = 12
MODEL_LIST_MAX_ROWS = 14
BACKUP_SELECTOR_MAX_ROWS = 12
PROVIDER_DEFAULT_HEALTH_KEY = "__provider_default_model_health"
PROVIDER_DEFAULT_HEALTH_DETAIL_KEY = "__provider_default_model_health_detail"
DANGER_ACTIONS = {"7", "11", "19", "20"}
COLOR_ROLES = {
    "title": "1;36",
    "section": "1;34",
    "focus": "1;36",
    "read": "32",
    "write": "33",
    "danger": "1;31",
    "required": "1;33",
    "muted": "2",
    "value": "1;37",
    "selected": "7",
}


class TuiInterrupted(Exception):
    """Raised when the user cancels the interactive menu."""


@dataclass(frozen=True)
class FormField:
    key: str
    label: str
    default: str = ""
    required: bool = False
    secret: bool = False
    choices: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelHealthResult:
    model_id: str
    ok: bool
    detail: str


def _supports_color() -> bool:
    return bool(getattr(sys.stdout, "isatty", lambda: False)()) and "NO_COLOR" not in os.environ


def _style(text: str, code: str, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


def _role(text: str, role: str, enabled: bool) -> str:
    return _style(text, COLOR_ROLES[role], enabled)


def _display_width(text: str) -> int:
    text = ANSI_RE.sub("", text)
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1


def _fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _display_width(text) <= width:
        return text + " " * (width - _display_width(text))

    clipped = ""
    used = 0
    ellipsis_width = 1
    index = 0
    while index < len(text):
        match = ANSI_RE.match(text, index)
        if match:
            clipped += match.group(0)
            index = match.end()
            continue
        char = text[index]
        char_width = _char_width(char)
        if used + char_width + ellipsis_width > width:
            break
        clipped += char
        used += char_width
        index += 1
    if "\033[" in text and not clipped.endswith("\033[0m"):
        clipped += "\033[0m"
    return clipped + "…" + " " * max(0, width - used - ellipsis_width)


def _clip_middle(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _display_width(text) <= width:
        return text
    ellipsis = "…"
    if width <= _display_width(ellipsis):
        return _fit(text, width).strip()
    left_budget = (width - 1 + 1) // 2
    right_budget = width - 1 - left_budget
    left = _fit(text, left_budget).rstrip()
    if left.endswith(ellipsis):
        left = left[:-1]
    right = ""
    used = 0
    for char in reversed(text):
        char_width = _char_width(char)
        if used + char_width > right_budget:
            break
        right = char + right
        used += char_width
    return left + ellipsis + right


def _fit_middle(text: str, width: int) -> str:
    value = _clip_middle(text, width)
    return value + " " * max(0, width - _display_width(value))


def _fit_right(text: str, width: int) -> str:
    value = _fit(text, width).rstrip()
    return " " * max(0, width - _display_width(value)) + value


def _box_line(text: str, width: int) -> str:
    return f"│ {_fit(text, width - 4)} │"


def _header_line(language: str, key: str, value: str) -> str:
    label = t(language, key)
    separator = "：" if language == "zh" else ":"
    return f"{_fit(label + separator, 11)} {value}"


def _rule(label: str, width: int, color: bool) -> str:
    styled = _role(label, "section", color)
    return f"  {styled}"


def render_menu(
    width: Optional[int] = None,
    color: Optional[bool] = None,
    cwd: Optional[Path] = None,
    language: str = "zh",
    provider_status: str = "unknown",
    model_status: str = "unknown",
    selected_action: str = "1",
) -> str:
    language = normalize_language(language)
    terminal_width = width or shutil.get_terminal_size((96, 30)).columns
    frame_width = max(74, min(terminal_width, 112))
    color_enabled = _supports_color() if color is None else color
    current_dir = str(cwd or Path.cwd())

    title = _role(">_ Codex Model Admin", "title", color_enabled)
    routing = _role("cc-switch managed", "read", color_enabled)
    routing_hint = _role("Codex CLI proxy", "muted", color_enabled)
    header = [
        f"╭{'─' * (frame_width - 2)}╮",
        _box_line(title, frame_width),
        _box_line("", frame_width),
        _box_line(_header_line(language, "header_routing", f"{routing} / {routing_hint}"), frame_width),
        _box_line(_header_line(language, "header_provider", provider_status), frame_width),
        _box_line(_header_line(language, "header_model", model_status), frame_width),
        _box_line(_header_line(language, "header_directory", current_dir), frame_width),
        f"╰{'─' * (frame_width - 2)}╯",
    ]

    body: List[str] = ["", _style(t(language, "select_title"), "1", color_enabled)]
    groups = menu_groups(language)
    label_width = min(20, max(_display_width(label) for _, label, _, _ in sum((tuple(items) for _, items in groups), ())))
    for group_name, items in groups:
        body.append(_rule(group_name, frame_width, color_enabled))
        for number, label, description, mode_key in items:
            pointer = "›" if number == selected_action else " "
            mode = t(language, mode_key)
            mode_role = "read" if mode_key == "read" else "write"
            if number in DANGER_ACTIONS:
                mode_role = "danger"
            number_text = number.rjust(2)
            label_text = _fit(label, label_width)
            if number == selected_action:
                mode_text = _fit(f"[{mode}]", 8)
                description_text = description
                line = f"  {pointer} {number_text}. {label_text} {mode_text} {description_text}"
                body.append(_role(_fit(line, frame_width), "selected", color_enabled))
                continue
            mode_text = _role(_fit(f"[{mode}]", 8), mode_role, color_enabled)
            description_text = _role(description, "muted", color_enabled)
            pointer_text = pointer
            line = f"  {pointer_text} {number_text}. {label_text} {mode_text} {description_text}"
            body.append(_fit(line, frame_width))
        body.append("")

    footer = _role(t(language, "footer"), "muted", color_enabled)
    prompt = _role(f"  › {t(language, 'select_action')}", "focus", color_enabled)
    return "\n".join(header + body + [footer, prompt]) + "\n"


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    write_text(f"› {prompt}{suffix}: ")
    try:
        value = input().strip()
    except (KeyboardInterrupt, EOFError) as exc:
        raise TuiInterrupted from exc
    return value or default


def _confirm(language: str) -> bool:
    return _ask(t(language, "confirm"), "").lower() == "yes"


def _is_interactive_terminal() -> bool:
    return bool(
        hasattr(sys.stdin, "isatty")
        and hasattr(sys.stdout, "isatty")
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )


def _pause_after_output(language: str) -> None:
    if not _is_interactive_terminal():
        return
    write_text(_role(f"\n{t(language, 'press_enter_return')}", "muted", _supports_color()))
    if not _supports_focus_form():
        try:
            input()
        except (KeyboardInterrupt, EOFError) as exc:
            raise TuiInterrupted from exc
        return
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            key = _read_key()
            if key in {"\r", "\n", "\x1b", "q", "Q", ""}:
                return
            if key in {"\x03", "\x04"}:
                raise TuiInterrupted
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _print_output_header(language: str, title: str) -> None:
    color_enabled = _supports_color()
    provider_status, model_status = _menu_status()
    lines = _form_status_lines(title, language, provider_status, model_status, color_enabled)
    lines.extend(
        [
            _role(t(language, "action_output"), "section", color_enabled),
            _role("────────────────────────────────────────", "muted", color_enabled),
            "",
        ]
    )
    write_text("\n".join(lines))


def _run_output_command(language: str, title: str, argv: Sequence[str]) -> None:
    if _is_interactive_terminal():
        _clear_screen()
        _print_output_header(language, title)
    main(argv)
    _pause_after_output(language)


def _run_bulk_output_commands(
    language: str,
    title: str,
    commands: Sequence[Tuple[str, Optional[Sequence[str]]]],
) -> None:
    if _is_interactive_terminal():
        _clear_screen()
        _print_output_header(language, title)
    success = 0
    skipped = 0
    failed = 0
    for model_id, argv in commands:
        print(f"\n[{model_id}]")
        if argv is None:
            print(t(language, "model_bulk_skip_existing"))
            skipped += 1
            continue
        code = main(list(argv))
        if code == 0:
            success += 1
        else:
            failed += 1
    print()
    print(t(language, "model_bulk_done").format(success=success, skipped=skipped, failed=failed))
    _pause_after_output(language)


def _run_model_delete_output_commands(
    language: str,
    title: str,
    commands: Sequence[Tuple[str, Sequence[str]]],
) -> None:
    if _is_interactive_terminal():
        _clear_screen()
        _print_output_header(language, title)
    success = 0
    failed = 0
    for model_id, argv in commands:
        print(f"\n[{model_id}]")
        code = main(list(argv))
        if code == 0:
            success += 1
        else:
            failed += 1
    print()
    print(t(language, "model_delete_bulk_done").format(success=success, failed=failed))
    _pause_after_output(language)


def _catalog_model_ids() -> set[str]:
    try:
        return {model.slug for model in ModelStore().list_models()}
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return set()


def _provider_model_ids(provider_id: str) -> set[str]:
    try:
        settings = ProviderManager().load_settings(provider_id)
    except (FileNotFoundError, OSError, ValueError):
        return set()
    model_catalog = settings.get("modelCatalog")
    if not isinstance(model_catalog, dict):
        return set()
    models = model_catalog.get("models", [])
    if not isinstance(models, list):
        return set()
    result = set()
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("model") or entry.get("id") or "").strip()
        if model_id:
            result.add(model_id)
    return result


def _provider_model_choices(provider_id: str) -> List[str]:
    try:
        settings = ProviderManager().load_settings(provider_id)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    model_catalog = settings.get("modelCatalog")
    if not isinstance(model_catalog, dict):
        return []
    models = model_catalog.get("models", [])
    if not isinstance(models, list):
        return []
    result: List[str] = []
    seen = set()
    for entry in models:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("model") or entry.get("id") or "").strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            result.append(model_id)
    return result


def _existing_model_ids(provider_id: str) -> set[str]:
    return _catalog_model_ids() | _provider_model_ids(provider_id)


def _catalog_model_choices() -> List[str]:
    try:
        models = [model.slug for model in ModelStore().list_models() if model.slug]
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    if not models:
        raise ValueError("empty model catalog")
    return models


def _current_provider_model_api(provider_id: str) -> Tuple[str, str]:
    settings = ProviderManager().load_settings(provider_id)
    config_text = str(settings.get("config") or "")
    base_url = extract_toml_string(config_text, "base_url").strip()
    auth = settings.get("auth")
    api_key = ""
    if isinstance(auth, dict):
        api_key = str(auth.get("OPENAI_API_KEY") or "").strip()
    if not base_url:
        raise ValueError("provider missing base_url")
    if not api_key:
        raise ValueError("provider missing OPENAI_API_KEY")
    return base_url, api_key


def _models_endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + "/models"


def _responses_endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + "/responses"


def _chat_completions_endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def _post_provider_json(base_url: str, api_key: str, payload: Dict[str, object], timeout: int) -> Dict[str, object]:
    request = Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    if not body.strip():
        return {}
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("invalid_response")
    return value


def _health_error_detail(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        if exc.code in {401, 403}:
            return "unauthorized"
        if exc.code == 429:
            return "rate_limited"
        if exc.code == 404:
            return "model_not_found"
        if 500 <= exc.code:
            return "server_error"
        return f"HTTP {exc.code}"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, URLError):
        return "timeout" if isinstance(exc.reason, TimeoutError) else str(exc.reason)
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_response"
    return str(exc) or exc.__class__.__name__


def _check_model_health_from_api(base_url: str, api_key: str, model_id: str, timeout: int = 12) -> ModelHealthResult:
    try:
        _post_provider_json(
            _responses_endpoint(base_url),
            api_key,
            {"model": model_id, "input": "ping", "max_output_tokens": 1},
            timeout,
        )
        return ModelHealthResult(model_id, True, "responses")
    except HTTPError as exc:
        if exc.code not in {404, 405}:
            return ModelHealthResult(model_id, False, _health_error_detail(exc))
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return ModelHealthResult(model_id, False, _health_error_detail(exc))

    try:
        _post_provider_json(
            _chat_completions_endpoint(base_url),
            api_key,
            {"model": model_id, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            timeout,
        )
        return ModelHealthResult(model_id, True, "chat/completions")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return ModelHealthResult(model_id, False, _health_error_detail(exc))


def _check_model_health(provider_id: str, model_id: str, timeout: int = 12) -> ModelHealthResult:
    base_url, api_key = _current_provider_model_api(provider_id)
    return _check_model_health_from_api(base_url, api_key, model_id, timeout)


def _fetch_models_from_api(base_url: str, api_key: str, timeout: int = 15) -> List[str]:
    request = Request(_models_endpoint(base_url), headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("invalid /models response")
    models = []
    seen = set()
    for entry in entries:
        model_id = ""
        if isinstance(entry, dict):
            model_id = str(entry.get("id") or "").strip()
        elif isinstance(entry, str):
            model_id = entry.strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            models.append(model_id)
    if not models:
        raise ValueError("empty model list")
    return sorted(models, key=str.lower)


def _fetch_provider_models(provider_id: str, timeout: int = 15) -> List[str]:
    base_url, api_key = _current_provider_model_api(provider_id)
    return _fetch_models_from_api(base_url, api_key, timeout)


def _provider_default_health_signature(values: Dict[str, str]) -> str:
    payload = "\0".join(
        [
            values.get("base_url", "").strip(),
            values.get("api_key", "").strip(),
            values.get("default_model", "").strip(),
            values.get("api_format", "").strip(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _provider_default_health_matches(values: Dict[str, str]) -> bool:
    return bool(values.get("default_model", "").strip()) and values.get(PROVIDER_DEFAULT_HEALTH_KEY) == _provider_default_health_signature(values)


def _clear_provider_default_health(values: Dict[str, str]) -> None:
    values.pop(PROVIDER_DEFAULT_HEALTH_KEY, None)
    values.pop(PROVIDER_DEFAULT_HEALTH_DETAIL_KEY, None)


def _set_provider_default_health(values: Dict[str, str], result: ModelHealthResult) -> None:
    values[PROVIDER_DEFAULT_HEALTH_KEY] = _provider_default_health_signature(values)
    values[PROVIDER_DEFAULT_HEALTH_DETAIL_KEY] = result.detail


def _provider_default_form_api(values: Dict[str, str]) -> Tuple[str, str]:
    base_url = values.get("base_url", "").strip()
    api_key = values.get("api_key", "").strip()
    if not base_url or not api_key:
        raise ValueError("missing_api")
    return base_url, api_key


def _check_provider_default_model_health(values: Dict[str, str]) -> ModelHealthResult:
    base_url, api_key = _provider_default_form_api(values)
    model_id = values.get("default_model", "").strip()
    if not model_id:
        raise ValueError("missing_model")
    result = _check_model_health_from_api(base_url, api_key, model_id)
    if result.ok:
        _set_provider_default_health(values, result)
    else:
        _clear_provider_default_health(values)
    return result


def _filter_models(models: Sequence[str], query: str) -> List[str]:
    tokens = [token for token in query.lower().split() if token]
    if not tokens:
        return list(models)
    return [model for model in models if all(token in model.lower() for token in tokens)]


def _selected_models_summary(
    language: str,
    selected_models: Sequence[str],
    limit: int = 3,
    label_key: str = "model_selector_selected",
) -> str:
    label = t(language, label_key)
    if not selected_models:
        return f"{label}: -"
    shown = list(selected_models[:limit])
    suffix = f", ... +{len(selected_models) - limit}" if len(selected_models) > limit else ""
    return f"{label} {len(selected_models)}: {', '.join(shown)}{suffix}"


def _format_field_value(field: FormField, value: str) -> str:
    if field.secret and value:
        return "*" * min(8, max(4, len(value)))
    return value or "-"


def _field_label(field: FormField) -> str:
    return field.label + (" *" if field.required else "")


def _field_label_width(fields: Sequence[FormField]) -> int:
    return max(_display_width(_field_label(field)) for field in fields)


def _form_status_lines(
    title: str,
    language: str,
    provider_status: str,
    model_status: str,
    color_enabled: bool,
) -> List[str]:
    separator = "：" if language == "zh" else ":"
    provider_label = t(language, "header_provider") + separator
    model_label = t(language, "header_model") + separator
    return [
        f"{_role('Codex Model Admin', 'title', color_enabled)} / {_role(title, 'focus', color_enabled)}",
        (
            f"{_role(provider_label, 'muted', color_enabled)} {_role(provider_status, 'read', color_enabled)}"
            f"    {_role(model_label, 'muted', color_enabled)} {_role(model_status, 'value', color_enabled)}"
        ),
        "",
    ]


def _styled_field_label(field: FormField, label_width: int, color_enabled: bool) -> str:
    label = field.label
    if field.required:
        label += _role(" *", "required", color_enabled)
    return _fit(label, label_width)


def render_action_form(
    title: str,
    fields: Sequence[FormField],
    values: Dict[str, str],
    language: str = "zh",
    errors: Sequence[str] = (),
    color: Optional[bool] = None,
) -> str:
    color_enabled = _supports_color() if color is None else color
    lines = [
        "",
        _role(title, "focus", color_enabled),
        _role("────────────────────────────────────────", "muted", color_enabled),
    ]
    if fields:
        label_width = _field_label_width(fields)
        for index, field in enumerate(fields, start=1):
            value = _format_field_value(field, values.get(field.key, ""))
            choices = _role(f" ({'/'.join(field.choices)})", "muted", color_enabled) if field.choices else ""
            label = _styled_field_label(field, label_width, color_enabled)
            lines.append(f"{index:>2}. {label} {value}{choices}")
    else:
        lines.append(t(language, "form_no_fields"))
    if errors:
        lines.append("")
        lines.append(_role(t(language, "form_errors"), "danger", color_enabled))
        lines.extend(_role(f"- {error}", "danger", color_enabled) for error in errors)
    lines.extend(
        [
            "",
            _role(t(language, "form_help"), "muted", color_enabled),
        ]
    )
    return "\n".join(lines) + "\n"


def render_focus_form(
    title: str,
    fields: Sequence[FormField],
    values: Dict[str, str],
    focus_index: int,
    language: str = "zh",
    errors: Sequence[str] = (),
    message_lines: Sequence[str] = (),
    provider_status: str = "unknown",
    model_status: str = "unknown",
    color: Optional[bool] = None,
) -> str:
    color_enabled = _supports_color() if color is None else color
    lines = _form_status_lines(title, language, provider_status, model_status, color_enabled)
    lines.extend(
        [
            _role(title, "focus", color_enabled),
            _role("────────────────────────────────────────", "muted", color_enabled),
        ]
    )
    if fields:
        label_width = _field_label_width(fields)
        for index, field in enumerate(fields):
            focused = index == focus_index
            marker = _role("›", "focus", color_enabled) if focused else " "
            value = _format_field_value(field, values.get(field.key, ""))
            choices = _role(f" ({'/'.join(field.choices)})", "muted", color_enabled) if field.choices else ""
            label = _styled_field_label(field, label_width, color_enabled)
            display_value = f"{_role('[', 'focus', color_enabled)} {value} {_role(']', 'focus', color_enabled)}" if focused else value
            lines.append(f"{marker} {label} {display_value}{choices}")
    else:
        lines.append(t(language, "form_no_fields"))
    if errors:
        lines.append("")
        lines.append(_role(t(language, "form_errors"), "danger", color_enabled))
        lines.extend(_role(f"- {error}", "danger", color_enabled) for error in errors)
    if message_lines:
        lines.append("")
        lines.extend(message_lines)
    lines.extend(["", _role(t(language, "focus_form_help"), "muted", color_enabled)])
    return "\n".join(lines) + "\n"


def _visible_model_window(filtered: Sequence[str], selected_index: int) -> Tuple[int, Sequence[str]]:
    if len(filtered) <= MODEL_SELECTOR_MAX_ROWS:
        return 0, filtered
    half = MODEL_SELECTOR_MAX_ROWS // 2
    start = max(0, min(selected_index - half, len(filtered) - MODEL_SELECTOR_MAX_ROWS))
    return start, filtered[start : start + MODEL_SELECTOR_MAX_ROWS]


def _visible_model_info_window(models: Sequence[ModelInfo], selected_index: int) -> Tuple[int, Sequence[ModelInfo]]:
    if len(models) <= MODEL_LIST_MAX_ROWS:
        return 0, models
    half = MODEL_LIST_MAX_ROWS // 2
    start = max(0, min(selected_index - half, len(models) - MODEL_LIST_MAX_ROWS))
    return start, models[start : start + MODEL_LIST_MAX_ROWS]


def _visible_backup_window(backups: Sequence[BackupManifest], selected_index: int) -> Tuple[int, Sequence[BackupManifest]]:
    if len(backups) <= BACKUP_SELECTOR_MAX_ROWS:
        return 0, backups
    half = BACKUP_SELECTOR_MAX_ROWS // 2
    start = max(0, min(selected_index - half, len(backups) - BACKUP_SELECTOR_MAX_ROWS))
    return start, backups[start : start + BACKUP_SELECTOR_MAX_ROWS]


def _backup_existing_count(backup: BackupManifest) -> int:
    return sum(1 for entry in backup.files if entry.exists)


def _selected_backups_summary(language: str, selected_backup_ids: Sequence[str], limit: int = 3) -> str:
    label = t(language, "backup_delete_selected")
    if not selected_backup_ids:
        return f"{label}: -"
    shown = list(selected_backup_ids[:limit])
    suffix = f", ... +{len(selected_backup_ids) - limit}" if len(selected_backup_ids) > limit else ""
    return f"{label} {len(selected_backup_ids)}: {', '.join(shown)}{suffix}"


def _model_list_table_width(width: Optional[int] = None) -> int:
    terminal_width = width if width is not None else shutil.get_terminal_size((100, 24)).columns
    return max(70, min(terminal_width, 118))


def _model_health_label(
    language: str,
    model_id: str,
    health_results: Optional[Dict[str, ModelHealthResult]] = None,
    checking_model: str = "",
) -> str:
    if checking_model == model_id:
        return t(language, "model_health_status_checking")
    result = (health_results or {}).get(model_id)
    if result is None:
        return t(language, "model_health_status_unknown")
    return "OK" if result.ok else "FAIL"


def _model_health_detail(
    language: str,
    model_id: str,
    health_results: Optional[Dict[str, ModelHealthResult]] = None,
    checking_model: str = "",
) -> str:
    label = _model_health_label(language, model_id, health_results, checking_model)
    result = (health_results or {}).get(model_id)
    if result is None or checking_model == model_id:
        return label
    return f"{label} ({result.detail})"


def _check_model_health_safe(provider_id: str, model_id: str) -> ModelHealthResult:
    try:
        return _check_model_health(provider_id, model_id)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return ModelHealthResult(model_id, False, str(exc))


def _model_catalog_sync_messages(language: str) -> List[str]:
    provider_id = _current_provider_id()
    provider_models = _provider_model_ids(provider_id)
    catalog_models = _catalog_model_ids()
    if provider_models and provider_models != catalog_models:
        return [
            t(language, "model_catalog_out_of_sync").format(
                provider_count=len(provider_models),
                catalog_count=len(catalog_models),
            )
        ]
    return []


def _model_list_columns(language: str, models: Sequence[ModelInfo], width: Optional[int] = None) -> Tuple[int, int, int, int, int]:
    table_width = _model_list_table_width(width)
    context_values = [str(model.context_window) if model.context_window is not None else "-" for model in models]
    context_width = max(_display_width(t(language, "model_list_context")), *(_display_width(value) for value in context_values), 6)
    health_width = max(
        _display_width(t(language, "model_list_health")),
        _display_width(t(language, "model_health_status_unknown")),
        _display_width(t(language, "model_health_status_checking")),
        6,
    )
    marker_width = 2
    gap_width = 6
    content_width = max(32, table_width - marker_width - gap_width - context_width - health_width)
    model_width = max(18, min(42, content_width // 2))
    display_width = max(18, content_width - model_width)
    table_width = marker_width + model_width + 2 + display_width + 2 + context_width + 2 + health_width
    return table_width, model_width, display_width, context_width, health_width


def _model_list_row(
    model: ModelInfo,
    marker: str,
    model_width: int,
    display_width: int,
    context_width: int,
    health_width: int,
    language: str,
    health_results: Optional[Dict[str, ModelHealthResult]] = None,
    checking_model: str = "",
) -> str:
    context = str(model.context_window) if model.context_window is not None else "-"
    health = _model_health_label(language, model.slug, health_results, checking_model)
    return (
        f"{marker} "
        f"{_fit_middle(model.slug, model_width)}  "
        f"{_fit_middle(model.display_name, display_width)}  "
        f"{_fit_right(context, context_width)}  "
        f"{_fit(health, health_width)}"
    )


def render_model_list_interactive(
    language: str,
    models: Sequence[ModelInfo],
    selected_index: int = 0,
    width: Optional[int] = None,
    color: Optional[bool] = None,
    health_results: Optional[Dict[str, ModelHealthResult]] = None,
    checking_model: str = "",
    message_lines: Sequence[str] = (),
) -> str:
    color_enabled = _supports_color() if color is None else color
    provider_status, model_status = _menu_status()
    selected_index = min(max(selected_index, 0), max(0, len(models) - 1))
    table_width, model_width, display_width, context_width, health_width = _model_list_columns(language, models, width)
    lines = _form_status_lines(_action_title(language, "模型列表", "List Models"), language, provider_status, model_status, color_enabled)
    lines.extend(
        [
            _role(t(language, "model_list_title"), "focus", color_enabled),
            _role("────────────────────────────────────────", "muted", color_enabled),
        ]
    )
    if not models:
        lines.extend(["", _role(t(language, "model_list_empty"), "muted", color_enabled)])
    else:
        header = (
            f"  "
            f"{_fit(t(language, 'model_list_model'), model_width)}  "
            f"{_fit(t(language, 'model_list_display_name'), display_width)}  "
            f"{_fit_right(t(language, 'model_list_context'), context_width)}  "
            f"{_fit(t(language, 'model_list_health'), health_width)}"
        )
        lines.append(header)
        start, visible = _visible_model_info_window(models, selected_index)
        if start > 0:
            lines.append(_role(_fit(f"... {start} more above", table_width), "muted", color_enabled))
        for offset, model in enumerate(visible):
            index = start + offset
            focused = index == selected_index
            marker = "›" if focused else " "
            row = _model_list_row(
                model,
                marker,
                model_width,
                display_width,
                context_width,
                health_width,
                language,
                health_results,
                checking_model,
            )
            row = _fit(row, table_width)
            lines.append(_role(row, "selected", color_enabled) if focused else row)
        remaining = len(models) - start - len(visible)
        if remaining > 0:
            lines.append(_role(_fit(f"... {remaining} more below", table_width), "muted", color_enabled))
    if message_lines:
        lines.append("")
        lines.extend(_role(message, "muted", color_enabled) for message in message_lines)
    lines.extend(["", _role(t(language, "model_list_help"), "muted", color_enabled)])
    return "\n".join(lines) + "\n"


def render_model_detail(
    language: str,
    model: ModelInfo,
    color: Optional[bool] = None,
    health_results: Optional[Dict[str, ModelHealthResult]] = None,
    checking_model: str = "",
) -> str:
    color_enabled = _supports_color() if color is None else color
    provider_status, model_status = _menu_status()
    context = str(model.context_window) if model.context_window is not None else "-"
    lines = _form_status_lines(t(language, "model_detail_title"), language, provider_status, model_status, color_enabled)
    lines.extend(
        [
            _role(t(language, "model_detail_title"), "focus", color_enabled),
            _role("────────────────────────────────────────", "muted", color_enabled),
            f"{t(language, 'model_list_model')}: {model.slug}",
            f"{t(language, 'model_list_display_name')}: {model.display_name}",
            f"{t(language, 'model_list_context')}: {context}",
            f"{t(language, 'model_list_health')}: {_model_health_detail(language, model.slug, health_results, checking_model)}",
            "",
            _role(t(language, "model_detail_help"), "muted", color_enabled),
        ]
    )
    return "\n".join(lines) + "\n"


def _model_delete_enabled(model: ModelInfo, health_results: Dict[str, ModelHealthResult]) -> bool:
    result = health_results.get(model.slug)
    return result is not None and not result.ok


def _model_delete_is_current_default(model: ModelInfo) -> bool:
    _, model_status = _menu_status()
    return model_status == model.slug


def render_model_delete_confirm(
    language: str,
    model: ModelInfo,
    health_result: ModelHealthResult,
    color: Optional[bool] = None,
) -> str:
    color_enabled = _supports_color() if color is None else color
    provider_status, model_status = _menu_status()
    context = str(model.context_window) if model.context_window is not None else "-"
    lines = _form_status_lines(t(language, "model_delete_confirm_title"), language, provider_status, model_status, color_enabled)
    lines.extend(
        [
            _role(t(language, "model_delete_confirm_title"), "danger", color_enabled),
            _role("────────────────────────────────────────", "muted", color_enabled),
            f"{t(language, 'model_list_model')}: {model.slug}",
            f"{t(language, 'model_list_display_name')}: {model.display_name}",
            f"{t(language, 'model_list_context')}: {context}",
            f"{t(language, 'model_list_health')}: {_model_health_detail(language, model.slug, {model.slug: health_result}, '')}",
            "",
            _role(t(language, "model_delete_confirm_help"), "muted", color_enabled),
        ]
    )
    return "\n".join(lines) + "\n"


def render_backup_delete_selector(
    language: str,
    backups: Sequence[BackupManifest],
    selected_index: int = 0,
    selected_backup_ids: Sequence[str] = (),
    width: Optional[int] = None,
    color: Optional[bool] = None,
) -> str:
    color_enabled = _supports_color() if color is None else color
    provider_status, model_status = _menu_status()
    table_width = max(70, min(width or shutil.get_terminal_size((100, 24)).columns, 118))
    selected_set = set(selected_backup_ids)
    selected_index = min(max(selected_index, 0), max(0, len(backups) - 1))
    count_label = "文件数" if language == "zh" else "Files"
    reason_label = "原因" if language == "zh" else "Reason"
    id_width = max(16, min(28, max([_display_width("ID")] + [_display_width(backup.backup_id) for backup in backups] or [16])))
    count_width = max(_display_width(count_label), 6)
    reason_width = max(18, table_width - 7 - id_width - count_width)
    lines = _form_status_lines(t(language, "backup_delete_title"), language, provider_status, model_status, color_enabled)
    lines.extend(
        [
            _role(t(language, "backup_delete_title"), "danger", color_enabled),
            _role("────────────────────────────────────────", "muted", color_enabled),
        ]
    )
    if not backups:
        lines.extend(["", _role(t(language, "backup_delete_empty"), "muted", color_enabled)])
    else:
        lines.append(f"  {_fit('ID', id_width)}  {_fit_right(count_label, count_width)}  {_fit(reason_label, reason_width)}")
        start, visible = _visible_backup_window(backups, selected_index)
        if start > 0:
            lines.append(_role(_fit(f"... {start} more above", table_width), "muted", color_enabled))
        for offset, backup in enumerate(visible):
            index = start + offset
            focused = index == selected_index
            marker = "›" if focused else " "
            checkbox = "[x]" if backup.backup_id in selected_set else "[ ]"
            row = (
                f"{marker} {checkbox} "
                f"{_fit_middle(backup.backup_id, id_width)}  "
                f"{_fit_right(str(_backup_existing_count(backup)), count_width)}  "
                f"{_fit(backup.reason or '-', reason_width)}"
            )
            row = _fit(row, table_width)
            lines.append(_role(row, "selected", color_enabled) if focused else row)
        remaining = len(backups) - start - len(visible)
        if remaining > 0:
            lines.append(_role(_fit(f"... {remaining} more below", table_width), "muted", color_enabled))
    lines.extend(["", _selected_backups_summary(language, selected_backup_ids), "", _role(t(language, "backup_delete_help"), "muted", color_enabled)])
    return "\n".join(lines) + "\n"


def render_backup_delete_confirm(
    language: str,
    backup_ids: Sequence[str],
    color: Optional[bool] = None,
) -> str:
    color_enabled = _supports_color() if color is None else color
    provider_status, model_status = _menu_status()
    lines = _form_status_lines(t(language, "backup_delete_title"), language, provider_status, model_status, color_enabled)
    lines.extend(
        [
            _role(t(language, "backup_delete_title"), "danger", color_enabled),
            _role("────────────────────────────────────────", "muted", color_enabled),
        ]
    )
    for backup_id in backup_ids[:12]:
        lines.append(f"- {backup_id}")
    if len(backup_ids) > 12:
        lines.append(f"... +{len(backup_ids) - 12}")
    lines.extend(["", _role(t(language, "backup_delete_confirm_help"), "muted", color_enabled)])
    return "\n".join(lines) + "\n"


def render_model_selector(
    language: str,
    provider_id: str,
    models: Sequence[str],
    filter_text: str,
    selected_index: int,
    selected_models: Sequence[str] = (),
    color: Optional[bool] = None,
) -> str:
    color_enabled = _supports_color() if color is None else color
    filtered = _filter_models(models, filter_text)
    selected_set = set(selected_models)
    if filtered:
        selected_index = min(max(selected_index, 0), len(filtered) - 1)
    title = f"{t(language, 'model_selector_title')} - {provider_id}"
    lines = [
        _role(title, "focus", color_enabled),
        _role("────────────────────────────────────────", "muted", color_enabled),
        f"{t(language, 'model_selector_filter')}: {filter_text or '-'}",
        "",
    ]
    if not filtered:
        lines.append(_role(t(language, "model_selector_empty"), "muted", color_enabled))
    else:
        start, visible = _visible_model_window(filtered, selected_index)
        if start > 0:
            lines.append(_role(f"... {start} more above", "muted", color_enabled))
        for offset, model_id in enumerate(visible):
            index = start + offset
            marker = _role("›", "focus", color_enabled) if index == selected_index else " "
            checkbox = _role("[x]", "read", color_enabled) if model_id in selected_set else "[ ]"
            value = _role(model_id, "value", color_enabled) if index == selected_index else model_id
            lines.append(f"{marker} {checkbox} {value}")
        remaining = len(filtered) - start - len(visible)
        if remaining > 0:
            lines.append(_role(f"... {remaining} more below", "muted", color_enabled))
    lines.extend(["", _selected_models_summary(language, selected_models), "", _role(t(language, "model_selector_help"), "muted", color_enabled)])
    return "\n".join(lines) + "\n"


def render_model_delete_selector(
    language: str,
    provider_id: str,
    models: Sequence[str],
    filter_text: str,
    selected_index: int,
    selected_models: Sequence[str] = (),
    current_model: str = "",
    color: Optional[bool] = None,
) -> str:
    color_enabled = _supports_color() if color is None else color
    filtered = _filter_models(models, filter_text)
    selected_set = set(selected_models)
    if filtered:
        selected_index = min(max(selected_index, 0), len(filtered) - 1)
    title = f"{t(language, 'model_delete_selector_title')} - {provider_id}"
    lines = [
        _role(title, "danger", color_enabled),
        _role("────────────────────────────────────────", "muted", color_enabled),
        f"{t(language, 'model_selector_filter')}: {filter_text or '-'}",
        "",
    ]
    if not models:
        lines.append(_role(t(language, "model_delete_empty"), "muted", color_enabled))
    elif not filtered:
        lines.append(_role(t(language, "model_selector_empty"), "muted", color_enabled))
    else:
        start, visible = _visible_model_window(filtered, selected_index)
        if start > 0:
            lines.append(_role(f"... {start} more above", "muted", color_enabled))
        for offset, model_id in enumerate(visible):
            index = start + offset
            marker = _role("›", "focus", color_enabled) if index == selected_index else " "
            checkbox = _role("[x]", "danger", color_enabled) if model_id in selected_set else "[ ]"
            value = _role(model_id, "value", color_enabled) if index == selected_index else model_id
            current = _role(f" ({t(language, 'default_model_current')})", "muted", color_enabled) if model_id == current_model else ""
            lines.append(f"{marker} {checkbox} {value}{current}")
        remaining = len(filtered) - start - len(visible)
        if remaining > 0:
            lines.append(_role(f"... {remaining} more below", "muted", color_enabled))
    lines.extend(
        [
            "",
            _selected_models_summary(language, selected_models, label_key="model_delete_selected"),
            "",
            _role(t(language, "model_delete_selector_help"), "muted", color_enabled),
        ]
    )
    return "\n".join(lines) + "\n"


def _model_selector_cursor_position(language: str, filter_text: str) -> Tuple[int, int]:
    prefix = f"{t(language, 'model_selector_filter')}: "
    return 3, _display_width(prefix) + _display_width(filter_text or "-") + 1


def render_default_model_selector(
    language: str,
    models: Sequence[str],
    filter_text: str,
    selected_index: int,
    current_model: str = "",
    color: Optional[bool] = None,
) -> str:
    color_enabled = _supports_color() if color is None else color
    filtered = _filter_models(models, filter_text)
    if filtered:
        selected_index = min(max(selected_index, 0), len(filtered) - 1)
    title = t(language, "default_model_selector_title")
    lines = [
        _role(title, "focus", color_enabled),
        _role("────────────────────────────────────────", "muted", color_enabled),
        f"{t(language, 'model_selector_filter')}: {filter_text or '-'}",
        "",
    ]
    if not filtered:
        lines.append(_role(t(language, "model_selector_empty"), "muted", color_enabled))
    else:
        start, visible = _visible_model_window(filtered, selected_index)
        if start > 0:
            lines.append(_role(f"... {start} more above", "muted", color_enabled))
        for offset, model_id in enumerate(visible):
            index = start + offset
            marker = _role("›", "focus", color_enabled) if index == selected_index else " "
            value = _role(model_id, "value", color_enabled) if index == selected_index else model_id
            current = _role(f" ({t(language, 'default_model_current')})", "muted", color_enabled) if model_id == current_model else ""
            lines.append(f"{marker} {value}{current}")
        remaining = len(filtered) - start - len(visible)
        if remaining > 0:
            lines.append(_role(f"... {remaining} more below", "muted", color_enabled))
    lines.extend(["", _role(t(language, "default_model_selector_help"), "muted", color_enabled)])
    return "\n".join(lines) + "\n"


def _selected_model_index(models: Sequence[str], current_model: str) -> int:
    if current_model:
        try:
            return list(models).index(current_model)
        except ValueError:
            pass
    return 0


def _run_default_model_selector(language: str, models: Sequence[str], current_model: str = "") -> Optional[str]:
    filter_text = ""
    selected_index = _selected_model_index(models, current_model)
    previous_line_count = 0
    first_draw = True
    while True:
        filtered = _filter_models(models, filter_text)
        if filtered:
            selected_index = min(max(selected_index, 0), len(filtered) - 1)
        else:
            selected_index = 0
        text = render_default_model_selector(language, models, filter_text, selected_index, current_model)
        if first_draw:
            _clear_screen()
            previous_line_count = 0
        previous_line_count = _write_focus_form_update(
            text,
            previous_line_count,
            _model_selector_cursor_position(language, filter_text),
        )
        first_draw = False
        key = _read_key()
        if key in {"\x03", "\x04"}:
            raise TuiInterrupted
        if key in {"\x1b", ""}:
            return None
        if key in {"\r", "\n", " "} and filtered:
            return filtered[selected_index]
        if _is_next_focus_key(key) and filtered:
            selected_index = (selected_index + 1) % len(filtered)
            continue
        if _is_previous_focus_key(key) and filtered:
            selected_index = (selected_index - 1) % len(filtered)
            continue
        if key in {"\x7f", "\b"}:
            filter_text = filter_text[:-1]
            selected_index = 0
            continue
        if key == "\x15":
            filter_text = ""
            selected_index = _selected_model_index(models, current_model)
            continue
        if key.startswith("\x1b"):
            continue
        if len(key) == 1 and key.isprintable():
            filter_text += key
            selected_index = 0


def render_model_health_check(
    language: str,
    provider_id: str,
    model_ids: Sequence[str],
    results: Sequence[ModelHealthResult] = (),
    checking_model: str = "",
    color: Optional[bool] = None,
) -> str:
    color_enabled = _supports_color() if color is None else color
    result_by_model = {result.model_id: result for result in results}
    passed = [result for result in results if result.ok]
    failed = [result for result in results if not result.ok]
    title = f"{t(language, 'model_health_title')} - {provider_id}"
    lines = [
        _role(title, "focus", color_enabled),
        _role("────────────────────────────────────────", "muted", color_enabled),
        "",
    ]
    for model_id in model_ids:
        result = result_by_model.get(model_id)
        if result is None:
            status = "[...] " if model_id == checking_model else "[ ]   "
            detail = t(language, "model_health_checking") if model_id == checking_model else ""
            lines.append(f"{status}{model_id}{('  ' + detail) if detail else ''}")
            continue
        if result.ok:
            lines.append(f"{_role('[OK]  ', 'read', color_enabled)}{model_id}  {result.detail}")
        else:
            lines.append(f"{_role('[FAIL]', 'danger', color_enabled)} {model_id}  {result.detail}")
    lines.extend(
        [
            "",
            t(language, "model_health_summary").format(passed=len(passed), failed=len(failed)),
            "",
            _role(t(language, "model_health_help"), "muted", color_enabled),
        ]
    )
    return "\n".join(lines) + "\n"


def _run_model_health_check(language: str, provider_id: str, model_ids: Sequence[str]) -> Optional[List[str]]:
    ordered_models = list(dict.fromkeys(model_ids))
    results_by_model: Dict[str, ModelHealthResult] = {}
    models_to_check = list(ordered_models)
    while True:
        for model_id in models_to_check:
            _clear_screen()
            _write_raw_text(
                render_model_health_check(
                    language,
                    provider_id,
                    ordered_models,
                    [results_by_model[item] for item in ordered_models if item in results_by_model],
                    checking_model=model_id,
                )
            )
            results_by_model[model_id] = _check_model_health(provider_id, model_id)
        _clear_screen()
        _write_raw_text(
            render_model_health_check(
                language,
                provider_id,
                ordered_models,
                [results_by_model[item] for item in ordered_models if item in results_by_model],
            )
        )
        key = _read_key()
        if key in {"\x03", "\x04"}:
            raise TuiInterrupted
        if key in {"\x1b", ""}:
            return None
        if key == "\x12":
            models_to_check = [model_id for model_id in ordered_models if not results_by_model[model_id].ok]
            if models_to_check:
                continue
        if key in {"\r", "\n"}:
            return [model_id for model_id in ordered_models if results_by_model[model_id].ok]


def _run_model_selector(language: str, provider_id: str, models: Sequence[str]) -> Optional[List[str]]:
    filter_text = ""
    selected_index = 0
    selected_models: List[str] = []
    previous_line_count = 0
    first_draw = True
    while True:
        filtered = _filter_models(models, filter_text)
        if filtered:
            selected_index = min(max(selected_index, 0), len(filtered) - 1)
        else:
            selected_index = 0
        text = render_model_selector(language, provider_id, models, filter_text, selected_index, selected_models)
        if first_draw:
            _clear_screen()
            previous_line_count = 0
        previous_line_count = _write_focus_form_update(
            text,
            previous_line_count,
            _model_selector_cursor_position(language, filter_text),
        )
        first_draw = False
        key = _read_key()
        if key in {"\x03", "\x04"}:
            raise TuiInterrupted
        if key in {"\x1b", ""}:
            return None
        if key in {"\r", "\n"} and filtered:
            checked_models = _run_model_health_check(language, provider_id, selected_models or [filtered[selected_index]])
            if checked_models is None:
                first_draw = True
                previous_line_count = 0
                continue
            return checked_models
        if key == " " and filtered:
            model_id = filtered[selected_index]
            if model_id in selected_models:
                selected_models = [item for item in selected_models if item != model_id]
            else:
                selected_models.append(model_id)
            continue
        if _is_next_focus_key(key) and filtered:
            selected_index = (selected_index + 1) % len(filtered)
            continue
        if _is_previous_focus_key(key) and filtered:
            selected_index = (selected_index - 1) % len(filtered)
            continue
        if key in {"\x7f", "\b"}:
            filter_text = filter_text[:-1]
            selected_index = 0
            continue
        if key == "\x15":
            filter_text = ""
            selected_index = 0
            continue
        if key.startswith("\x1b"):
            continue
        if len(key) == 1 and key.isprintable():
            filter_text += key
            selected_index = 0


def _run_model_delete_selector(
    language: str,
    provider_id: str,
    models: Sequence[str],
    selected_models: Sequence[str] = (),
    current_model: str = "",
) -> Optional[List[str]]:
    filter_text = ""
    selected_index = _selected_model_index(models, current_model)
    selected_ids = [model_id for model_id in selected_models if model_id in set(models)]
    previous_line_count = 0
    first_draw = True
    while True:
        filtered = _filter_models(models, filter_text)
        if filtered:
            selected_index = min(max(selected_index, 0), len(filtered) - 1)
        else:
            selected_index = 0
        text = render_model_delete_selector(
            language,
            provider_id,
            models,
            filter_text,
            selected_index,
            selected_ids,
            current_model,
        )
        if first_draw:
            _clear_screen()
            previous_line_count = 0
        previous_line_count = _write_focus_form_update(
            text,
            previous_line_count,
            _model_selector_cursor_position(language, filter_text),
        )
        first_draw = False
        key = _read_key()
        if key in {"\x03", "\x04"}:
            raise TuiInterrupted
        if key in {"\x1b", "", "q", "Q"}:
            return None
        if key in {"\r", "\n"}:
            if selected_ids:
                return selected_ids
            if filtered:
                return [filtered[selected_index]]
            continue
        if key == " " and filtered:
            model_id = filtered[selected_index]
            if model_id in selected_ids:
                selected_ids = [item for item in selected_ids if item != model_id]
            else:
                selected_ids.append(model_id)
            continue
        if key == "\x01" and filtered:
            filtered_set = set(filtered)
            if all(model_id in selected_ids for model_id in filtered):
                selected_ids = [item for item in selected_ids if item not in filtered_set]
            else:
                selected_ids.extend(model_id for model_id in filtered if model_id not in selected_ids)
            continue
        if _is_next_focus_key(key) and filtered:
            selected_index = (selected_index + 1) % len(filtered)
            continue
        if _is_previous_focus_key(key) and filtered:
            selected_index = (selected_index - 1) % len(filtered)
            continue
        if key in {"\x7f", "\b"}:
            filter_text = filter_text[:-1]
            selected_index = 0
            continue
        if key == "\x15":
            filter_text = ""
            selected_index = _selected_model_index(models, current_model)
            continue
        if key.startswith("\x1b"):
            continue
        if len(key) == 1 and key.isprintable():
            filter_text += key
            selected_index = 0


def _model_selector_hint_lines(
    model_selector: bool,
    fields: Sequence[FormField],
    focus_index: int,
    language: str,
    catalog_model_selector: bool = False,
    provider_default_model_selector: bool = False,
    model_delete_selector: bool = False,
) -> List[str]:
    if not fields:
        return []
    focus_index = min(max(focus_index, 0), len(fields) - 1)
    if provider_default_model_selector and fields[focus_index].key == "default_model":
        return [t(language, "provider_default_model_select_hint")]
    if fields[focus_index].key != "model_id":
        return []
    if model_delete_selector:
        return [t(language, "model_delete_select_hint")]
    if catalog_model_selector:
        return [t(language, "default_model_select_hint")]
    if model_selector:
        return [t(language, "model_select_hint")]
    return []


def _bulk_model_ids(values: Dict[str, str]) -> List[str]:
    raw = values.get("__bulk_model_ids", "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    result = []
    seen = set()
    for item in parsed:
        model_id = str(item or "").strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            result.append(model_id)
    return result


def _set_bulk_model_ids(values: Dict[str, str], model_ids: Sequence[str]) -> None:
    if len(model_ids) > 1:
        values["__bulk_model_ids"] = json.dumps(list(model_ids), ensure_ascii=False)
    else:
        values.pop("__bulk_model_ids", None)


def _clear_bulk_model_ids_for_field(values: Dict[str, str], field: FormField) -> None:
    if field.key in {"model_id", "provider_id"}:
        values.pop("__bulk_model_ids", None)
    if field.key in {"base_url", "api_key", "default_model", "api_format"}:
        _clear_provider_default_health(values)


def _bulk_model_message_lines(language: str, model_ids: Sequence[str]) -> List[str]:
    return [
        t(language, "model_bulk_ready").format(count=len(model_ids)),
        _selected_models_summary(language, model_ids),
        t(language, "model_bulk_no_default"),
    ]


def _model_delete_bulk_message_lines(language: str, model_ids: Sequence[str]) -> List[str]:
    return [
        t(language, "model_delete_bulk_ready").format(count=len(model_ids)),
        _selected_models_summary(language, model_ids, label_key="model_delete_selected"),
    ]


def _build_model_add_argv(
    values: Dict[str, str],
    model: str,
    allow_default: bool = True,
    allow_profile: bool = True,
) -> List[str]:
    argv = [
        "model",
        "add",
        model,
        "--display-name",
        values["display_name"] or model,
        "--context-window",
        values["context_window"],
        "--provider-id",
        values["provider_id"],
        "--yes",
    ]
    if allow_profile and values["profile_name"]:
        argv.extend(["--profile", values["profile_name"]])
    if allow_default and values["make_default"] == "yes":
        argv.append("--default")
    return argv


def _validate_form(fields: Sequence[FormField], values: Dict[str, str], language: str) -> List[str]:
    errors: List[str] = []
    for field in fields:
        value = values.get(field.key, "").strip()
        if field.required and not value:
            errors.append(f"{field.label}: {t(language, 'form_required')}")
        if value and field.choices and value not in field.choices:
            errors.append(f"{field.label}: {t(language, 'form_choice_error')} {'/'.join(field.choices)}")
    return errors


def _edit_field(field: FormField, current: str) -> str:
    prompt = field.label
    if field.choices:
        prompt = f"{prompt} ({'/'.join(field.choices)})"
    if field.secret:
        write_text(f"› {prompt}{' [已设置]' if current else ''}: ")
        try:
            value = input().strip()
        except (KeyboardInterrupt, EOFError) as exc:
            raise TuiInterrupted from exc
        return value or current
    return _ask(prompt, current)


def _read_key() -> str:
    fd = sys.stdin.fileno()
    first = os.read(fd, 1)
    if not first:
        return ""
    if first != b"\x1b":
        return _decode_input_bytes(fd, first)
    sequence = first
    while True:
        ready, _, _ = select.select([fd], [], [], ESC_SEQUENCE_TIMEOUT_SECONDS)
        if not ready:
            break
        next_byte = os.read(fd, 1)
        if not next_byte:
            break
        sequence += next_byte
        text = sequence.decode("utf-8", errors="ignore")
        if _escape_sequence_complete(text) or len(sequence) >= 16:
            break
    return sequence.decode("utf-8", errors="ignore")


def _decode_input_bytes(fd: int, first: bytes) -> str:
    expected = _utf8_expected_length(first[0])
    data = first
    while len(data) < expected:
        ready, _, _ = select.select([fd], [], [], 0.02)
        if not ready:
            break
        data += os.read(fd, 1)
    return data.decode("utf-8", errors="ignore")


def _utf8_expected_length(first_byte: int) -> int:
    if first_byte < 0x80:
        return 1
    if 0xC0 <= first_byte < 0xE0:
        return 2
    if 0xE0 <= first_byte < 0xF0:
        return 3
    if 0xF0 <= first_byte < 0xF8:
        return 4
    return 1


def _escape_sequence_complete(sequence: str) -> bool:
    if len(sequence) < 3 or not sequence.startswith("\x1b"):
        return False
    if sequence.startswith("\x1b["):
        final = sequence[-1]
        return "@" <= final <= "~"
    if sequence.startswith("\x1bO"):
        return len(sequence) >= 3
    return len(sequence) >= 2


def _csi_key(key: str, final: str) -> bool:
    return bool(re.fullmatch(rf"\x1b\[[0-9;?]*{re.escape(final)}", key))


def _ss3_key(key: str, final: str) -> bool:
    return key == f"\x1bO{final}"


def _is_next_focus_key(key: str) -> bool:
    return key in {"\t", "\r", "\n"} or _csi_key(key, "B") or _ss3_key(key, "B")


def _is_previous_focus_key(key: str) -> bool:
    return _csi_key(key, "A") or _csi_key(key, "Z") or _ss3_key(key, "A")


def _is_right_key(key: str) -> bool:
    return _csi_key(key, "C") or _ss3_key(key, "C")


def _is_left_key(key: str) -> bool:
    return _csi_key(key, "D") or _ss3_key(key, "D")


def _menu_action_numbers(language: str) -> List[str]:
    return [number for _, items in menu_groups(language) for number, _, _, _ in items]


def _move_menu_selection(language: str, selected_action: str, direction: int) -> str:
    numbers = _menu_action_numbers(language)
    if not numbers:
        return selected_action
    try:
        index = numbers.index(selected_action)
    except ValueError:
        index = 0
    return numbers[(index + direction) % len(numbers)]


def _menu_cursor_position(text: str) -> Tuple[int, int]:
    lines = _focus_form_lines(text)
    return max(1, len(lines)), 4


def _read_menu_choice_focus(language: str, selected_action: str) -> Tuple[str, str]:
    previous_line_count = 0
    first_draw = True
    digit_buffer = ""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            provider_status, model_status = _menu_status()
            text = render_menu(
                language=language,
                provider_status=provider_status,
                model_status=model_status,
                selected_action=selected_action,
            )
            if first_draw:
                _clear_screen()
                previous_line_count = 0
            previous_line_count = _write_focus_form_update(text, previous_line_count, _menu_cursor_position(text))
            first_draw = False
            key = _read_key()
            if key in {"\x03", "\x04"}:
                raise TuiInterrupted
            if key in {"q", "Q", "\x1b", ""}:
                _clear_screen()
                return "q", selected_action
            if key in {"\r", "\n"}:
                _clear_screen()
                return selected_action, selected_action
            if _is_next_focus_key(key):
                selected_action = _move_menu_selection(language, selected_action, 1)
                digit_buffer = ""
                continue
            if _is_previous_focus_key(key):
                selected_action = _move_menu_selection(language, selected_action, -1)
                digit_buffer = ""
                continue
            if len(key) == 1 and key.isdigit():
                valid_numbers = _menu_action_numbers(language)
                candidate = digit_buffer + key
                if candidate in valid_numbers:
                    selected_action = candidate
                    digit_buffer = candidate if any(number.startswith(candidate) and number != candidate for number in valid_numbers) else ""
                    continue
                if key in valid_numbers:
                    selected_action = key
                    digit_buffer = key if any(number.startswith(key) and number != key for number in valid_numbers) else ""
                    continue
                digit_buffer = ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _next_choice(field: FormField, current: str, direction: int = 1) -> str:
    if not field.choices:
        return current
    try:
        index = field.choices.index(current)
    except ValueError:
        index = 0 if direction >= 0 else len(field.choices) - 1
    else:
        index = (index + direction) % len(field.choices)
    return field.choices[index]


def _supports_focus_form() -> bool:
    return bool(
        hasattr(sys.stdin, "isatty")
        and hasattr(sys.stdout, "isatty")
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and os.name == "posix"
        and termios is not None
        and tty is not None
    )


def _clear_screen() -> None:
    write_text("\r\033[2J\033[H")


def _raw_newlines(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "\r\n")


def _write_raw_text(text: str) -> None:
    write_text(_raw_newlines(text))


def _hide_cursor() -> None:
    write_text("\033[?25l")


def _show_cursor() -> None:
    write_text("\033[?25h")


def _focus_form_lines(text: str) -> List[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _focus_form_cursor_position(fields: Sequence[FormField], values: Dict[str, str], focus_index: int) -> Tuple[int, int]:
    if not fields:
        return 1, 1
    focus_index = min(max(focus_index, 0), len(fields) - 1)
    label_width = _field_label_width(fields)
    field = fields[focus_index]
    value = _format_field_value(field, values.get(field.key, ""))
    prefix = f"› {_fit(_field_label(field), label_width)} [ "
    return FOCUS_FORM_FIELD_START_ROW + focus_index, _display_width(prefix) + _display_width(value) + 1


def _write_focus_form_update(
    text: str,
    previous_line_count: int,
    cursor_position: Tuple[int, int],
) -> int:
    lines = _focus_form_lines(text)
    line_count = max(len(lines), previous_line_count)
    cursor_row, cursor_column = cursor_position
    _hide_cursor()
    try:
        write_text("\r\033[H")
        for index in range(line_count):
            if index < len(lines):
                _write_raw_text(lines[index])
            write_text("\033[K")
            if index < line_count - 1:
                write_text("\r\n")
        write_text(f"\r\033[{cursor_row};{cursor_column}H")
    finally:
        _show_cursor()
    return len(lines)


def _redraw_focus_form(
    title: str,
    fields: Sequence[FormField],
    values: Dict[str, str],
    focus_index: int,
    language: str,
    errors: Sequence[str],
    message_lines: Sequence[str],
    provider_status: str,
    model_status: str,
    previous_line_count: int = 0,
    first_draw: bool = False,
) -> int:
    if first_draw:
        _clear_screen()
        previous_line_count = 0
    return _write_focus_form_update(
        render_focus_form(
            title,
            fields,
            values,
            focus_index,
            language,
            errors,
            message_lines,
            provider_status=provider_status,
            model_status=model_status,
        ),
        previous_line_count,
        _focus_form_cursor_position(fields, values, focus_index),
    )


def _run_focus_form(
    language: str,
    title: str,
    fields: Sequence[FormField],
    build_argv: Callable[[Dict[str, str]], List[str]],
    preview_lines: Sequence[str],
    confirm: bool,
    execute: Optional[Callable[[Dict[str, str]], Optional[str]]],
    model_selector: bool = False,
    catalog_model_selector: bool = False,
    provider_default_model_selector: bool = False,
    model_delete_selector: bool = False,
    on_change: Optional[Callable[[Dict[str, str], FormField], Sequence[str]]] = None,
) -> Optional[str]:
    values = {field.key: field.default for field in fields}
    focus_index = 0
    errors: List[str] = []
    message_lines: List[str] = _model_selector_hint_lines(
        model_selector,
        fields,
        focus_index,
        language,
        catalog_model_selector,
        provider_default_model_selector,
        model_delete_selector,
    )
    confirm_pending = False
    first_draw = True
    previous_line_count = 0
    provider_status, model_status = _menu_status()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    restored = False

    def apply_field_change(changed_field: FormField) -> List[str]:
        _clear_bulk_model_ids_for_field(values, changed_field)
        if on_change:
            return list(on_change(values, changed_field))
        return []

    try:
        tty.setraw(fd)
        while True:
            previous_line_count = _redraw_focus_form(
                title,
                fields,
                values,
                focus_index,
                language,
                errors,
                message_lines,
                provider_status,
                model_status,
                previous_line_count=previous_line_count,
                first_draw=first_draw,
            )
            first_draw = False
            key = _read_key()
            errors = []
            if key == "":
                _clear_screen()
                _write_raw_text(t(language, "cancelled") + "\n")
                return None
            if key in {"\x03", "\x04"}:
                raise TuiInterrupted
            if key == "\x1b":
                _clear_screen()
                _write_raw_text(t(language, "cancelled") + "\n")
                return None
            if _is_next_focus_key(key):
                focus_index = (focus_index + 1) % max(1, len(fields))
                confirm_pending = False
                message_lines = _model_selector_hint_lines(
                    model_selector,
                    fields,
                    focus_index,
                    language,
                    catalog_model_selector,
                    provider_default_model_selector,
                    model_delete_selector,
                )
                continue
            if _is_previous_focus_key(key):
                focus_index = (focus_index - 1) % max(1, len(fields))
                confirm_pending = False
                message_lines = _model_selector_hint_lines(
                    model_selector,
                    fields,
                    focus_index,
                    language,
                    catalog_model_selector,
                    provider_default_model_selector,
                    model_delete_selector,
                )
                continue
            if key == "\x10":
                form_errors = _validate_form(fields, values, language)
                if form_errors:
                    errors = form_errors
                    message_lines = []
                else:
                    message_lines = ["预览", "────────────────────────────────────────"]
                    message_lines.extend(f"- {line}" for line in preview_lines)
                    message_lines.extend(["命令:", " ".join(_mask_argv(build_argv(values)))])
                confirm_pending = False
                continue
            if key == "\x13":
                form_errors = _validate_form(fields, values, language)
                if form_errors:
                    errors = form_errors
                    message_lines = []
                    confirm_pending = False
                    continue
                submit_messages: List[str] = []
                if provider_default_model_selector and not _provider_default_health_matches(values):
                    message_lines = [t(language, "provider_model_health_checking")]
                    previous_line_count = _redraw_focus_form(
                        title,
                        fields,
                        values,
                        focus_index,
                        language,
                        errors,
                        message_lines,
                        provider_status,
                        model_status,
                        previous_line_count=previous_line_count,
                    )
                    try:
                        result = _check_provider_default_model_health(values)
                    except ValueError:
                        errors = [t(language, "provider_model_api_required")]
                        message_lines = []
                        confirm_pending = False
                        continue
                    if not result.ok:
                        errors = [t(language, "provider_model_health_failed").format(detail=result.detail)]
                        message_lines = []
                        confirm_pending = False
                        continue
                    submit_messages = [t(language, "provider_model_health_passed").format(detail=result.detail)]
                if confirm and not confirm_pending:
                    message_lines = submit_messages + [t(language, "focus_form_confirm")]
                    confirm_pending = True
                    continue
                _clear_screen()
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                restored = True
                if execute:
                    return execute(values)
                _run_output_command(language, title, build_argv(values))
                return None
            if not fields:
                continue
            field = fields[focus_index]
            current = values.get(field.key, "")
            if provider_default_model_selector and field.key == "default_model" and key in {"\x0c", " "}:
                try:
                    base_url, api_key = _provider_default_form_api(values)
                except ValueError:
                    message_lines = [t(language, "provider_model_api_required")]
                    confirm_pending = False
                    continue
                message_lines = [t(language, "provider_model_fetching")]
                previous_line_count = _redraw_focus_form(
                    title,
                    fields,
                    values,
                    focus_index,
                    language,
                    errors,
                    message_lines,
                    provider_status,
                    model_status,
                    previous_line_count=previous_line_count,
                )
                try:
                    models = _fetch_models_from_api(base_url, api_key)
                except ValueError as exc:
                    message_lines = [f"{t(language, 'provider_model_fetch_failed')}: {exc}"]
                    confirm_pending = False
                    continue
                selected_model = _run_default_model_selector(language, models, current)
                first_draw = True
                previous_line_count = 0
                if not selected_model:
                    message_lines = [t(language, "provider_default_model_select_hint")]
                    confirm_pending = False
                    continue
                previous_value = values.get(field.key, "")
                values[field.key] = selected_model
                _clear_provider_default_health(values)
                _clear_screen()
                message_lines = [t(language, "provider_model_health_checking")]
                previous_line_count = _redraw_focus_form(
                    title,
                    fields,
                    values,
                    focus_index,
                    language,
                    errors,
                    message_lines,
                    provider_status,
                    model_status,
                    previous_line_count=0,
                    first_draw=True,
                )
                result = _check_provider_default_model_health(values)
                first_draw = True
                previous_line_count = 0
                if result.ok:
                    message_lines = [
                        f"{t(language, 'provider_model_selected')}: {selected_model}",
                        t(language, "provider_model_health_passed").format(detail=result.detail),
                    ]
                else:
                    values[field.key] = previous_value
                    _clear_provider_default_health(values)
                    message_lines = [t(language, "provider_model_health_failed").format(detail=result.detail)]
                confirm_pending = False
                continue
            if catalog_model_selector and field.key == "model_id" and key in {"\x0c", " "}:
                message_lines = [t(language, "default_model_loading")]
                previous_line_count = _redraw_focus_form(
                    title,
                    fields,
                    values,
                    focus_index,
                    language,
                    errors,
                    message_lines,
                    provider_status,
                    model_status,
                    previous_line_count=previous_line_count,
                )
                try:
                    models = _catalog_model_choices()
                except ValueError as exc:
                    message_lines = [f"{t(language, 'default_model_load_failed')}: {exc}"]
                    confirm_pending = False
                    continue
                current_model = current or model_status
                if current_model in {"-", "unknown", "dynamic"}:
                    current_model = ""
                selected_model = _run_default_model_selector(language, models, current_model)
                first_draw = True
                previous_line_count = 0
                if selected_model:
                    values[field.key] = selected_model
                    message_lines = [f"{t(language, 'default_model_selected')}: {selected_model}"]
                else:
                    message_lines = [t(language, "default_model_select_hint")]
                confirm_pending = False
                continue
            if model_delete_selector and field.key == "model_id" and key in {"\x0c", " "}:
                provider_id = values.get("provider_id") or _current_provider_id()
                message_lines = [t(language, "model_delete_loading")]
                previous_line_count = _redraw_focus_form(
                    title,
                    fields,
                    values,
                    focus_index,
                    language,
                    errors,
                    message_lines,
                    provider_status,
                    model_status,
                    previous_line_count=previous_line_count,
                )
                try:
                    models = _provider_model_choices(provider_id)
                except ValueError as exc:
                    message_lines = [f"{t(language, 'model_delete_load_failed')}: {exc}"]
                    confirm_pending = False
                    continue
                if not models:
                    message_lines = [t(language, "model_delete_empty")]
                    confirm_pending = False
                    continue
                current_model = current if current in models else model_status
                selected_models = _run_model_delete_selector(
                    language,
                    provider_id,
                    models,
                    _bulk_model_ids(values) or ([current] if current in models else []),
                    current_model if current_model in models else "",
                )
                first_draw = True
                previous_line_count = 0
                if selected_models:
                    values[field.key] = selected_models[0]
                    _set_bulk_model_ids(values, selected_models)
                    if len(selected_models) > 1:
                        message_lines = _model_delete_bulk_message_lines(language, selected_models)
                    else:
                        message_lines = [f"{t(language, 'model_delete_selected')}: {selected_models[0]}"]
                else:
                    message_lines = [t(language, "model_delete_select_hint")]
                confirm_pending = False
                continue
            if model_selector and field.key == "model_id" and key in {"\x0c", " "}:
                provider_id = values.get("provider_id") or _current_provider_id()
                message_lines = [t(language, "model_fetching")]
                previous_line_count = _redraw_focus_form(
                    title,
                    fields,
                    values,
                    focus_index,
                    language,
                    errors,
                    message_lines,
                    provider_status,
                    model_status,
                    previous_line_count=previous_line_count,
                )
                try:
                    models = _fetch_provider_models(provider_id)
                except ValueError as exc:
                    message_lines = [f"{t(language, 'model_fetch_failed')}: {exc}"]
                    confirm_pending = False
                    continue
                selected_models = _run_model_selector(language, provider_id, models)
                first_draw = True
                previous_line_count = 0
                if selected_models:
                    values[field.key] = selected_models[0]
                    _set_bulk_model_ids(values, selected_models)
                    if len(selected_models) > 1:
                        message_lines = _bulk_model_message_lines(language, selected_models)
                    else:
                        message_lines = [f"{t(language, 'model_selected')}: {selected_models[0]}"]
                elif selected_models == []:
                    message_lines = [t(language, "model_health_no_pass")]
                else:
                    message_lines = [t(language, "model_select_hint")]
                confirm_pending = False
                continue
            if field.choices and (key == " " or _is_right_key(key)):
                values[field.key] = _next_choice(field, current, 1)
                confirm_pending = False
                message_lines = apply_field_change(field)
                continue
            if field.choices and _is_left_key(key):
                values[field.key] = _next_choice(field, current, -1)
                confirm_pending = False
                message_lines = apply_field_change(field)
                continue
            if key.startswith("\x1b"):
                continue
            if key in {"\x7f", "\b"} and not field.choices:
                values[field.key] = current[:-1]
                confirm_pending = False
                message_lines = apply_field_change(field)
                continue
            if key == "\x15" and not field.choices:
                values[field.key] = ""
                confirm_pending = False
                message_lines = apply_field_change(field)
                continue
            if len(key) == 1 and key.isprintable() and not field.choices:
                values[field.key] = current + key
                confirm_pending = False
                message_lines = apply_field_change(field)
                continue
    finally:
        if not restored:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _print_preview(argv: Sequence[str], preview_lines: Sequence[str]) -> None:
    print("预览")
    print("────────────────────────────────────────")
    if preview_lines:
        for line in preview_lines:
            print(f"- {line}")
    print("命令:")
    print(" ".join(_mask_argv(argv)))


def _mask_argv(argv: Sequence[str]) -> List[str]:
    masked: List[str] = []
    secret_next = False
    for item in argv:
        if secret_next:
            masked.append("********")
            secret_next = False
            continue
        masked.append(item)
        secret_next = item in {"--api-key", "--token", "--password"}
    return masked


def _run_form(
    language: str,
    title: str,
    fields: Sequence[FormField],
    build_argv: Callable[[Dict[str, str]], List[str]],
    preview_lines: Sequence[str] = (),
    confirm: bool = True,
    execute: Optional[Callable[[Dict[str, str]], Optional[str]]] = None,
    model_selector: bool = False,
    catalog_model_selector: bool = False,
    provider_default_model_selector: bool = False,
    model_delete_selector: bool = False,
    on_change: Optional[Callable[[Dict[str, str], FormField], Sequence[str]]] = None,
) -> Optional[str]:
    if _supports_focus_form():
        return _run_focus_form(
            language,
            title,
            fields,
            build_argv,
            preview_lines,
            confirm,
            execute,
            model_selector,
            catalog_model_selector,
            provider_default_model_selector,
            model_delete_selector,
            on_change,
        )

    values = {field.key: field.default for field in fields}
    errors: List[str] = []
    while True:
        write_text(render_action_form(title, fields, values, language=language, errors=errors))
        choice = _ask(t(language, "form_action"), "s").lower()
        errors = []
        if choice in {"q", "esc"}:
            print(t(language, "cancelled"))
            return None
        if choice == "p":
            form_errors = _validate_form(fields, values, language)
            if form_errors:
                errors = form_errors
                continue
            _print_preview(build_argv(values), preview_lines)
            continue
        if choice == "s":
            form_errors = _validate_form(fields, values, language)
            if form_errors:
                errors = form_errors
                continue
            if provider_default_model_selector and not _provider_default_health_matches(values):
                try:
                    result = _check_provider_default_model_health(values)
                except ValueError:
                    errors = [t(language, "provider_model_api_required")]
                    continue
                if not result.ok:
                    errors = [t(language, "provider_model_health_failed").format(detail=result.detail)]
                    continue
                print(t(language, "provider_model_health_passed").format(detail=result.detail))
            if confirm and not _confirm(language):
                print(t(language, "cancelled"))
                return None
            if execute:
                return execute(values)
            _run_output_command(language, title, build_argv(values))
            return None
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(fields):
                field = fields[index - 1]
                values[field.key] = _edit_field(field, values.get(field.key, ""))
                _clear_bulk_model_ids_for_field(values, field)
                if on_change:
                    on_change(values, field)
                continue
        print(t(language, "form_invalid_action"))


def _action_title(language: str, zh: str, en: str) -> str:
    return zh if language == "zh" else en


def _codex_config_model() -> str:
    try:
        return extract_toml_string(DEFAULT_CODEX_CONFIG.read_text(encoding="utf-8"), "model").strip()
    except (FileNotFoundError, OSError, ValueError):
        return ""


def _menu_status() -> Tuple[str, str]:
    codex_model = _codex_config_model()
    try:
        for provider in ProviderStore().list_providers():
            if not provider.current:
                continue
            provider_id = provider.provider_id or "-"
            if codex_model:
                model_id = codex_model
            elif provider.default_model:
                model_id = provider.default_model
            elif provider.read_only:
                model_id = "dynamic"
            else:
                model_id = "-"
            return provider_id, model_id
    except (FileNotFoundError, OSError, ValueError):
        pass
    if codex_model:
        return "unknown", codex_model
    return "unknown", "unknown"


def _current_provider_id(default: str = "example-provider") -> str:
    try:
        for provider in ProviderStore().list_providers():
            if provider.current and provider.provider_id:
                return provider.provider_id
    except (FileNotFoundError, OSError, ValueError):
        pass
    return default


def _switchable_provider_choices() -> Tuple[str, ...]:
    try:
        return tuple(
            provider.provider_id
            for provider in ProviderStore().list_providers()
            if provider.provider_id and not provider.read_only
        )
    except (FileNotFoundError, OSError, ValueError):
        return ()


def _provider_update_form_values(provider_id: str) -> Dict[str, str]:
    settings = ProviderManager().load_settings(provider_id)
    provider_name = provider_id
    try:
        for provider in ProviderStore().list_providers():
            if provider.provider_id == provider_id:
                provider_name = provider.name or provider.provider_id
                break
    except (FileNotFoundError, OSError, ValueError):
        pass

    config_text = str(settings.get("config") or "")
    auth = settings.get("auth")
    api_key = str(auth.get("OPENAI_API_KEY") or "") if isinstance(auth, dict) else ""
    default_model = extract_toml_string(config_text, "model").strip()
    context_window = "128000"
    model_catalog = settings.get("modelCatalog")
    models = model_catalog.get("models", []) if isinstance(model_catalog, dict) else []
    if isinstance(models, list):
        for entry in models:
            if not isinstance(entry, dict):
                continue
            model_id = str(entry.get("model") or entry.get("id") or "").strip()
            if model_id == default_model:
                context_window = str(entry.get("contextWindow") or entry.get("context_window") or context_window)
                break
    api_format = extract_toml_string(config_text, "wire_api").strip() or "responses"
    if api_format not in {"responses", "chat"}:
        api_format = "responses"
    return {
        "provider_id": provider_id,
        "new_provider_id": provider_id,
        "provider_name": str(settings.get("name") or provider_name or provider_id),
        "base_url": extract_toml_string(config_text, "base_url").strip(),
        "api_key": api_key,
        "default_model": default_model,
        "context_window": context_window,
        "api_format": api_format,
        "sync_current": "yes",
        "restart_now": "yes",
    }


def run_tui(language: Optional[str] = None) -> int:
    settings_manager = SettingsManager()
    current_language = normalize_language(language) if language else settings_manager.load().language
    selected_action = "1"
    actions: Dict[str, Callable[[], Optional[str]]] = {
        "1": lambda: _doctor(current_language),
        "2": lambda: _install_missing(current_language),
        "3": lambda: _provider_list(current_language),
        "4": lambda: _provider_add(current_language),
        "5": lambda: _provider_update(current_language),
        "6": lambda: _provider_switch(current_language),
        "7": lambda: _provider_delete(current_language),
        "8": lambda: _model_list(current_language),
        "9": lambda: _model_add(current_language),
        "10": lambda: _model_set_default(current_language),
        "11": lambda: _model_delete(current_language),
        "12": lambda: _proxy_status(current_language),
        "13": lambda: _proxy_set(current_language),
        "14": lambda: _proxy_restart(current_language),
        "15": lambda: _proxy_logs(current_language),
        "16": lambda: _proxy_test(current_language),
        "17": lambda: _backup_create(current_language),
        "18": lambda: _backup_list(current_language),
        "19": lambda: _backup_restore(current_language),
        "20": lambda: _backup_delete(current_language),
        "21": lambda: _language_settings(current_language, settings_manager),
    }
    try:
        while True:
            if _supports_focus_form():
                choice, selected_action = _read_menu_choice_focus(current_language, selected_action)
            else:
                provider_status, model_status = _menu_status()
                write_text(
                    render_menu(
                        language=current_language,
                        provider_status=provider_status,
                        model_status=model_status,
                        selected_action=selected_action,
                    )
                )
                choice = _ask(t(current_language, "select_action"), "q")
            if choice.lower() == "q":
                return 0
            action = actions.get(choice)
            if not action:
                print(t(current_language, "invalid_selection"))
                continue
            selected_action = choice
            try:
                new_language = action()
                if new_language:
                    current_language = new_language
            except TuiInterrupted:
                raise
            except Exception as exc:  # pragma: no cover - defensive for interactive use
                print(f"{t(current_language, 'error_prefix')}: {exc}")
    except TuiInterrupted:
        write_text(f"\n{t(current_language, 'exited')}\n")
        return 130


def _doctor(language: str) -> None:
    _run_output_command(language, _action_title(language, "环境检查", "Doctor"), ["doctor"])


def _install_missing(language: str) -> None:
    _run_form(
        language,
        _action_title(language, "安装缺失组件", "Install Components"),
        [FormField("target", t(language, "install_target"), "all", required=True, choices=("all", "codex", "cc-switch"))],
        lambda values: ["install", values["target"], "--yes"],
        ["安装选中的缺失组件。"] if language == "zh" else ["Install the selected missing component(s)."],
    )


def _provider_list(language: str) -> None:
    _run_output_command(language, _action_title(language, "供应商列表", "List Providers"), ["provider", "list"])


def _provider_add(language: str) -> None:
    def build(values: Dict[str, str]) -> List[str]:
        provider_id = values["provider_id"]
        argv = [
            "provider",
            "add",
            provider_id,
            "--name",
            values["provider_name"] or provider_id,
            "--base-url",
            values["base_url"],
            "--api-key",
            values["api_key"],
            "--default-model",
            values["default_model"],
            "--context-window",
            values["context_window"],
            "--yes",
        ]
        if values["switch_after_add"] == "yes":
            argv.append("--switch")
        return argv

    _run_form(
        language,
        _action_title(language, "新增供应商", "Add Provider"),
        [
            FormField("provider_id", t(language, "provider_id"), required=True),
            FormField("provider_name", t(language, "provider_name")),
            FormField("base_url", t(language, "base_url"), required=True),
            FormField("api_key", t(language, "api_key"), required=True, secret=True),
            FormField("default_model", t(language, "default_model"), required=True),
            FormField("context_window", t(language, "context_window"), "128000", required=True),
            FormField("switch_after_add", t(language, "switch_after_add"), "no", choices=("yes", "no")),
        ],
        build,
        (
            ["写入 cc-switch provider 和初始模型。", t(language, "provider_default_model_select_hint")]
            if language == "zh"
            else ["Create provider and initial model.", t(language, "provider_default_model_select_hint")]
        ),
        provider_default_model_selector=True,
    )


def _provider_update(language: str) -> None:
    provider_choices = _switchable_provider_choices()
    current_provider = _current_provider_id()
    provider_default = current_provider if not provider_choices or current_provider in provider_choices else provider_choices[0]
    defaults = _provider_update_form_values(provider_default)

    def build(values: Dict[str, str]) -> List[str]:
        argv = [
            "provider",
            "update",
            values["provider_id"],
            "--new-id",
            values["new_provider_id"],
            "--name",
            values["provider_name"] or values["new_provider_id"],
            "--base-url",
            values["base_url"],
            "--api-key",
            values["api_key"],
            "--default-model",
            values["default_model"],
            "--context-window",
            values["context_window"],
            "--api-format",
            values["api_format"],
            "--yes",
        ]
        if values["sync_current"] == "yes":
            argv.append("--sync-current")
            if values["restart_now"] == "yes":
                argv.append("--restart")
        return argv

    def on_change(values: Dict[str, str], field: FormField) -> Sequence[str]:
        if field.key != "provider_id":
            return []
        loaded = _provider_update_form_values(values["provider_id"])
        for key, value in loaded.items():
            if key != "provider_id":
                values[key] = value
        _clear_provider_default_health(values)
        return [t(language, "provider_update_loaded").format(provider=values["provider_id"])]

    _run_form(
        language,
        _action_title(language, "修改供应商", "Update Provider"),
        [
            FormField("provider_id", t(language, "provider_id"), defaults["provider_id"], required=True, choices=provider_choices),
            FormField("new_provider_id", t(language, "new_provider_id"), defaults["new_provider_id"], required=True),
            FormField("provider_name", t(language, "provider_name"), defaults["provider_name"]),
            FormField("base_url", t(language, "base_url"), defaults["base_url"], required=True),
            FormField("api_key", t(language, "api_key"), defaults["api_key"], required=True, secret=True),
            FormField("default_model", t(language, "default_model"), defaults["default_model"], required=True),
            FormField("context_window", t(language, "context_window"), defaults["context_window"], required=True),
            FormField("api_format", t(language, "api_format"), defaults["api_format"], required=True, choices=("responses", "chat")),
            FormField("sync_current", t(language, "sync_current"), defaults["sync_current"], choices=("yes", "no")),
            FormField("restart_now", t(language, "restart_now"), defaults["restart_now"], choices=("yes", "no")),
        ],
        build,
        (
            ["供应商 ID 用于选择要修改的 provider；新供应商 ID 用于改名。", t(language, "provider_default_model_select_hint")]
            if language == "zh"
            else ["Provider ID selects the target; New provider ID renames it.", t(language, "provider_default_model_select_hint")]
        ),
        provider_default_model_selector=True,
        on_change=on_change,
    )


def _provider_switch(language: str) -> None:
    def build(values: Dict[str, str]) -> List[str]:
        argv = ["provider", "switch", values["provider_id"], "--yes"]
        if values["restart_now"] == "no":
            argv.append("--no-restart")
        return argv

    provider_choices = _switchable_provider_choices()
    current_provider = _current_provider_id()
    provider_default = current_provider if not provider_choices or current_provider in provider_choices else provider_choices[0]
    _run_form(
        language,
        _action_title(language, "切换供应商", "Switch Provider"),
        [
            FormField("provider_id", t(language, "provider_id"), provider_default, required=True, choices=provider_choices),
            FormField("restart_now", t(language, "restart_now"), "yes", choices=("yes", "no")),
        ],
        build,
        ["同步 catalog、config.toml，并按选择重启代理。"] if language == "zh" else ["Sync catalog/config.toml and optionally restart proxy."],
    )


def _provider_delete(language: str) -> None:
    def build(values: Dict[str, str]) -> List[str]:
        argv = ["provider", "delete", values["provider_id"], "--yes"]
        if values["force_delete"] == "yes":
            argv.append("--force")
        return argv

    _run_form(
        language,
        _action_title(language, "删除供应商", "Delete Provider"),
        [
            FormField("provider_id", t(language, "provider_id"), required=True),
            FormField("force_delete", t(language, "force_delete"), "no", choices=("yes", "no")),
        ],
        build,
        ["删除可管理 provider；官方系统项不可删除。"] if language == "zh" else ["Delete a managed provider; official system provider is protected."],
    )


def _show_model_detail(language: str, model: ModelInfo, health_results: Dict[str, ModelHealthResult]) -> None:
    while True:
        _clear_screen()
        _write_raw_text(render_model_detail(language, model, health_results=health_results))
        key = _read_key()
        if key in {"\x03", "\x04"}:
            raise TuiInterrupted
        if key in {"\x1b", "", "\r", "\n", "q", "Q"}:
            return


def _confirm_delete_failed_model(language: str, model: ModelInfo, health_result: ModelHealthResult) -> bool:
    while True:
        _clear_screen()
        _write_raw_text(render_model_delete_confirm(language, model, health_result))
        key = _read_key()
        if key in {"\x03", "\x04"}:
            raise TuiInterrupted
        if key in {"y", "Y"}:
            return True
        if key in {"\x1b", "", "q", "Q"}:
            return False


def _run_model_delete_quietly(provider_id: str, model_id: str) -> Tuple[int, str]:
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["model", "delete", model_id, "--provider-id", provider_id, "--yes"])
    except (OSError, ValueError) as exc:
        return 2, str(exc)
    lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
    return code, (lines[-1] if lines else "")


def _run_model_sync_current_quietly() -> Tuple[int, str]:
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(["model", "sync-current", "--yes"])
    except (OSError, ValueError) as exc:
        return 2, str(exc)
    lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
    return code, (lines[0] if lines else "")


def _redraw_model_list(
    language: str,
    models: Sequence[ModelInfo],
    selected_index: int,
    health_results: Dict[str, ModelHealthResult],
    checking_model: str,
    previous_line_count: int,
    message_lines: Sequence[str] = (),
) -> int:
    text = render_model_list_interactive(
        language,
        models,
        selected_index,
        health_results=health_results,
        checking_model=checking_model,
        message_lines=message_lines,
    )
    return _write_focus_form_update(text, previous_line_count, (1, 1))


def _run_model_list_interactive(language: str) -> None:
    try:
        models = ModelStore().list_models()
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        _run_output_command(language, _action_title(language, "模型列表", "List Models"), ["model", "list"])
        return
    selected_index = 0
    health_results: Dict[str, ModelHealthResult] = {}
    checking_model = ""
    message_lines: List[str] = _model_catalog_sync_messages(language)
    previous_line_count = 0
    first_draw = True
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            if models:
                selected_index = min(max(selected_index, 0), len(models) - 1)
            else:
                selected_index = 0
            text = render_model_list_interactive(
                language,
                models,
                selected_index,
                health_results=health_results,
                checking_model=checking_model,
                message_lines=message_lines,
            )
            if first_draw:
                _clear_screen()
                previous_line_count = 0
            previous_line_count = _write_focus_form_update(text, previous_line_count, (1, 1))
            first_draw = False
            key = _read_key()
            if key in {"\x03", "\x04"}:
                raise TuiInterrupted
            if key in {"\x1b", "", "q", "Q"}:
                _clear_screen()
                return
            if key in {"\r", "\n"} and models:
                _show_model_detail(language, models[selected_index], health_results)
                first_draw = True
                previous_line_count = 0
                continue
            if key in {"s", "S"}:
                code, reason = _run_model_sync_current_quietly()
                if code == 0:
                    try:
                        models = ModelStore().list_models()
                    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                        models = []
                    selected_index = min(selected_index, max(0, len(models) - 1))
                    health_results = {}
                    checking_model = ""
                    message_lines = [
                        t(language, "model_catalog_sync_done").format(
                            provider=_current_provider_id(),
                            count=len(models),
                        )
                    ]
                else:
                    message_lines = [t(language, "model_catalog_sync_failed").format(reason=reason or str(code))]
                first_draw = True
                previous_line_count = 0
                continue
            if key in {"d", "D"} and models:
                model = models[selected_index]
                result = health_results.get(model.slug)
                if not _model_delete_enabled(model, health_results):
                    message_lines = [t(language, "model_delete_failed_only")]
                    continue
                if _model_delete_is_current_default(model):
                    message_lines = [t(language, "model_delete_current_default_blocked")]
                    continue
                if not _confirm_delete_failed_model(language, model, result):
                    message_lines = [t(language, "model_delete_cancelled")]
                    first_draw = True
                    previous_line_count = 0
                    continue
                code, reason = _run_model_delete_quietly(_current_provider_id(), model.slug)
                if code == 0:
                    health_results.pop(model.slug, None)
                    try:
                        models = ModelStore().list_models()
                    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                        models = []
                    selected_index = min(selected_index, max(0, len(models) - 1))
                    message_lines = [t(language, "model_delete_done").format(model=model.slug)]
                else:
                    message_lines = [t(language, "model_delete_failed").format(model=model.slug, reason=reason or str(code))]
                first_draw = True
                previous_line_count = 0
                continue
            if key == " " and models:
                message_lines = []
                checking_model = models[selected_index].slug
                previous_line_count = _redraw_model_list(
                    language,
                    models,
                    selected_index,
                    health_results,
                    checking_model,
                    previous_line_count,
                    message_lines,
                )
                health_results[checking_model] = _check_model_health_safe(_current_provider_id(), checking_model)
                checking_model = ""
                continue
            if key == "\x01" and models:
                message_lines = []
                provider_id = _current_provider_id()
                for index, model in enumerate(models):
                    selected_index = index
                    checking_model = model.slug
                    previous_line_count = _redraw_model_list(
                        language,
                        models,
                        selected_index,
                        health_results,
                        checking_model,
                        previous_line_count,
                        message_lines,
                    )
                    health_results[model.slug] = _check_model_health_safe(provider_id, model.slug)
                checking_model = ""
                continue
            if _is_next_focus_key(key) and models:
                selected_index = (selected_index + 1) % len(models)
                continue
            if _is_previous_focus_key(key) and models:
                selected_index = (selected_index - 1) % len(models)
                continue
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _model_list(language: str) -> None:
    if _supports_focus_form():
        _run_model_list_interactive(language)
        return
    _run_output_command(language, _action_title(language, "模型列表", "List Models"), ["model", "list"])


def _model_add(language: str) -> None:
    title = _action_title(language, "新增模型", "Add Model")

    def build_one(values: Dict[str, str], model: str, allow_default: bool = True, allow_profile: bool = True) -> List[str]:
        return _build_model_add_argv(values, model, allow_default=allow_default, allow_profile=allow_profile)

    def build(values: Dict[str, str]) -> List[str]:
        bulk_models = _bulk_model_ids(values)
        if len(bulk_models) > 1:
            return [
                "model",
                "add",
                f"{len(bulk_models)} models",
                "--provider-id",
                values["provider_id"],
                "--context-window",
                values["context_window"],
                "--yes",
            ]
        return build_one(values, values["model_id"])

    def execute(values: Dict[str, str]) -> Optional[str]:
        bulk_models = _bulk_model_ids(values)
        if len(bulk_models) > 1:
            existing_models = _existing_model_ids(values["provider_id"])
            commands = [
                (
                    model_id,
                    None if model_id in existing_models else build_one(values, model_id, allow_default=False, allow_profile=False),
                )
                for model_id in bulk_models
            ]
            _run_bulk_output_commands(language, t(language, "model_bulk_output"), commands)
            return None
        _run_output_command(language, title, build_one(values, values["model_id"]))
        return None

    _run_form(
        language,
        title,
        [
            FormField("model_id", t(language, "model_id"), required=True),
            FormField("display_name", t(language, "display_name")),
            FormField("context_window", t(language, "context_window"), "128000", required=True),
            FormField("provider_id", t(language, "provider_id"), _current_provider_id(), required=True),
            FormField("profile_name", t(language, "profile_name")),
            FormField("make_default", t(language, "make_default"), "no", choices=("yes", "no")),
        ],
        build,
        (
            ["追加模型到 provider 与 Codex catalog。", t(language, "model_select_hint")]
            if language == "zh"
            else ["Add model to provider and Codex catalog.", t(language, "model_select_hint")]
        ),
        execute=execute,
        model_selector=True,
    )


def _model_set_default(language: str) -> None:
    _run_form(
        language,
        _action_title(language, "设置默认模型", "Set Default Model"),
        [FormField("model_id", t(language, "model_id"), required=True)],
        lambda values: ["model", "set-default", values["model_id"], "--yes"],
        (
            ["更新 Codex config.toml 的 model。", t(language, "default_model_select_hint")]
            if language == "zh"
            else ["Update model in Codex config.toml.", t(language, "default_model_select_hint")]
        ),
        catalog_model_selector=True,
    )


def _model_delete(language: str) -> None:
    title = _action_title(language, "删除模型", "Delete Model")

    def build_one(values: Dict[str, str], model_id: str) -> List[str]:
        argv = ["model", "delete", model_id, "--provider-id", values["provider_id"], "--yes"]
        if values["force_delete"] == "yes":
            argv.append("--force")
        return argv

    def build(values: Dict[str, str]) -> List[str]:
        bulk_models = _bulk_model_ids(values)
        if len(bulk_models) > 1:
            argv = ["model", "delete", f"{len(bulk_models)} models", "--provider-id", values["provider_id"], "--yes"]
            if values["force_delete"] == "yes":
                argv.append("--force")
            return argv
        return build_one(values, values["model_id"])

    def execute(values: Dict[str, str]) -> Optional[str]:
        bulk_models = _bulk_model_ids(values)
        if len(bulk_models) > 1:
            commands = [(model_id, build_one(values, model_id)) for model_id in bulk_models]
            _run_model_delete_output_commands(language, t(language, "model_delete_bulk_output"), commands)
            return None
        _run_output_command(language, title, build_one(values, values["model_id"]))
        return None

    _run_form(
        language,
        title,
        [
            FormField("model_id", t(language, "model_id"), required=True),
            FormField("provider_id", t(language, "provider_id"), _current_provider_id(), required=True),
            FormField("force_delete", t(language, "force_delete"), "no", choices=("yes", "no")),
        ],
        build,
        (
            ["从 provider/catalog 移除模型。", t(language, "model_delete_select_hint")]
            if language == "zh"
            else ["Remove model from provider/catalog.", t(language, "model_delete_select_hint")]
        ),
        execute=execute,
        model_delete_selector=True,
    )


def _proxy_status(language: str) -> None:
    _run_output_command(language, _action_title(language, "代理状态", "Proxy Status"), ["proxy", "status"])


def _proxy_set(language: str) -> None:
    def build(values: Dict[str, str]) -> List[str]:
        argv = ["proxy", "set", "--listen-address", values["listen_address"], "--listen-port", values["listen_port"], "--yes"]
        if values["restart_now"] == "yes":
            argv.append("--restart")
        return argv

    _run_form(
        language,
        _action_title(language, "设置代理", "Set Proxy"),
        [
            FormField("listen_address", t(language, "listen_address"), "127.0.0.1", required=True),
            FormField("listen_port", t(language, "listen_port"), "15721", required=True),
            FormField("restart_now", t(language, "restart_now"), "no", choices=("yes", "no")),
        ],
        build,
        ["更新代理监听配置。"] if language == "zh" else ["Update proxy listen configuration."],
    )


def _proxy_restart(language: str) -> None:
    _run_form(
        language,
        _action_title(language, "重启代理", "Restart Proxy"),
        [],
        lambda values: ["proxy", "restart", "--yes"],
        ["执行 systemctl restart cc-switch-codex-proxy。"] if language == "zh" else ["Run systemctl restart cc-switch-codex-proxy."],
    )


def _proxy_logs(language: str) -> None:
    _run_form(
        language,
        _action_title(language, "查看代理日志", "Proxy Logs"),
        [FormField("log_lines", t(language, "log_lines"), "100", required=True)],
        lambda values: ["proxy", "logs", "-n", values["log_lines"]],
        ["读取 journalctl 最近日志。"] if language == "zh" else ["Read recent journalctl logs."],
        confirm=False,
    )


def _proxy_test(language: str) -> None:
    _run_form(
        language,
        _action_title(language, "测试代理", "Test Proxy"),
        [
            FormField("model_id", t(language, "model_id"), required=True),
            FormField("base_url", t(language, "base_url"), "http://127.0.0.1:15721/v1", required=True),
        ],
        lambda values: ["proxy", "test", "--model", values["model_id"], "--base-url", values["base_url"]],
        ["通过 /responses 发送 ping。"] if language == "zh" else ["Send ping through /responses."],
        confirm=False,
    )


def _run_backup_delete_selector(language: str) -> Optional[List[str]]:
    try:
        backups = BackupManager().list_backups()
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        backups = []
    selected_index = 0
    selected_ids: List[str] = []
    previous_line_count = 0
    first_draw = True
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            if backups:
                selected_index = min(max(selected_index, 0), len(backups) - 1)
            else:
                selected_index = 0
            text = render_backup_delete_selector(language, backups, selected_index, selected_ids)
            if first_draw:
                _clear_screen()
                previous_line_count = 0
            previous_line_count = _write_focus_form_update(text, previous_line_count, (1, 1))
            first_draw = False
            key = _read_key()
            if key in {"\x03", "\x04"}:
                raise TuiInterrupted
            if key in {"\x1b", "", "q", "Q"}:
                _clear_screen()
                return None
            if key in {"\r", "\n"} and backups:
                return selected_ids or [backups[selected_index].backup_id]
            if key == " " and backups:
                backup_id = backups[selected_index].backup_id
                if backup_id in selected_ids:
                    selected_ids = [item for item in selected_ids if item != backup_id]
                else:
                    selected_ids.append(backup_id)
                continue
            if key == "\x01" and backups:
                all_ids = [backup.backup_id for backup in backups]
                selected_ids = [] if len(selected_ids) == len(all_ids) else all_ids
                continue
            if _is_next_focus_key(key) and backups:
                selected_index = (selected_index + 1) % len(backups)
                continue
            if _is_previous_focus_key(key) and backups:
                selected_index = (selected_index - 1) % len(backups)
                continue
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _confirm_delete_backups(language: str, backup_ids: Sequence[str]) -> bool:
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            _clear_screen()
            _write_raw_text(render_backup_delete_confirm(language, backup_ids))
            key = _read_key()
            if key in {"\x03", "\x04"}:
                raise TuiInterrupted
            if key in {"y", "Y"}:
                return True
            if key in {"\x1b", "", "q", "Q"}:
                return False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _backup_create(language: str) -> None:
    _run_form(
        language,
        _action_title(language, "创建备份", "Create Backup"),
        [FormField("backup_reason", t(language, "backup_reason"), "manual", required=True)],
        lambda values: ["backup", "create", "--reason", values["backup_reason"]],
        ["保存 cc-switch、Codex、systemd 配置。"] if language == "zh" else ["Save cc-switch, Codex, and systemd configs."],
        confirm=False,
    )


def _backup_list(language: str) -> None:
    _run_output_command(language, _action_title(language, "备份列表", "List Backups"), ["backup", "list"])


def _backup_restore(language: str) -> None:
    _run_form(
        language,
        _action_title(language, "恢复备份", "Restore Backup"),
        [FormField("backup_id", t(language, "backup_id"), required=True)],
        lambda values: ["backup", "restore", values["backup_id"], "--yes"],
        ["把选中备份写回原路径。"] if language == "zh" else ["Restore selected backup to original paths."],
    )


def _backup_delete(language: str) -> None:
    if not _supports_focus_form():
        _run_form(
            language,
            _action_title(language, "删除备份", "Delete Backups"),
            [FormField("backup_id", t(language, "backup_id"), required=True)],
            lambda values: ["backup", "delete", values["backup_id"], "--yes"],
            ["删除选中的备份。"] if language == "zh" else ["Delete selected backup."],
        )
        return
    backup_ids = _run_backup_delete_selector(language)
    if not backup_ids:
        return
    if not _confirm_delete_backups(language, backup_ids):
        _clear_screen()
        _write_raw_text(t(language, "backup_delete_cancelled") + "\n")
        return
    _run_output_command(language, _action_title(language, "删除备份", "Delete Backups"), ["backup", "delete", *backup_ids, "--yes"])


def _language_settings(language: str, settings_manager: SettingsManager) -> Optional[str]:
    def apply_language(values: Dict[str, str]) -> Optional[str]:
        try:
            new_language = normalize_language(values["language"])
        except ValueError:
            print(t(language, "invalid_language"))
            return None
        settings_manager.set_language(new_language)
        print(t(new_language, "language_switched"))
        return new_language

    return _run_form(
        language,
        _action_title(language, "界面语言", "Language"),
        [FormField("language", t(language, "language_prompt"), language, required=True, choices=("zh", "en"))],
        lambda values: [],
        ["切换 TUI 显示语言。"] if language == "zh" else ["Switch the TUI display language."],
        confirm=False,
        execute=apply_language,
    )
