#!/usr/bin/env bash
# Echeneis — Telegram notification helper.
#
# Source this file and call:
#   notify_telegram "<text>"              # default parse_mode=Markdown
#   notify_telegram "<text>" ""           # plain text (no parse_mode)
#   notify_telegram "<text>" HTML         # HTML parse_mode
#
# Also provides build_change_summary <old_sha> <new_sha> [repo_dir] — asks
# the local Echeneis gateway to translate commit subjects in the range to
# Traditional Chinese. Falls back to the raw English subjects if the
# gateway is unreachable.
#
# Silently no-ops if TELEGRAM_BOT_TOKEN or chat id are unset,
# so it's safe to call from any deploy script.
#
# Environment (loaded from /opt/echeneis/.env if present):
#   TELEGRAM_BOT_TOKEN        — bot token
#   TELEGRAM_ADMIN_CHAT_ID    — admin chat id (preferred)
#   TELEGRAM_ALLOWED_USERS    — comma-separated fallback (first entry used)
#   LITELLM_MASTER_KEY        — for dogfooded gateway calls

_ECHENEIS_ENV_FILE="${ECHENEIS_ENV_FILE:-/opt/echeneis/.env}"
_ECHENEIS_GATEWAY_URL="${ECHENEIS_GATEWAY_URL:-http://localhost:4000}"
_ECHENEIS_LOG_FILE="${ECHENEIS_LOG_FILE:-/var/log/echeneis-deploy.log}"

_notify_log() {
    # Append a timestamped line to the deploy log if writable. Silent on
    # any error so logging can never break the caller.
    if [[ -w "${_ECHENEIS_LOG_FILE}" ]] || { [[ ! -e "${_ECHENEIS_LOG_FILE}" ]] && [[ -w "$(dirname "${_ECHENEIS_LOG_FILE}")" ]]; }; then
        echo "$(date -Is) notify: $*" >> "${_ECHENEIS_LOG_FILE}" 2>/dev/null || true
    fi
}

_load_env() {
    if [[ -f "${_ECHENEIS_ENV_FILE}" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "${_ECHENEIS_ENV_FILE}"
        set +a
    fi
}

_resolve_chat_id() {
    if [[ -n "${TELEGRAM_ADMIN_CHAT_ID:-}" ]]; then
        echo "${TELEGRAM_ADMIN_CHAT_ID}"
        return
    fi
    if [[ -n "${TELEGRAM_ALLOWED_USERS:-}" ]]; then
        echo "${TELEGRAM_ALLOWED_USERS%%,*}"
    fi
}

notify_telegram() {
    local text="$1"
    local parse_mode="${2-Markdown}"
    _load_env

    if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
        _notify_log "skipped (TELEGRAM_BOT_TOKEN unset)"
        return 0
    fi
    local chat_id
    chat_id=$(_resolve_chat_id)
    if [[ -z "${chat_id}" ]]; then
        _notify_log "skipped (no chat id — TELEGRAM_ADMIN_CHAT_ID / TELEGRAM_ALLOWED_USERS unset)"
        return 0
    fi

    local -a curl_args=(
        -sS -m 10
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
        --data-urlencode "chat_id=${chat_id}"
        --data-urlencode "text=${text}"
        --data-urlencode "disable_web_page_preview=true"
    )
    if [[ -n "${parse_mode}" ]]; then
        curl_args+=(--data-urlencode "parse_mode=${parse_mode}")
    fi

    local response http_code
    # `|| true` so a network-level curl failure cannot kill callers that
    # run under `set -e`.
    response=$(curl "${curl_args[@]}" -o "/tmp/echeneis-notify-body.$$" -w '%{http_code}' 2>&1 || true)
    http_code="${response: -3}"
    if [[ "${http_code}" != "200" ]]; then
        local body
        body=$(head -c 300 /tmp/echeneis-notify-body.$$ 2>/dev/null || echo "")
        _notify_log "ERROR http=${http_code} body=${body}"
    fi
    rm -f /tmp/echeneis-notify-body.$$
    return 0
}

# Ask the local gateway to translate commit subjects to Traditional Chinese.
# Prints the translation on stdout, or the raw English subjects on failure.
# Returns 0 even if the gateway call fails — this must never break deploys.
build_change_summary() {
    local old_sha="$1"
    local new_sha="$2"
    local repo_dir="${3:-/opt/echeneis}"

    _load_env

    local commits
    if [[ -n "${old_sha}" ]]; then
        commits=$(cd "${repo_dir}" && git log --format='- %s' "${old_sha}..${new_sha}" 2>/dev/null || true)
    else
        commits=$(cd "${repo_dir}" && git log -1 --format='- %s' "${new_sha}" 2>/dev/null || true)
    fi
    if [[ -z "${commits}" ]]; then
        return 0
    fi

    if [[ -z "${LITELLM_MASTER_KEY:-}" ]]; then
        echo "${commits}"
        return 0
    fi

    # Build the JSON payload. The prompt contains the keyword 翻譯 so the
    # classifier routes this to the translation task type (A-tier Mistral).
    local payload
    payload=$(python3 -c '
import json, sys
commits = sys.stdin.read().rstrip()
prompt = (
    "請將以下 git commit 訊息翻譯成繁體中文並簡潔說明，每個 commit 一行，"
    "保留技術專有名詞不翻譯（例如 LiteLLM、API、YAML、regex、CI、tier 等）。"
    "不要使用 markdown 格式，只輸出翻譯結果，不要任何前言或結語：\n\n" + commits
)
print(json.dumps({"messages": [{"role": "user", "content": prompt}]}))
' <<< "${commits}") || {
        echo "${commits}"
        return 0
    }

    # Gateway may still be booting after update.sh's systemctl restart —
    # poll /health briefly so cron-triggered deploys don't fall back to the
    # raw English commits just because the container isn't listening yet.
    local health_ok=0
    local _i
    for _i in 1 2 3 4 5 6 7 8 9 10; do
        if curl -fsS -m 2 "${_ECHENEIS_GATEWAY_URL}/health" >/dev/null 2>&1; then
            health_ok=1
            break
        fi
        sleep 3
    done
    if [[ "${health_ok}" != "1" ]]; then
        _notify_log "build_change_summary: gateway /health not ready after 30s, using English fallback"
        echo "${commits}"
        return 0
    fi

    local response
    response=$(curl -fsS -m 20 \
        -X POST "${_ECHENEIS_GATEWAY_URL}/chat/completions" \
        -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" \
        -H "Content-Type: application/json" \
        -d "${payload}" 2>/dev/null) || {
        echo "${commits}"
        return 0
    }

    local chinese
    chinese=$(echo "${response}" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    text = data["choices"][0]["message"]["content"]
    print(text.strip())
except Exception:
    pass
' 2>/dev/null || true)

    if [[ -n "${chinese}" ]]; then
        echo "${chinese}"
    else
        echo "${commits}"
    fi
}
