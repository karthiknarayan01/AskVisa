#!/usr/bin/env bash
# Set everything up and run the eval suites.
#
#   ./eval.sh                                   every suite, local Qwen as model and judge
#   ./eval.sh evals/suites/permtracker.toml     one suite
#   ./eval.sh --repeat 1 --no-judge             any other arguments are passed to agent-eval
#   ./eval.sh --judge-model qwen3.5:27b         a bigger local judge (downloaded if missing)
#   ./eval.sh --provider anthropic              a hosted model instead (needs its API key)
#
# Before running it:
#   1. creates .venv next to this script and installs the package (again whenever
#      pyproject.toml changes)
#   2. for every local model in the run (the model and the judge): installs Ollama if it's
#      missing (after asking), starts it, and downloads the model if Ollama doesn't have it
#
# Defaults, each overridden by giving the option: --provider ollama, --model the Qwen that fits
# this computer's memory (9B, or 4B under 12 GB), --repeat 3, and for Ollama --parallel 1. The
# judge is agent-eval's default: the evaluated model. Results go to evals/results/. The exit
# status is 0 only when every suite passed.
#
# Written for bash 3.2 (macOS's /bin/bash): no mapfile, and no expanding an array that may be empty.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/setup.sh
source "$ROOT/scripts/setup.sh"

main() {
    local suites=() args=() provider="" model="" judge_provider="" judge_model="" no_judge=0
    local repeat_given=0 parallel_given=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            *.toml) suites+=("$(absolute "$1")") ;;
            --provider) provider="${2:-}"; shift ;;
            --model) model="${2:-}"; shift ;;
            --judge-provider) judge_provider="${2:-}"; args+=("$1" "$judge_provider"); shift ;;
            --judge-model) judge_model="${2:-}"; args+=("$1" "$judge_model"); shift ;;
            --no-judge) no_judge=1; args+=("$1") ;;
            --repeat) repeat_given=1; args+=("$1" "${2:-}"); shift ;;
            --parallel) parallel_given=1; args+=("$1" "${2:-}"); shift ;;
            *) args+=("$1") ;;
        esac
        shift
    done
    provider="${provider:-ollama}"
    [[ -n "$model" || "$provider" != "ollama" ]] || model="$(qwen_for_this_machine)"
    [[ $repeat_given -eq 1 ]] || args+=(--repeat 3)
    [[ $parallel_given -eq 1 || "$provider" != "ollama" ]] || args+=(--parallel 1)
    if [[ $no_judge -eq 0 && "$judge_provider" == "ollama" && "$provider" != "ollama" && -z "$judge_model" ]]; then
        judge_model="$(qwen_for_this_machine)"
        args+=(--judge-model "$judge_model")
    fi
    if [[ ${#suites[@]} -eq 0 ]]; then
        local found
        while IFS= read -r found; do suites+=("$found"); done < <(find "$ROOT/evals/suites" -name '*.toml' | sort)
    fi
    [[ ${#suites[@]} -gt 0 ]] || fail "No eval suites in $ROOT/evals/suites"

    install_package
    load_env_file
    prepare_local_models "$provider" "$model" "$no_judge" "${judge_provider:-$provider}" "${judge_model:-$model}"

    local model_args=(--provider "$provider")
    [[ -z "$model" ]] || model_args+=(--model "$model")
    local failed=0 suite
    for suite in "${suites[@]}"; do
        "$VENV/bin/agent-eval" "$suite" "${model_args[@]}" --out "$ROOT/evals/results" "${args[@]}" || failed=1
        echo
    done
    return $failed
}

prepare_local_models() {
    # $1 provider, $2 model, $3 no_judge (1 = no judge), $4 judge provider, $5 judge model.
    local wanted=()
    [[ "$1" != "ollama" ]] || wanted+=("$2")
    [[ "$3" -eq 1 || "$4" != "ollama" || -z "$5" ]] || wanted+=("$5")
    [[ ${#wanted[@]} -gt 0 ]] || return 0
    ensure_ollama_running
    local name
    for name in $(printf '%s\n' "${wanted[@]}" | sort -u); do
        ensure_model_downloaded "$name"
    done
}

absolute() {
    # A path as an absolute one, so it still works from any directory.
    [[ "$1" == /* ]] && echo "$1" || echo "$PWD/$1"
}

main "$@"
