#!/usr/bin/env bash
set -Eeuo pipefail

PROGRAM="$(basename "$0")"
SERVICE="cc-switch-codex-proxy"
REPO_URL="https://github.com/chismi1017/codex-model-admin.git"
BRANCH="main"
INSTALL_DIR="/opt/codex-model-admin"
SOURCE_HOST=""
ASSUME_YES=0
KEEP_ARCHIVE=0
SOURCE_WATCHDOG_SECONDS=600

WORK_DIR=""
SOURCE_ARCHIVE=""
ROLLBACK_DIR=""
LOCK_DIR="/run/codex-model-admin-migrate.lock"
TARGET_BACKED_UP=0
RESTORE_FINISHED=0
TARGET_SERVICE_WAS_ACTIVE=0
TARGET_SERVICE_STOPPED=0

SSH_OPTIONS=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=3
)

TARGET_PATHS=(
  "/root/.cc-switch"
  "/root/.codex"
  "/root/.config/codex-model-admin"
  "/root/.config/cc-switch"
  "/root/.npmrc"
  "/etc/systemd/system/${SERVICE}.service"
  "/etc/systemd/system/${SERVICE}.service.d"
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
  ${PROGRAM} <源服务器SSH地址> [选项]

示例：
  ${PROGRAM} root@old-server --yes
  ${PROGRAM} old-server-alias --install-dir /opt/codex-model-admin --yes

选项：
  --source <地址>       源服务器 SSH 地址或 ~/.ssh/config 别名
  --repo-url <URL>      Git 仓库地址，默认：${REPO_URL}
  --branch <分支>       Git 分支，默认：${BRANCH}
  --install-dir <目录>  新服务器安装目录，默认：${INSTALL_DIR}
  --keep-archive        成功后保留本地迁移归档
  -y, --yes             不询问，直接执行
  -h, --help            显示帮助

要求：
  - 在新服务器上以 root 执行。
  - 新服务器能够免密 SSH 登录源服务器的 root 账户。
  - 源端活动 Codex/cc-switch 进程会被短暂暂停，并在归档后自动恢复。
  - 新旧服务器均使用 systemd，新服务器 CPU 架构为 x86_64。
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
      --source)
        require_value "$1" "${2:-}"
        SOURCE_HOST="$2"
        shift 2
        ;;
      --repo-url)
        require_value "$1" "${2:-}"
        REPO_URL="$2"
        shift 2
        ;;
      --branch)
        require_value "$1" "${2:-}"
        BRANCH="$2"
        shift 2
        ;;
      --install-dir)
        require_value "$1" "${2:-}"
        INSTALL_DIR="$2"
        shift 2
        ;;
      --keep-archive)
        KEEP_ARCHIVE=1
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
      --*)
        die "未知选项：$1"
        ;;
      *)
        if [[ -n "$SOURCE_HOST" ]]; then
          die "只能指定一个源服务器"
        fi
        SOURCE_HOST="$1"
        shift
        ;;
    esac
  done

  if [[ -z "$SOURCE_HOST" ]]; then
    usage >&2
    exit 2
  fi
}

