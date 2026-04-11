#!/usr/bin/env bash
# Echeneis — Telegram notification helper.
#
# Source this file and call notify_telegram "<markdown text>".
# Silently no-ops if TELEGRAM_BOT_TOKEN or chat id are unset,
# so it's safe to call from any deploy script.
#
# Environment (loaded from /opt/echeneis/.env if present):
#   TELEGRAM_BOT_TOKEN        — bot token
#   TELEGRAM_ADMIN_CHAT_ID    — admin chat id (preferred)
#   TELEGRAM_ALLOWED_USERS    — comma-separated fallback (first entry used)

_ECHENEIS_ENV_FILE="${ECHENEIS_ENV_FILE:-/opt/echeneis/.env}"

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
    _load_env

    if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
        return 0
    fi
    local chat_id
    chat_id=$(_resolve_chat_id)
    if [[ -z "${chat_id}" ]]; then
        return 0
    fi

    curl -fsS -m 10 \
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${chat_id}" \
        --data-urlencode "text=${text}" \
        --data-urlencode "parse_mode=Markdown" \
        --data-urlencode "disable_web_page_preview=true" \
        > /dev/null 2>&1 || true
}
