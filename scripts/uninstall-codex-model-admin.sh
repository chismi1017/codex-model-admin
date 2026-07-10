#!/usr/bin/env bash
set -Eeuo pipefail

PROGRAM="$(basename "$0")"
DEFAULT_PROJECT_DIR="/opt/codex-model-admin"
PROJECT_DIR="$DEFAULT_PROJECT_DIR"
LOCK_DIR="/run/codex-model-admin-uninstall.lock"
MIGRATION_LOCK_DIR="/run/codex-model-admin-migrate.lock"
DRY_RUN=0
ASSUME_YES=0
PURGE_NPMRC=0
LOCK_CREATED=0
FAILURES=0

SERVICE_UNITS=(
  "cc-switch-codex-proxy.service"
  "cc-switch.service"
  "cc-switch-daemon.service"
)

log() {
  printf '[INFO] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

die() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
用法：
  ${PROGRAM} [选项]

完整卸载 codex-model-admin、Codex CLI、cc-switch、相关服务和持久化数据。

选项：
  --project-dir <目录>  项目目录，默认：${DEFAULT_PROJECT_DIR}
  --purge-npmrc        同时删除 /root/.npmrc
  --dry-run            只显示将执行的操作，不修改系统
  -y, --yes            跳过交互式确认
  -h, --help           显示帮助

示例：
  ${PROGRAM} --dry-run
  ${PROGRAM} --yes
  ${PROGRAM} --yes --purge-npmrc

说明：
  - 必须以 root 在独立 SSH shell 中执行。
  - 会永久删除 API Key、Codex 认证、模型、memory、会话和项目备份。
  - 不会卸载 Node.js、npm、Python、Git、curl、tar、SSH 等共享系统依赖。
  - 不会清理共享 journald 历史日志或 shell history。
EOF
}

require_value() {
  local option="$1"
  local value="${2:-}"
  [[ -n "$value" ]] || die "${option} 缺少参数"
}

parse_args() {
  while (($# > 0)); do
    case "$1" in
      --project-dir)
        require_value "$1" "${2:-}"
        PROJECT_DIR="$2"
        shift 2
        ;;
      --purge-npmrc)
        PURGE_NPMRC=1
        shift
        ;;
      --dry-run)
        DRY_RUN=1
        shift
        ;;
      -y|--yes)
        ASSUME_YES=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "未知参数：$1"
        ;;
    esac
  done
}

