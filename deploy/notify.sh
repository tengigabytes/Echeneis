#!/usr/bin/env bash
# Echeneis — Telegram notification helper.
#
# Source this file and call:
#   notify_telegram "<text>"              # default parse_mode=Markdown
#   notify_telegram "<text>" ""           # plain text (no parse_mode)
#   notify_telegram "<text>" HTML         # HTML parse_mode
#
# Silently no-ops if TELEGRAM_BOT_TOKEN or chat id are unset,
# so it's safe to call from any deploy script.
#
# Environment (loaded from /opt/echeneis/.env if present):
#   TELEGRAM_BOT_TOKEN        — bot token
#   TELEGRAM_ADMIN_CHAT_ID    — admin chat id (preferred)
#   TELEGRAM_ALLOWED_USERS    — comma-separated fallback (first entry used)

_ECHENEIS_ENV_FILE="${ECHENEIS_ENV_FILE:-/opt/echeneis/.env}"
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