validate_arguments() {
  [[ "$SOURCE_HOST" != -* ]] || die "源服务器地址不能以 - 开头"
  [[ "$SOURCE_HOST" != *[[:space:]]* ]] || die "源服务器地址不能包含空白字符"
  [[ "$REPO_URL" != -* && "$REPO_URL" != *[[:space:]]* ]] || die "Git 仓库地址无效"
  [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || die "Git 分支名称无效：${BRANCH}"
  [[ "$INSTALL_DIR" == /* && "$INSTALL_DIR" != "/" ]] || die "安装目录必须是非根目录的绝对路径"
}

require_root_and_platform() {
  [[ "$(id -u)" -eq 0 ]] || die "请以 root 用户执行"
  [[ "$(uname -m)" == "x86_64" ]] || die "当前安装器只支持 x86_64 Linux"
  command -v systemctl >/dev/null 2>&1 || die "需要 systemd/systemctl"
  [[ -d /run/systemd/system ]] || die "systemd 当前没有作为 init system 运行"
}

prepare_ssh_environment() {
  if [[ -n "${SSH_AUTH_SOCK:-}" && ! -S "$SSH_AUTH_SOCK" ]]; then
    warn "SSH_AUTH_SOCK 指向无效 socket，已忽略并继续使用 SSH key/config"
    unset SSH_AUTH_SOCK
  fi
}

confirm_action() {
  if ((ASSUME_YES)); then
    return
  fi

  printf '\n将从 %s 复制完整 Codex/cc-switch 持久化环境。\n' "$SOURCE_HOST"
  printf '目标服务器上的现有配置会先保存到回滚目录，然后被源配置替换。\n'
  read -r -p '输入 yes 继续: ' answer
  [[ "$answer" == "yes" ]] || {
    log "已取消"
    exit 0
  }
}

install_prerequisites() {
  local missing=()
  local command_name
  for command_name in git ssh tar gzip curl python3 node npm pgrep; do
    command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
  done

  if ((${#missing[@]} == 0)); then
    return
  fi

  log "安装基础依赖：${missing[*]}"
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y git openssh-clients tar gzip curl python3 nodejs npm procps-ng
  elif command -v yum >/dev/null 2>&1; then
    yum install -y git openssh-clients tar gzip curl python3 nodejs npm procps-ng
  elif command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y git openssh-client tar gzip curl python3 nodejs npm procps
  else
    die "缺少基础依赖且未检测到 dnf/yum/apt-get：${missing[*]}"
  fi

  for command_name in git ssh tar gzip curl python3 node npm pgrep; do
    command -v "$command_name" >/dev/null 2>&1 || die "依赖安装后仍找不到命令：${command_name}"
  done
}

ssh_source() {
  ssh "${SSH_OPTIONS[@]}" "$SOURCE_HOST" "$@"
}

preflight_source() {
  log "检查源服务器 SSH 和运行环境"
  ssh_source bash -s <<'REMOTE'
set -Eeuo pipefail

[[ "$(id -u)" -eq 0 ]] || {
  echo "源服务器 SSH 用户必须是 root" >&2
  exit 1
}
for command_name in tar systemctl pgrep ps kill nohup awk grep tr; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "源服务器缺少 ${command_name}" >&2
    exit 1
  }
done
tar --version 2>/dev/null | grep -q 'GNU tar' || {
  echo "源服务器需要 GNU tar" >&2
  exit 1
}
[[ -d /root/.cc-switch ]] || {
  echo "源服务器不存在 /root/.cc-switch" >&2
  exit 1
}
[[ -d /root/.codex ]] || {
  echo "源服务器不存在 /root/.codex" >&2
  exit 1
}
[[ -f /root/.cc-switch/cc-switch.db ]] || {
  echo "源服务器不存在 cc-switch.db" >&2
  exit 1
}
[[ -f /root/.codex/config.toml ]] || {
  echo "源服务器不存在 Codex config.toml" >&2
  exit 1
}
REMOTE
}

read_source_service_state() {
  local state
  state="$(ssh_source bash -s -- "$SERVICE" <<'REMOTE'
set -Eeuo pipefail
service="$1"
enabled="$(systemctl is-enabled "${service}.service" 2>/dev/null || true)"
active="$(systemctl is-active "${service}.service" 2>/dev/null || true)"
printf '%s|%s\n' "${enabled:-not-found}" "${active:-inactive}"
REMOTE
)"

  IFS='|' read -r SOURCE_SERVICE_ENABLED SOURCE_SERVICE_ACTIVE <<<"$state"
  [[ -n "$SOURCE_SERVICE_ENABLED" && -n "$SOURCE_SERVICE_ACTIVE" ]] || die "无法读取源服务器代理状态"
  log "源服务器代理状态：enabled=${SOURCE_SERVICE_ENABLED}, active=${SOURCE_SERVICE_ACTIVE}"
}

clone_or_update_project() {
  log "安装项目代码到 ${INSTALL_DIR}"
  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    [[ -z "$(git -C "$INSTALL_DIR" status --porcelain)" ]] || die "安装目录存在未提交修改：${INSTALL_DIR}"
    git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
    git -C "$INSTALL_DIR" fetch --depth 1 --prune origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout -B "$BRANCH" FETCH_HEAD
  else
    if [[ -e "$INSTALL_DIR" && -n "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
      die "安装目录已存在且不是 Git 仓库：${INSTALL_DIR}"
    fi
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
}

install_components() {
  log "安装 codex-model-admin、Codex CLI 和 cc-switch"
  "${INSTALL_DIR}/scripts/install-codex-model-admin.sh" "$INSTALL_DIR"
  codex-model-admin install all --yes

  command -v codex >/dev/null 2>&1 || die "Codex CLI 安装失败"
  command -v cc-switch >/dev/null 2>&1 || die "cc-switch 安装失败"
  codex --version
  cc-switch --version
}

create_source_archive() {
  WORK_DIR="$(mktemp -d /root/codex-model-migration.XXXXXX)"
  chmod 0700 "$WORK_DIR"
  SOURCE_ARCHIVE="${WORK_DIR}/source-environment.tar.gz"

  log "自动暂停源服务器相关进程并创建一致性迁移归档"
  if ! ssh_source bash -s -- "$SERVICE" "$SOURCE_WATCHDOG_SECONDS" >"$SOURCE_ARCHIVE" <<'REMOTE'
set -Eeuo pipefail

service="$1"
watchdog_seconds="$2"
was_active=0
paused_file=""
watchdog_status_file=""
watchdog_pid=""

resume_paused_processes() {
  local pid
  local failed=0
  local timed_out=0

  if [[ -n "$watchdog_status_file" && -f "$watchdog_status_file" ]]; then
    echo "警告：源服务器暂停 watchdog 已超时，本次归档作废" >&2
    timed_out=1
  fi

  if [[ -n "$paused_file" && -f "$paused_file" ]]; then
    while read -r pid; do
      [[ "$pid" =~ ^[0-9]+$ ]] || continue
      if kill -0 "$pid" 2>/dev/null && ! kill -CONT "$pid" 2>/dev/null; then
        echo "警告：无法恢复源服务器进程 PID ${pid}" >&2
        failed=1
      fi
    done <"$paused_file"
  fi

  if ((failed == 0)); then
    [[ -n "$paused_file" ]] && rm -f -- "$paused_file"
    [[ -n "$watchdog_status_file" ]] && rm -f -- "$watchdog_status_file"
    if [[ -n "$watchdog_pid" ]]; then
      kill "$watchdog_pid" >/dev/null 2>&1 || true
      wait "$watchdog_pid" 2>/dev/null || true
    fi
  fi
  ((failed == 0 && timed_out == 0))
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if ! resume_paused_processes; then
    status=1
  fi
  if ((was_active)); then
    systemctl start "${service}.service" >/dev/null 2>&1 || {
      echo "警告：源服务器代理未能自动恢复，请手动检查" >&2
      status=1
    }
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if systemctl is-active --quiet "${service}.service"; then
  was_active=1
  systemctl stop "${service}.service" >/dev/null
fi

paused_file="$(mktemp /tmp/codex-model-migration-paused.XXXXXX)"
watchdog_status_file="${paused_file}.status"
chmod 0600 "$paused_file"

nohup bash -c '
parent_pid="$1"
pid_file="$2"
timeout="$3"
status_file="$4"
elapsed=0

while kill -0 "$parent_pid" 2>/dev/null && ((elapsed < timeout)); do
  sleep 2
  elapsed=$((elapsed + 2))
done

if kill -0 "$parent_pid" 2>/dev/null; then
  printf 'timeout\n' >"$status_file"
fi

if [[ -f "$pid_file" ]]; then
  while read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    kill -CONT "$pid" 2>/dev/null || true
  done <"$pid_file"
  rm -f -- "$pid_file"
fi
' _ "$$" "$paused_file" "$watchdog_seconds" "$watchdog_status_file" </dev/null >/dev/null 2>&1 &
watchdog_pid=$!

matching_pids() {
  ps -eo pid=,comm=,args= | awk '
    $2 == "codex" || $2 ~ /^cc-switch/ { print $1; next }
    $2 == "node" && $0 ~ /node [^ ]*\/codex app-server/ { print $1; next }
    ($2 == "python" || $2 == "python3") && $0 ~ /(codex_model_admin|codex-model-admin| -m cli( |$))/ { print $1 }
  '
}

paused_count=0
for _round in 1 2 3 4 5; do
  discovered=0
  while read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    grep -Fxq "$pid" "$paused_file" && continue

    printf '%s\n' "$pid" >>"$paused_file"
    if kill -STOP "$pid" 2>/dev/null; then
      paused_count=$((paused_count + 1))
      discovered=1
    elif kill -0 "$pid" 2>/dev/null; then
      echo "无法暂停源服务器进程 PID ${pid}" >&2
      exit 1
    fi
  done < <(matching_pids)

  ((discovered == 0)) && break
  sleep 0.2
done

while read -r pid; do
  [[ "$pid" =~ ^[0-9]+$ ]] || continue
  grep -Fxq "$pid" "$paused_file" || {
    echo "源服务器仍出现新的相关进程 PID ${pid}" >&2
    exit 1
  }
  if kill -0 "$pid" 2>/dev/null; then
    state="$(ps -o stat= -p "$pid" | tr -d '[:space:]')"
    [[ "$state" == *T* ]] || {
      echo "源服务器进程 PID ${pid} 未进入暂停状态" >&2
      exit 1
    }
  fi
done < <(matching_pids)

if ((paused_count > 0)); then
  echo "已临时暂停 ${paused_count} 个源服务器进程，归档结束后自动恢复" >&2
fi

cd /
paths=(root/.cc-switch root/.codex)
for optional in \
  root/.config/codex-model-admin \
  root/.config/cc-switch \
  root/.npmrc \
  "etc/systemd/system/${service}.service" \
  "etc/systemd/system/${service}.service.d"; do
  [[ -e "$optional" || -L "$optional" ]] && paths+=("$optional")
done

tar \
  --acls \
  --xattrs \
  --numeric-owner \
  --exclude='root/.codex/model-admin-backups' \
  --exclude='root/.codex/log' \
  --exclude='root/.codex/app-server-control' \
  --exclude='root/.codex/.tmp' \
  --exclude='root/.cc-switch/*.log' \
  --exclude='root/.cc-switch/*.lock' \
  -czf - "${paths[@]}"
REMOTE
  then
    die "源服务器归档创建失败；源端代理已尝试恢复"
  fi

  chmod 0600 "$SOURCE_ARCHIVE"
}

validate_source_archive() {
  local listing="${WORK_DIR}/archive-files.txt"
  tar -tzf "$SOURCE_ARCHIVE" >"$listing" || die "迁移归档损坏"

  if grep -Eq '(^/|(^|/)\.\.(/|$))' "$listing"; then
    die "迁移归档包含不安全路径"
  fi
  grep -Fxq 'root/.cc-switch/cc-switch.db' "$listing" || die "迁移归档缺少 cc-switch.db"
  grep -Fxq 'root/.codex/config.toml' "$listing" || die "迁移归档缺少 Codex config.toml"

  log "迁移归档校验完成：$(du -h "$SOURCE_ARCHIVE" | awk '{print $1}')"
}

move_to_rollback() {
  local path="$1"
  local backup_path="${ROLLBACK_DIR}${path}"
  if [[ -e "$path" || -L "$path" ]]; then
    mkdir -p "$(dirname "$backup_path")"
    mv -- "$path" "$backup_path"
  fi
}

restore_rollback_paths() {
  local path backup_path
  for path in "${TARGET_PATHS[@]}"; do
    rm -rf -- "$path"
    backup_path="${ROLLBACK_DIR}${path}"
    if [[ -e "$backup_path" || -L "$backup_path" ]]; then
      mkdir -p "$(dirname "$path")"
      mv -- "$backup_path" "$path"
    fi
  done
}

backup_target_environment() {
  ROLLBACK_DIR="/root/codex-model-admin-rollback-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$ROLLBACK_DIR"
  chmod 0700 "$ROLLBACK_DIR"
  TARGET_BACKED_UP=1

  local path
  for path in "${TARGET_PATHS[@]}"; do
    move_to_rollback "$path"
  done
  log "目标服务器原配置已保存：${ROLLBACK_DIR}"
}

matching_target_pids() {
  ps -eo pid=,comm=,args= | awk '
    $2 == "codex" || $2 ~ /^cc-switch/ { print $1; next }
    $2 == "node" && $0 ~ /node [^ ]*\/codex app-server/ { print $1; next }
    ($2 == "python" || $2 == "python3") && $0 ~ /(codex_model_admin|codex-model-admin| -m cli( |$))/ { print $1 }
  '
}

target_pid_is_live() {
  local state
  state="$(ps -o stat= -p "$1" 2>/dev/null | tr -d '[:space:]')"
  [[ -n "$state" && "$state" != Z* ]]
}

terminate_target_processes() {
  local pids=()
  local remaining=()
  local pid
  mapfile -t pids < <(matching_target_pids)
  ((${#pids[@]} > 0)) || return 0

  log "自动停止目标服务器旧 Codex/cc-switch 进程：${pids[*]}"
  kill -TERM "${pids[@]}" 2>/dev/null || true

  for _round in {1..40}; do
    remaining=()
    for pid in "${pids[@]}"; do
      target_pid_is_live "$pid" && remaining+=("$pid")
    done
    ((${#remaining[@]} == 0)) && return
    sleep 0.25
  done

  warn "目标服务器部分进程未正常退出，执行强制停止：${remaining[*]}"
  kill -KILL "${remaining[@]}" 2>/dev/null || true
  sleep 0.2
  for pid in "${remaining[@]}"; do
    target_pid_is_live "$pid" && die "无法停止目标服务器进程 PID ${pid}"
  done
}

stop_target_environment() {
  if systemctl is-active --quiet "${SERVICE}.service"; then
    TARGET_SERVICE_WAS_ACTIVE=1
    systemctl stop "${SERVICE}.service"
    TARGET_SERVICE_STOPPED=1
  fi
  if command -v cc-switch >/dev/null 2>&1; then
    cc-switch daemon stop >/dev/null 2>&1 || true
  fi
  terminate_target_processes
}

restore_source_environment() {
  log "恢复源服务器配置"
  tar --acls --xattrs --numeric-owner -xzf "$SOURCE_ARCHIVE" -C /

  [[ -f /root/.cc-switch/cc-switch.db ]] || die "恢复后缺少 cc-switch.db"
  [[ -f /root/.codex/config.toml ]] || die "恢复后缺少 Codex config.toml"

  chmod 0700 /root/.cc-switch /root/.codex
  chmod -R go-rwx /root/.cc-switch /root/.codex
  if [[ -d /root/.config/codex-model-admin ]]; then
    chmod 0700 /root/.config/codex-model-admin
    chmod -R go-rwx /root/.config/codex-model-admin
  fi
  if [[ -f "/etc/systemd/system/${SERVICE}.service" && ! -L "/etc/systemd/system/${SERVICE}.service" ]]; then
    chmod 0644 "/etc/systemd/system/${SERVICE}.service"
  fi
  if command -v restorecon >/dev/null 2>&1; then
    restorecon -RF /root/.cc-switch /root/.codex /root/.config/codex-model-admin 2>/dev/null || true
    restorecon -F "/etc/systemd/system/${SERVICE}.service" 2>/dev/null || true
  fi

  systemctl daemon-reload
}

apply_source_service_state() {
  local unit="${SERVICE}.service"
  if [[ ! -e "/etc/systemd/system/${unit}" && ! -L "/etc/systemd/system/${unit}" ]]; then
    warn "源服务器没有代理 unit，跳过服务状态恢复"
    return
  fi

  case "$SOURCE_SERVICE_ENABLED" in
    enabled|enabled-runtime)
      systemctl enable "$unit" >/dev/null
      ;;
    disabled)
      systemctl disable "$unit" >/dev/null 2>&1 || true
      ;;
    masked)
      systemctl mask "$unit" >/dev/null
      ;;
  esac

  if [[ "$SOURCE_SERVICE_ACTIVE" == "active" ]]; then
    systemctl start "$unit"
  else
    systemctl stop "$unit" >/dev/null 2>&1 || true
  fi
}

verify_environment() {
  log "执行迁移后检查"
  codex-model-admin doctor
  codex-model-admin provider list
  codex-model-admin model list
  codex-model-admin proxy status

  if [[ "$SOURCE_SERVICE_ACTIVE" == "active" ]]; then
    local default_model
    default_model="$(sed -n 's/^[[:space:]]*model[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' /root/.codex/config.toml | head -n 1)"
    if [[ -n "$default_model" ]]; then
      codex-model-admin proxy test --model "$default_model" || warn "代理模型健康检测失败，请检查上游网络或凭据"
    fi
  fi
}

on_exit() {
  local status=$?
  trap - EXIT INT TERM

  if ((status != 0)); then
    if ((TARGET_BACKED_UP && !RESTORE_FINISHED)); then
      warn "迁移失败，正在恢复目标服务器原配置"
      systemctl stop "${SERVICE}.service" >/dev/null 2>&1 || true
      restore_rollback_paths || true
      systemctl daemon-reload >/dev/null 2>&1 || true
      if ((TARGET_SERVICE_WAS_ACTIVE)); then
        systemctl start "${SERVICE}.service" >/dev/null 2>&1 || true
      fi
    elif ((TARGET_SERVICE_STOPPED && TARGET_SERVICE_WAS_ACTIVE)); then
      systemctl start "${SERVICE}.service" >/dev/null 2>&1 || true
    fi
  fi

  if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
    if ((status == 0 && KEEP_ARCHIVE)); then
      log "迁移归档已保留：${SOURCE_ARCHIVE}"
    else
      [[ "$WORK_DIR" == /root/codex-model-migration.* ]] && rm -rf -- "$WORK_DIR"
    fi
  fi
  rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
  exit "$status"
}

main() {
  parse_args "$@"
  validate_arguments
  require_root_and_platform
  confirm_action

  mkdir "$LOCK_DIR" 2>/dev/null || die "已有迁移任务正在运行：${LOCK_DIR}"
  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  install_prerequisites
  prepare_ssh_environment
  preflight_source
  read_source_service_state
  clone_or_update_project
  install_components
  create_source_archive
  validate_source_archive
  stop_target_environment
  backup_target_environment
  restore_source_environment
  apply_source_service_state
  RESTORE_FINISHED=1
  verify_environment

  log "迁移完成"
  log "项目目录：${INSTALL_DIR}"
  log "配置回滚目录：${ROLLBACK_DIR}"
}

main "$@"
