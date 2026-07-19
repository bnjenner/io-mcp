#!/usr/bin/env bash
#
# io-mcp installer — Fedora-focused, works on any systemd Linux.
#
# Creates an isolated Python environment (venv or conda), installs io-mcp
# editable into it, writes a default config + state DB, and optionally installs
# systemd user units for the MCP server and the nightly paper digest.
#
# Usage:
#   deploy/install.sh [options]
#
# Options:
#   --method venv|conda   Environment backend (default: venv)
#   --venv-dir PATH       venv location (default: <repo>/.venv)
#   --env-name NAME       conda env name (default: io-mcp)
#   --python VERSION      Python for conda env (default: 3.11)
#   --dev                 Also install dev extras (pytest, ruff)
#   --system-deps         Install OS packages via dnf (python3, pip, git)
#   --systemd             Install + enable the MCP server systemd user unit
#   --timer               Install + enable the nightly digest systemd timer
#   --no-init             Skip creating the default config / state DB
#   -h, --help            Show this help
#
set -euo pipefail

# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

METHOD="venv"
VENV_DIR="${REPO_DIR}/.venv"
ENV_NAME="io-mcp"
PY_VERSION="3.11"
INSTALL_DEV=0
SYSTEM_DEPS=0
DO_SYSTEMD=0
DO_TIMER=0
DO_INIT=1

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  # Print the leading comment block (after the shebang), stripping "# ".
  awk 'NR>2 && /^#/ {sub(/^# ?/, ""); print; next} NR>2 {exit}' "${BASH_SOURCE[0]}"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --method)      METHOD="${2:?}"; shift 2 ;;
    --venv-dir)    VENV_DIR="${2:?}"; shift 2 ;;
    --env-name)    ENV_NAME="${2:?}"; shift 2 ;;
    --python)      PY_VERSION="${2:?}"; shift 2 ;;
    --dev)         INSTALL_DEV=1; shift ;;
    --system-deps) SYSTEM_DEPS=1; shift ;;
    --systemd)     DO_SYSTEMD=1; shift ;;
    --timer)       DO_TIMER=1; shift ;;
    --no-init)     DO_INIT=0; shift ;;
    -h|--help)     usage ;;
    *)             die "unknown option: $1 (try --help)" ;;
  esac
done

# ---------------------------------------------------------------------------
# Optional: OS packages (Fedora / dnf).
if [[ "${SYSTEM_DEPS}" -eq 1 ]]; then
  if command -v dnf >/dev/null 2>&1; then
    log "Installing OS packages via dnf (may prompt for sudo)…"
    sudo dnf install -y python3 python3-pip git
  else
    warn "dnf not found; skipping --system-deps (install python3/pip manually)."
  fi
fi

# ---------------------------------------------------------------------------
# Create the environment and resolve the pip + io-mcp binary paths.
PIP=""
IO_MCP_BIN=""
PY_BIN=""

case "${METHOD}" in
  venv)
    command -v python3 >/dev/null 2>&1 || die "python3 not found."
    # Require >= 3.11.
    python3 - <<'PY' || die "Python >= 3.11 is required."
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
    log "Creating venv at ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
    PIP="${VENV_DIR}/bin/pip"
    PY_BIN="${VENV_DIR}/bin/python"
    IO_MCP_BIN="${VENV_DIR}/bin/io-mcp"
    ;;
  conda)
    command -v conda >/dev/null 2>&1 || die "conda not found on PATH."
    CONDA_BASE="$(conda info --base)"
    # shellcheck disable=SC1091
    source "${CONDA_BASE}/etc/profile.d/conda.sh"
    if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
      log "Reusing existing conda env: ${ENV_NAME}"
    else
      log "Creating conda env '${ENV_NAME}' (python=${PY_VERSION})"
      conda create -y -n "${ENV_NAME}" "python=${PY_VERSION}"
    fi
    ENV_PREFIX="$(conda run -n "${ENV_NAME}" python -c 'import sys; print(sys.prefix)')"
    PIP="${ENV_PREFIX}/bin/pip"
    PY_BIN="${ENV_PREFIX}/bin/python"
    IO_MCP_BIN="${ENV_PREFIX}/bin/io-mcp"
    ;;
  *)
    die "unknown --method '${METHOD}' (expected venv or conda)."
    ;;
esac

# ---------------------------------------------------------------------------
log "Upgrading pip"
"${PIP}" install --upgrade pip >/dev/null

log "Installing io-mcp (editable) from ${REPO_DIR}"
if [[ "${INSTALL_DEV}" -eq 1 ]]; then
  "${PIP}" install -e "${REPO_DIR}[dev]"
else
  "${PIP}" install -e "${REPO_DIR}"
fi

# Sanity check: the entry point and prompts must both resolve.
"${PY_BIN}" -c "import io_mcp; from io_mcp.config import load_prompt; load_prompt('relevance_score')" \
  || die "post-install check failed (prompts not reachable?)."

# ---------------------------------------------------------------------------
if [[ "${DO_INIT}" -eq 1 ]]; then
  log "Initializing config + state database"
  "${IO_MCP_BIN}" init || warn "init reported an issue (config may already exist)."
fi

# ---------------------------------------------------------------------------
# systemd user units (server + optional digest timer).
render_unit() {
  # render_unit <template> <dest>
  local template="$1" dest="$2"
  sed -e "s|@IO_MCP_BIN@|${IO_MCP_BIN}|g" \
      -e "s|@REPO_DIR@|${REPO_DIR}|g" \
      "${template}" > "${dest}"
}

if [[ "${DO_SYSTEMD}" -eq 1 || "${DO_TIMER}" -eq 1 ]]; then
  UNIT_DIR="${HOME}/.config/systemd/user"
  mkdir -p "${UNIT_DIR}"
fi

if [[ "${DO_SYSTEMD}" -eq 1 ]]; then
  log "Installing systemd user unit: io-mcp.service"
  render_unit "${SCRIPT_DIR}/io-mcp.service" "${UNIT_DIR}/io-mcp.service"
  systemctl --user daemon-reload
  systemctl --user enable --now io-mcp.service
  log "MCP server enabled. Check: systemctl --user status io-mcp.service"
fi

if [[ "${DO_TIMER}" -eq 1 ]]; then
  log "Installing systemd user units: io-mcp-digest.service + .timer"
  render_unit "${SCRIPT_DIR}/io-mcp-digest.service" "${UNIT_DIR}/io-mcp-digest.service"
  cp "${SCRIPT_DIR}/io-mcp-digest.timer" "${UNIT_DIR}/io-mcp-digest.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now io-mcp-digest.timer
  log "Digest timer enabled. Check: systemctl --user list-timers io-mcp-digest.timer"
fi

# ---------------------------------------------------------------------------
cat <<EOF

$(log "Done.")
  io-mcp binary : ${IO_MCP_BIN}
  config        : ~/.config/io-mcp/config.yaml   (edit before first real run)
  state db      : ~/.local/share/io-mcp/state.db

Next steps:
  1. Edit ~/.config/io-mcp/config.yaml (ollama/ntfy/prometheus URLs, interests).
  2. Test:   ${IO_MCP_BIN} config
             ${IO_MCP_BIN} digest --dry-run
  3. Serve:  ${IO_MCP_BIN} serve         (or enable the systemd unit with --systemd)

If you enabled systemd user units and want them to run while logged out:
  loginctl enable-linger \$USER
EOF
