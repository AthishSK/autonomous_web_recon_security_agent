#!/usr/bin/env bash
# =============================================================================
# run_local.sh — Start the recon agent backend in development mode
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Colour

# ---------------------------------------------------------------------------
# Config (override via env vars before calling this script)
# ---------------------------------------------------------------------------
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"
LOG_LEVEL="${LOG_LEVEL:-debug}"
ENV_FILE="${ENV_FILE:-.env}"
VENV_DIR="${VENV_DIR:-.venv}"
APP_MODULE="app.main:app"
RELOAD="--reload"   # remove for prod-like local run: RELOAD=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

banner() {
  echo -e "${BOLD}"
  echo "  ╔══════════════════════════════════════╗"
  echo "  ║        Recon Agent — Dev Server      ║"
  echo "  ╚══════════════════════════════════════╝"
  echo -e "${NC}"
}

# ---------------------------------------------------------------------------
# 1. Banner
# ---------------------------------------------------------------------------
banner

# ---------------------------------------------------------------------------
# 2. Resolve project root (directory containing this script)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
info "Project root: ${PROJECT_ROOT}"

# ---------------------------------------------------------------------------
# 3. Check required binaries
# ---------------------------------------------------------------------------
for cmd in python3 pip; do
  command -v "$cmd" &>/dev/null || error "'$cmd' not found. Please install Python 3.11+."
done

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [[ "$PYTHON_MAJOR" -lt 3 || ( "$PYTHON_MAJOR" -eq 3 && "$PYTHON_MINOR" -lt 11 ) ]]; then
  error "Python 3.11+ required. Found: ${PYTHON_VERSION}"
fi
success "Python ${PYTHON_VERSION} detected"

# ---------------------------------------------------------------------------
# 4. Virtual environment
# ---------------------------------------------------------------------------
if [[ ! -d "$VENV_DIR" ]]; then
  info "Creating virtual environment at ${VENV_DIR}..."
  python3 -m venv "$VENV_DIR"
  success "Virtual environment created"
fi

# Activate
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
success "Virtual environment activated"

# ---------------------------------------------------------------------------
# 5. Install / sync dependencies
# ---------------------------------------------------------------------------
if [[ ! -f requirements.txt ]]; then
  warn "requirements.txt not found — skipping dependency install"
else
  info "Installing dependencies from requirements.txt..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
  success "Dependencies installed"
fi

# ---------------------------------------------------------------------------
# 6. Load & validate .env
# ---------------------------------------------------------------------------
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f ".env.example" ]]; then
    warn ".env not found. Copying from .env.example — fill in real values before running."
    cp .env.example .env
    ENV_FILE=".env"
  else
    warn "No .env file found. Continuing without it — make sure env vars are set."
  fi
fi

if [[ -f "$ENV_FILE" ]]; then
  info "Loading environment from ${ENV_FILE}"
  # Export vars, skip comments and blank lines
  set -o allexport
  # shellcheck disable=SC1090
  source <(grep -v '^\s*#' "$ENV_FILE" | grep -v '^\s*$')
  set +o allexport
  success "Environment loaded"
fi

# Check critical env vars
MISSING_VARS=()
[[ -z "${ANTHROPIC_API_KEY:-}" ]] && MISSING_VARS+=("ANTHROPIC_API_KEY")

if [[ ${#MISSING_VARS[@]} -gt 0 ]]; then
  error "Missing required environment variable(s): ${MISSING_VARS[*]}"
fi
success "Environment variables validated"

# ---------------------------------------------------------------------------
# 7. Optional: run quick pre-flight checks
# ---------------------------------------------------------------------------
info "Running pre-flight import check..."
python3 -c "from app.main import app" 2>/dev/null \
  && success "App imports OK" \
  || error "App failed to import. Check your code for syntax/import errors."

# ---------------------------------------------------------------------------
# 8. Print startup summary
# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}  Starting server${NC}"
echo -e "  ├── Host       : ${CYAN}http://${HOST}:${PORT}${NC}"
echo -e "  ├── Docs       : ${CYAN}http://localhost:${PORT}/docs${NC}"
echo -e "  ├── Redoc      : ${CYAN}http://localhost:${PORT}/redoc${NC}"
echo -e "  ├── Module     : ${APP_MODULE}"
echo -e "  ├── Workers    : ${WORKERS}"
echo -e "  ├── Log level  : ${LOG_LEVEL}"
echo -e "  └── Hot reload : $([ -n "$RELOAD" ] && echo 'enabled' || echo 'disabled')"
echo ""

# ---------------------------------------------------------------------------
# 9. Launch uvicorn
# ---------------------------------------------------------------------------
exec uvicorn "$APP_MODULE" \
  --host "$HOST" \
  --port "$PORT" \
  --workers "$WORKERS" \
  --log-level "$LOG_LEVEL" \
  $RELOAD