validate_environment() {
  [[ "$(id -u)" -eq 0 ]] || die "请以 root 用户执行"
  command -v readlink >/dev/null 2>&1 || die "缺少 readlink 命令"
  command -v pgrep >/dev/null 2>&1 || die "缺少 pgrep 命令"

  [[ "$PROJECT_DIR" == /* && "$PROJECT_DIR" != "/" ]] || die "项目目录必须是非根目录的绝对路径"
  [[ ! -L "$PROJECT_DIR" ]] || die "项目目录不能是符号链接：${PROJECT_DIR}"
  PROJECT_DIR="$(readlink -m -- "$PROJECT_DIR")"
  if command -v mountpoint >/dev/null 2>&1 && mountpoint -q "$PROJECT_DIR"; then
    die "拒绝删除作为挂载点的项目目录：${PROJECT_DIR}"
  fi
  [[ "$(basename "$PROJECT_DIR")" == "codex-model-admin" ]] || {
    die "为避免误删，项目目录名称必须是 codex-model-admin：${PROJECT_DIR}"
  }
  if [[ "$PROJECT_DIR" != "$DEFAULT_PROJECT_DIR" && -e "$PROJECT_DIR" ]]; then
    [[ -f "$PROJECT_DIR/src/cli.py" && -f "$PROJECT_DIR/scripts/install-codex-model-admin.sh" ]] || {
      die "自定义项目目录缺少 codex-model-admin 标识文件：${PROJECT_DIR}"
    }
  fi

  if pgrep -f '(^|/)migrate-from-server\.sh([[:space:]]|$)' >/dev/null 2>&1; then
    die "检测到迁移任务，请等待迁移结束后再卸载"
  fi
  if [[ -e "$MIGRATION_LOCK_DIR" ]]; then
    warn "检测到无活动迁移进程的残留锁，将在卸载时删除：${MIGRATION_LOCK_DIR}"
  fi
}

print_plan() {
  cat <<EOF

将永久删除：
  - systemd 服务：${SERVICE_UNITS[*]}
  - 命令入口：codex-model-admin、codex、cc-switch
  - 配置数据：/root/.codex、/root/.cc-switch
  - 管理器设置：/root/.config/codex-model-admin、/root/.config/cc-switch
  - 项目目录：${PROJECT_DIR}
  - 迁移回滚、临时归档和安装临时文件
EOF
  if ((PURGE_NPMRC)); then
    printf '  - npm 用户配置：/root/.npmrc\n'
  else
    printf '\n将保留 /root/.npmrc；需要删除时使用 --purge-npmrc。\n'
  fi
  printf '共享系统依赖和 journald 历史不会删除。\n\n'
}

confirm_action() {
  ((DRY_RUN)) && return 0
  ((ASSUME_YES)) && return 0

  local hostname_value answer
  hostname_value="$(hostname)"
  printf '此操作不可恢复。请输入 DELETE %s 继续：' "$hostname_value"
  read -r answer || die "未读取到确认内容，已停止"
  [[ "$answer" == "DELETE ${hostname_value}" ]] || {
    log "已取消"
    exit 0
  }
}

acquire_lock() {
  local lock_pid="" lock_cmdline=""
  ((DRY_RUN)) && return 0
  [[ ! -L "$LOCK_DIR" ]] || die "卸载锁不能是符号链接：${LOCK_DIR}"
  [[ ! -e "$LOCK_DIR" || -d "$LOCK_DIR" ]] || die "卸载锁路径不是目录：${LOCK_DIR}"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    lock_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [[ "$lock_pid" =~ ^[0-9]+$ ]] && kill -0 "$lock_pid" 2>/dev/null; then
      lock_cmdline="$(tr '\0' ' ' <"/proc/${lock_pid}/cmdline" 2>/dev/null || true)"
      [[ "$lock_cmdline" == *"uninstall-codex-model-admin.sh"* ]] && {
        die "已有卸载任务正在运行，PID=${lock_pid}"
      }
    fi
    warn "清理失效的卸载锁：${LOCK_DIR}"
    rm -f -- "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || die "无法清理失效的卸载锁：${LOCK_DIR}"
    mkdir "$LOCK_DIR" 2>/dev/null || die "无法创建卸载锁：${LOCK_DIR}"
  fi
  chmod 0700 "$LOCK_DIR"
  printf '%s\n' "$$" >"$LOCK_DIR/pid"
  LOCK_CREATED=1
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if ((LOCK_CREATED)); then
    rm -f -- "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
  fi
  exit "$status"
}

print_command() {
  printf '[DRY-RUN]'
  printf ' %q' "$@"
  printf '\n'
}

perform() {
  if ((DRY_RUN)); then
    print_command "$@"
    return 0
  fi
  if ! "$@"; then
    warn "命令执行失败：$(printf '%q ' "$@")"
    FAILURES=$((FAILURES + 1))
  fi
  return 0
}

perform_optional() {
  if ((DRY_RUN)); then
    print_command "$@"
    return 0
  fi
  "$@" >/dev/null 2>&1 || true
}

path_exists() {
  [[ -e "$1" || -L "$1" ]]
}

assert_removable_path() {
  local path="$1"
  case "$path" in
    "$PROJECT_DIR")
      return 0
      ;;
    /usr/local/bin/codex-model-admin|/usr/local/bin/cc-switch|/usr/local/bin/codex|/usr/bin/codex|/root/.local/bin/codex)
      return 0
      ;;
    /root/.codex|/root/.cc-switch|/root/.config/codex-model-admin|/root/.config/cc-switch|/root/.npmrc)
      return 0
      ;;
    /run/codex-model-admin-migrate.lock|/tmp/cc-switch|/tmp/cc-switch-cli.tar.gz)
      return 0
      ;;
    /root/migrate-from-server.sh)
      return 0
      ;;
    /etc/systemd/system/cc-switch-codex-proxy.service|/etc/systemd/system/cc-switch-codex-proxy.service.d)
      return 0
      ;;
    /etc/systemd/system/cc-switch.service|/etc/systemd/system/cc-switch.service.d)
      return 0
      ;;
    /etc/systemd/system/cc-switch-daemon.service|/etc/systemd/system/cc-switch-daemon.service.d)
      return 0
      ;;
    /etc/systemd/system/multi-user.target.wants/cc-switch-codex-proxy.service)
      return 0
      ;;
    /etc/systemd/system/multi-user.target.wants/cc-switch.service)
      return 0
      ;;
    /etc/systemd/system/multi-user.target.wants/cc-switch-daemon.service)
      return 0
      ;;
    /root/codex-model-admin-rollback-*|/root/codex-model-migration.*|/tmp/codex-model-migration-paused.*)
      return 0
      ;;
    /usr/lib/node_modules/@openai/codex|/usr/local/lib/node_modules/@openai/codex|/root/.local/lib/node_modules/@openai/codex)
      return 0
      ;;
    /root/.nvm/versions/node/*/lib/node_modules/@openai/codex)
      return 0
      ;;
    *)
      die "拒绝删除未列入白名单的路径：${path}"
      ;;
  esac
}

