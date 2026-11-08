#!/usr/bin/env bash
# Set up the assistant and start its server.
#
#   ./start.sh                 first run: install, choose a model, then serve; later runs: serve
#   ./start.sh --setup         choose the model again
#   ./start.sh --port 9000     any other arguments are passed to askvisa-server
#
# First run:
#   1. creates .venv next to this script and installs the package (again whenever
#      pyproject.toml changes), and builds the web app (installing Node.js first if needed,
#      after asking; again whenever the frontend changes)
#   2. asks which model to use: Qwen on this computer (through Ollama), Anthropic or DeepSeek
#   3. local: installs Ollama if it's missing (after asking), starts it, downloads the model
#      hosted: asks for the API key unless it is already in the environment
#   4. saves the choice to ~/.config/askvisa/config (readable only by you)
#
# Service keys the tools use (PERMTRACKER_API_KEY) are read from a .env file next to this script
# when it exists, as KEY=value lines; it is git-ignored and never asked for.
#
# Later runs read that file and start the server straight away. For a local model they only make
# sure Ollama is running and the model is there, which takes a moment.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/setup.sh
source "$ROOT/scripts/setup.sh"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/askvisa"
CONFIG="$CONFIG_DIR/config"

PROVIDER=""
MODEL=""
ANTHROPIC_KEY=""
DEEPSEEK_KEY=""

main() {
    local setup=0 args=()
    for arg in "$@"; do
        if [[ "$arg" == "--setup" ]]; then setup=1; else args+=("$arg"); fi
    done

    install_package
    build_web_app
    load_env_file
    if [[ $setup -eq 0 ]] && load_config; then
        say "Using $MODEL ($PROVIDER). Run ./start.sh --setup to change it."
    else
        choose_model
        save_config
    fi
    if [[ "$PROVIDER" == "ollama" ]]; then
        ensure_ollama_running
        ensure_model_downloaded "$MODEL"
    fi
    launch "${args[@]+"${args[@]}"}"
}

# --- the model choice and the saved config ----------------------------------------------

load_config() {
    # Read the saved choice. The file is parsed as KEY=value lines, never executed.
    [[ -f "$CONFIG" ]] || return 1
    local key value
    while IFS='=' read -r key value; do
        case "$key" in
            PROVIDER) PROVIDER="$value" ;;
            MODEL) MODEL="$value" ;;
            ANTHROPIC_API_KEY) ANTHROPIC_KEY="$value" ;;
            DEEPSEEK_API_KEY) DEEPSEEK_KEY="$value" ;;
        esac
    done < "$CONFIG"
    case "$PROVIDER" in
        ollama) [[ -n "$MODEL" ]] ;;
        anthropic) [[ -n "$MODEL" && -n "${ANTHROPIC_API_KEY:-$ANTHROPIC_KEY}" ]] ;;
        deepseek) [[ -n "$MODEL" && -n "${DEEPSEEK_API_KEY:-$DEEPSEEK_KEY}" ]] ;;
        *) return 1 ;;
    esac
}

choose_model() {
    local qwen
    qwen="$(qwen_for_this_machine)"
    echo
    echo "Which model should the agent use?"
    echo "  1) Qwen on this computer ($qwen through Ollama): free, private, no key"
    echo "  2) Anthropic Claude (needs an API key)"
    echo "  3) DeepSeek (needs an API key)"
    local answer
    while true; do
        read -r -p "Choose 1, 2 or 3 [1]: " answer || fail "No model is set up yet. Run ./start.sh in a terminal to choose one."
        case "${answer:-1}" in
            1) PROVIDER=ollama MODEL="$qwen"; return ;;
            2) PROVIDER=anthropic MODEL=claude-sonnet-5-5; ask_key ANTHROPIC_API_KEY ANTHROPIC_KEY; return ;;
            3) PROVIDER=deepseek MODEL=deepseek-v4-pro; ask_key DEEPSEEK_API_KEY DEEPSEEK_KEY; return ;;
        esac
    done
}

ask_key() {
    # $1: the environment variable the CLI reads; $2: the variable to keep it in here.
    local env_name="$1" target="$2" key
    if [[ -n "${!env_name:-}" ]]; then
        say "Using $env_name from the environment."
        return
    fi
    while true; do
        read -r -s -p "Paste your $env_name (input is hidden): " key || fail "No $env_name given."
        echo
        [[ -n "$key" ]] && break
    done
    printf -v "$target" '%s' "$key"
}

save_config() {
    mkdir -p "$CONFIG_DIR"
    (
        umask 077
        {
            echo "PROVIDER=$PROVIDER"
            echo "MODEL=$MODEL"
            [[ -n "$ANTHROPIC_KEY" ]] && echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY"
            [[ -n "$DEEPSEEK_KEY" ]] && echo "DEEPSEEK_API_KEY=$DEEPSEEK_KEY"
            true
        } > "$CONFIG"
    )
    chmod 600 "$CONFIG"
    say "Saved to $CONFIG."
}

# --- the server -----------------------------------------------------------------------------

launch() {
    [[ -n "$ANTHROPIC_KEY" && -z "${ANTHROPIC_API_KEY:-}" ]] && export ANTHROPIC_API_KEY="$ANTHROPIC_KEY"
    [[ -n "$DEEPSEEK_KEY" && -z "${DEEPSEEK_API_KEY:-}" ]] && export DEEPSEEK_API_KEY="$DEEPSEEK_KEY"
    open_browser_when_ready "$@"
    exec "$VENV/bin/askvisa-server" --provider "$PROVIDER" --model "$MODEL" "$@"
}

open_browser_when_ready() {
    # Open the app in the browser once the server answers. Skipped without a browser opener,
    # or when ASKVISA_NO_BROWSER is set.
    [[ -z "${ASKVISA_NO_BROWSER:-}" ]] || return 0
    local opener="" port=8000 previous=""
    command -v open >/dev/null 2>&1 && [[ "$(uname)" == "Darwin" ]] && opener=open
    command -v xdg-open >/dev/null 2>&1 && opener="${opener:-xdg-open}"
    [[ -n "$opener" ]] || return 0
    for arg in "$@"; do
        [[ "$previous" == "--port" ]] && port="$arg"
        previous="$arg"
    done
    local url="http://127.0.0.1:$port"
    (
        for _ in $(seq 1 60); do
            curl -fsS --max-time 1 "$url/api/health" >/dev/null 2>&1 && exec "$opener" "$url"
            sleep 0.5
        done
    ) >/dev/null 2>&1 &
}

main "$@"
