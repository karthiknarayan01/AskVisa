# shellcheck shell=bash
# Setup steps shared by start.sh and eval.sh. Source it after setting ROOT to the repository:
#
#   ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "$ROOT/scripts/setup.sh"
#
#   install_package               .venv with the package installed (again when pyproject.toml changes)
#   build_web_app                 frontend/dist built (again when the frontend's files change)
#   load_env_file                 export service keys (PERMTRACKER_API_KEY) from .env
#   qwen_for_this_machine         the Qwen model that fits this computer's memory
#   ensure_ollama_running         install Ollama (after asking) and start it if needed
#   ensure_model_downloaded M     pull model M unless Ollama already has it
#   say / fail                    a status line / an error line and exit 1

VENV="${AGENT_RUNTIME_VENV:-$ROOT/.venv}"
WEB_DIR="${AGENT_RUNTIME_WEB_DIR:-$ROOT/frontend}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/agent-runtime"
OLLAMA_URL="${OLLAMA_HOST:-127.0.0.1:11434}"
[[ "$OLLAMA_URL" == http* ]] || OLLAMA_URL="http://$OLLAMA_URL"

# --- the package ----------------------------------------------------------------------------

install_package() {
    # Reinstall only when pyproject.toml has changed since the last install.
    local stamp="$VENV/.agent-runtime-installed" want
    want="$(cksum < "$ROOT/pyproject.toml")"
    if [[ -x "$VENV/bin/agent-server" && -f "$stamp" && "$(cat "$stamp")" == "$want" ]]; then
        return
    fi
    if [[ ! -x "$VENV/bin/python" ]]; then
        local python
        python="$(find_python)" || fail "Python 3.11 or newer is needed. Install it (macOS: brew install python@3.13) and run this again."
        say "Creating a virtual environment with $("$python" --version)…"
        "$python" -m venv "$VENV"
    fi
    say "Installing the assistant…"
    mkdir -p "$STATE_DIR"
    local log="$STATE_DIR/install.log"
    if ! { "$VENV/bin/python" -m pip install --upgrade pip &&
        "$VENV/bin/python" -m pip install -e "$ROOT"; } > "$log" 2>&1; then
        tail -n 20 "$log" >&2
        fail "Installing failed; the full output is in $log"
    fi
    printf '%s\n' "$want" > "$stamp"
}

find_python() {
    # The first python on PATH that is 3.11 or newer.
    local candidate
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

# --- the web app ----------------------------------------------------------------------------

build_web_app() {
    # Rebuild only when the frontend's sources or lock file have changed since the last build.
    local stamp="$WEB_DIR/dist/.built-from" want
    want="$(web_sources_checksum)"
    if [[ -f "$WEB_DIR/dist/index.html" && -f "$stamp" && "$(cat "$stamp")" == "$want" ]]; then
        return
    fi
    command -v npm >/dev/null 2>&1 || install_node
    say "Building the web app…"
    mkdir -p "$STATE_DIR"
    local log="$STATE_DIR/web-build.log"
    if ! (cd "$WEB_DIR" && npm ci --no-audit --no-fund && npm run build) > "$log" 2>&1; then
        tail -n 20 "$log" >&2
        fail "Building the web app failed; the full output is in $log"
    fi
    printf '%s\n' "$want" > "$stamp"
}

web_sources_checksum() {
    # find fails on a path that doesn't exist; that only means there's nothing there to hash.
    (cd "$WEB_DIR" && { find package-lock.json index.html vite.config.ts tsconfig.json src public -type f 2>/dev/null || true; } |
        LC_ALL=C sort | xargs cat | cksum)
}

install_node() {
    local answer
    read -r -p "The web app needs Node.js to build, and it isn't installed. Install it now? [Y/n] " answer ||
        fail "Node.js isn't installed. Get it from https://nodejs.org (version 20 or newer) and run this again."
    [[ "${answer:-y}" =~ ^[Yy] ]] || fail "Install Node.js from https://nodejs.org (version 20 or newer) and run this again."
    if [[ "$(uname)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
        brew install node
    else
        fail "Install Node.js 20 or newer from https://nodejs.org, then run this again."
    fi
}

# --- service keys ---------------------------------------------------------------------------

load_env_file() {
    # Export the service keys from .env, parsed as KEY=value lines, never executed. A key
    # already set in the environment wins.
    local file="${AGENT_RUNTIME_ENV_FILE:-$ROOT/.env}" key value
    [[ -f "$file" ]] || return 0
    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        key="${key#export }"
        value="${value%$'\r'}"
        value="${value#\"}"; value="${value%\"}"
        case "$key" in
            PERMTRACKER_API_KEY) [[ -n "${!key:-}" || -z "$value" ]] || export "$key=$value" ;;
        esac
    done < "$file"
}

# --- Ollama ---------------------------------------------------------------------------------

qwen_for_this_machine() {
    # qwen3.5:9b needs about 8 GB with its context; below 12 GB of memory, the 4B model.
    local bytes=0
    if [[ "$(uname)" == "Darwin" ]]; then
        bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    elif [[ -r /proc/meminfo ]]; then
        bytes="$(awk '/MemTotal/ {print $2 * 1024}' /proc/meminfo)"
    fi
    if [[ "$bytes" -gt 0 && "$bytes" -lt $((12 * 1024 * 1024 * 1024)) ]]; then
        echo "qwen3.5:4b"
    else
        echo "qwen3.5:9b"
    fi
}

ensure_ollama_running() {
    command -v ollama >/dev/null 2>&1 || install_ollama
    ollama_up && return
    say "Starting Ollama…"
    if [[ "$(uname)" == "Darwin" && -d /Applications/Ollama.app ]]; then
        open -a Ollama
    else
        mkdir -p "$STATE_DIR"
        nohup ollama serve >> "$STATE_DIR/ollama.log" 2>&1 &
    fi
    local waited=0
    until ollama_up; do
        sleep 1
        waited=$((waited + 1))
        [[ $waited -lt 30 ]] || fail "Ollama didn't start within 30 seconds; see $STATE_DIR/ollama.log"
    done
}

ollama_up() {
    curl -fsS --max-time 2 "$OLLAMA_URL/api/version" >/dev/null 2>&1
}

install_ollama() {
    local answer
    read -r -p "Ollama runs the model on this computer and isn't installed. Install it now? [Y/n] " answer ||
        fail "Ollama isn't installed. Get it from https://ollama.com/download and run this again."
    [[ "${answer:-y}" =~ ^[Yy] ]] || fail "Install Ollama from https://ollama.com/download, or pick a hosted model instead."
    if [[ "$(uname)" == "Darwin" ]]; then
        command -v brew >/dev/null 2>&1 ||
            fail "Download the Ollama app from https://ollama.com/download, open it once, then run this again."
        brew install ollama
    else
        curl -fsSL https://ollama.com/install.sh | sh
    fi
}

ensure_model_downloaded() {
    # $1: the model, e.g. qwen3.5:9b
    ollama show "$1" >/dev/null 2>&1 && return
    say "Downloading $1 (once; several GB, so this can take a while)…"
    ollama pull "$1"
}

# --- output ---------------------------------------------------------------------------------

say() { printf '\033[36m›\033[0m %s\n' "$*"; }
fail() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