remove_path() {
  local path="$1"
  assert_removable_path "$path"
  if ! path_exists "$path"; then
    log "不存在，跳过：${path}"
    return 0
  fi
  perform rm -rf -- "$path"
}

stop_services() {
  local unit
  if command -v systemctl >/dev/null 2>&1; then
    for unit in "${SERVICE_UNITS[@]}"; do
      if systemctl cat "$unit" >/dev/null 2>&1 || systemctl is-enabled "$unit" >/dev/null 2>&1; then
        perform systemctl disable --now "$unit"
      fi
    done
  fi

  if command -v cc-switch >/dev/null 2>&1; then
    perform_optional cc-switch daemon stop
  fi
}

matching_pids() {
  local process pid state comm cmdline environment
  for process in /proc/[0-9]*; do
    [[ -r "$process/comm" && -r "$process/cmdline" ]] || continue
    pid="${process##*/}"
    [[ "$pid" != "$$" && "$pid" != "$PPID" ]] || continue
    state="$(cut -d ' ' -f 3 <"$process/stat" 2>/dev/null || true)"
    [[ "$state" != "Z" ]] || continue
    comm="$(<"$process/comm")"
    cmdline="$(tr '\0' ' ' <"$process/cmdline" 2>/dev/null || true)"

    case "$comm" in
      codex|cc-switch*)
        printf '%s\n' "$pid"
        ;;
      node)
        if [[ "$cmdline" == *"/codex "* || "$cmdline" == *"@openai/codex"* ]]; then
          printf '%s\n' "$pid"
        fi
        ;;
      python|python3)
        if [[ "$cmdline" == *" -m codex_model_admin.cli"* ]]; then
          printf '%s\n' "$pid"
        elif [[ "$cmdline" == *" -m cli"* && -r "$process/environ" ]]; then
          environment="$(tr '\0' '\n' <"$process/environ" 2>/dev/null || true)"
          [[ "$environment" == *"PYTHONPATH=${PROJECT_DIR}/src"* ]] && printf '%s\n' "$pid"
        fi
        ;;
    esac
  done
}

