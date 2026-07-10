#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="${1:-$(dirname -- "${SCRIPT_DIR}")}"
TARGET="/usr/local/bin/codex-model-admin"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 1
fi

if [ ! -f "${SOURCE_ROOT}/src/cli.py" ]; then
  echo "error: source root does not contain src/cli.py: ${SOURCE_ROOT}" >&2
  exit 1
fi

SOURCE_ROOT="$(cd -- "${SOURCE_ROOT}" && pwd)"

cat > "${TARGET}" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="${SOURCE_ROOT}/src"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m cli "\$@"
EOF

chmod 0755 "${TARGET}"
echo "installed ${TARGET}"