terminate_processes() {
  local pids=() remaining=() pid
  mapfile -t pids < <(matching_pids | sort -n -u)
  ((${#pids[@]} > 0)) || {
    log "未发现 Codex/cc-switch/codex-model-admin 相关进程"
    return 0
  }

  log "停止相关进程：${pids[*]}"
  if ((DRY_RUN)); then
    print_command kill -CONT "${pids[@]}"
    print_command kill -TERM "${pids[@]}"
    return 0
  fi

  kill -CONT "${pids[@]}" 2>/dev/null || true
  kill -TERM "${pids[@]}" 2>/dev/null || true
  for _round in {1..40}; do
    remaining=()
    for pid in "${pids[@]}"; do
      kill -0 "$pid" 2>/dev/null && remaining+=("$pid")
    done
    ((${#remaining[@]} == 0)) && return 0
    sleep 0.25
  done

  warn "部分进程未正常退出，执行强制停止：${remaining[*]}"
  kill -KILL "${remaining[@]}" 2>/dev/null || true
  sleep 0.2
}

remove_service_files() {
  local unit unit_path
  for unit in "${SERVICE_UNITS[@]}"; do
    unit_path="/etc/systemd/system/${unit}"
    remove_path "$unit_path"
    remove_path "${unit_path}.d"
    remove_path "/etc/systemd/system/multi-user.target.wants/${unit}"
  done

  if command -v systemctl >/dev/null 2>&1; then
    perform systemctl daemon-reload
  fi
}

remove_codex_cli() {
  local npm_root="" npm_package_path="" package_path="" codex_path="" version_output=""
  if command -v npm >/dev/null 2>&1; then
    npm_root="$(npm root -g 2>/dev/null || true)"
    if [[ -n "$npm_root" ]]; then
      npm_package_path="$(readlink -m -- "${npm_root%/}/@openai/codex")"
    fi
    if [[ -n "$npm_package_path" && -d "$npm_package_path" ]]; then
      perform npm uninstall -g @openai/codex
    fi
  fi

  if [[ -n "$npm_package_path" ]] && path_exists "$npm_package_path"; then
    case "$npm_package_path" in
      /usr/lib/node_modules/@openai/codex|/usr/local/lib/node_modules/@openai/codex|/root/.local/lib/node_modules/@openai/codex)
        remove_path "$npm_package_path"
        ;;
      /root/.nvm/versions/node/*/lib/node_modules/@openai/codex)
        remove_path "$npm_package_path"
        ;;
      *)
        warn "npm 卸载后仍存在非标准 Codex 包目录：${npm_package_path}"
        ;;
    esac
  fi

  for package_path in \
    /usr/lib/node_modules/@openai/codex \
    /usr/local/lib/node_modules/@openai/codex \
    /root/.local/lib/node_modules/@openai/codex; do
    [[ "$package_path" == "$npm_package_path" ]] && continue
    remove_path "$package_path"
  done

  codex_path="$(command -v codex 2>/dev/null || true)"
  if [[ -n "$codex_path" ]]; then
    version_output="$("$codex_path" --version 2>/dev/null || true)"
    case "$codex_path" in
      /usr/local/bin/codex|/usr/bin/codex|/root/.local/bin/codex)
        if [[ "$version_output" == codex-cli* || -L "$codex_path" ]]; then
          remove_path "$codex_path"
        else
          warn "保留无法确认归属的 codex 命令：${codex_path}"
        fi
        ;;
      *)
        warn "保留安装在非标准路径的 codex 命令：${codex_path}"
        ;;
    esac
  fi
}

remove_components() {
  remove_codex_cli
  remove_path /usr/local/bin/cc-switch
  remove_path /usr/local/bin/codex-model-admin
}

remove_persistent_data() {
  remove_path /root/.codex
  remove_path /root/.cc-switch
  remove_path /root/.config/codex-model-admin
  remove_path /root/.config/cc-switch
  if ((PURGE_NPMRC)); then
    remove_path /root/.npmrc
  fi
}

remove_migration_artifacts() {
  local path
  shopt -s nullglob
  for path in \
    /root/codex-model-admin-rollback-* \
    /root/codex-model-migration.* \
    /tmp/codex-model-migration-paused.*; do
    remove_path "$path"
  done
  shopt -u nullglob

  remove_path /run/codex-model-admin-migrate.lock
  remove_path /root/migrate-from-server.sh
  remove_path /tmp/cc-switch
  remove_path /tmp/cc-switch-cli.tar.gz
}

verify_removed() {
  local leftovers=() remaining_pids=() package_paths=() rollback_paths=() path unit
  hash -r
  for path in \
    "$PROJECT_DIR" \
    /usr/local/bin/codex-model-admin \
    /usr/local/bin/cc-switch \
    /root/.codex \
    /root/.cc-switch \
    /root/.config/codex-model-admin \
    /root/.config/cc-switch \
    /run/codex-model-admin-migrate.lock \
    /root/migrate-from-server.sh \
    /tmp/cc-switch \
    /tmp/cc-switch-cli.tar.gz; do
    path_exists "$path" && leftovers+=("$path")
  done
  if ((PURGE_NPMRC)) && path_exists /root/.npmrc; then
    leftovers+=("/root/.npmrc")
  fi
  for unit in "${SERVICE_UNITS[@]}"; do
    path_exists "/etc/systemd/system/${unit}" && leftovers+=("/etc/systemd/system/${unit}")
    path_exists "/etc/systemd/system/${unit}.d" && leftovers+=("/etc/systemd/system/${unit}.d")
    path_exists "/etc/systemd/system/multi-user.target.wants/${unit}" && {
      leftovers+=("/etc/systemd/system/multi-user.target.wants/${unit}")
    }
  done
  shopt -s nullglob
  package_paths=(
    /usr/lib/node_modules/@openai/codex
    /usr/local/lib/node_modules/@openai/codex
    /root/.local/lib/node_modules/@openai/codex
    /root/.nvm/versions/node/*/lib/node_modules/@openai/codex
  )
  rollback_paths=(
    /root/codex-model-admin-rollback-*
    /root/codex-model-migration.*
    /tmp/codex-model-migration-paused.*
  )
  shopt -u nullglob
  for path in "${package_paths[@]}" "${rollback_paths[@]}"; do
    path_exists "$path" && leftovers+=("$path")
  done
  command -v codex >/dev/null 2>&1 && leftovers+=("command:codex")
  command -v cc-switch >/dev/null 2>&1 && leftovers+=("command:cc-switch")
  command -v codex-model-admin >/dev/null 2>&1 && leftovers+=("command:codex-model-admin")
  if command -v npm >/dev/null 2>&1 && npm list -g --depth=0 @openai/codex >/dev/null 2>&1; then
    leftovers+=("npm:@openai/codex")
  fi
  mapfile -t remaining_pids < <(matching_pids | sort -n -u)
  ((${#remaining_pids[@]} > 0)) && leftovers+=("processes:${remaining_pids[*]}")

  if ((${#leftovers[@]} > 0)); then
    warn "仍有残留：${leftovers[*]}"
    FAILURES=$((FAILURES + 1))
  fi
}

main() {
  parse_args "$@"
  validate_environment
  print_plan
  confirm_action
  acquire_lock
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  if ((DRY_RUN)); then
    log "dry-run 模式：不会修改系统"
  fi

  stop_services
  terminate_processes
  remove_service_files
  remove_components
  remove_persistent_data
  remove_migration_artifacts
  remove_path "$PROJECT_DIR"

  if ((DRY_RUN)); then
    log "dry-run 完成，未修改任何文件、服务或进程"
    return 0
  fi

  verify_removed
  if ((FAILURES > 0)); then
    die "卸载结束，但检测到 ${FAILURES} 项失败或残留"
  fi
  log "完全卸载完成"
  if ((!PURGE_NPMRC)); then
    log "已按默认策略保留 /root/.npmrc"
  fi
}

main "$@"
