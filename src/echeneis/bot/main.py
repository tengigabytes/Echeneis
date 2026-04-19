"""Telegram Bot entry point.

Initializes the bot application, registers handlers,
starts the background system monitor, and begins polling for updates.
"""

import logging
import os
import subprocess
import sys
import time

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from echeneis.bot.conversation import ConversationStore
from echeneis.bot.gateway_client import GatewayClient
from echeneis.bot.handlers.admin_users import (
    adduser_command,
    ban_command,
    listusers_command,
    removeuser_command,
    unban_command,
    whois_command,
)
from echeneis.bot.handlers.bench import bench_command
from echeneis.bot.handlers.commands import (
    fast_command,
    help_command,
    model_command,
    models_command,
    start_command,
    think_command,
    use_command,
    whoami_command,
)
from echeneis.bot.handlers.eviction import eviction_command
from echeneis.bot.handlers.messages import (
    document_message,
    photo_message,
    text_message,
)
from echeneis.bot.handlers.requests import (
    pending_command,
    request_callback,
    request_command,
)
from echeneis.bot.handlers.status import set_boot_time, status_command
from echeneis.bot.monitor import SystemMonitor
from echeneis.gateway.notifier import send_telegram

logger = logging.getLogger(__name__)


def _git_sha() -> str:
    """Get current git commit SHA (short)."""
    env_sha = os.environ.get("GIT_SHA", "").strip()
    if env_sha:
        return env_sha[:7]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


async def _post_init(app) -> None:
    """Run after the Application is fully initialized.

    Sends a startup notification and starts the background monitor.
    """
    gateway: GatewayClient = app.bot_data["gateway"]
    monitor: SystemMonitor = app.bot_data["monitor"]

    # Record boot time for /status uptime display
    set_boot_time(time.time())

    # Count available models
    model_count = 0
    try:
        data = await gateway.models()
        model_count = sum(1 for m in data.get("models", []) if m.get("available"))
    except Exception:
        pass

    sha = _git_sha()
    await send_telegram(f"🟢 Echeneis Bot 啟動 (`{sha}`)\n模型：{model_count} 個可用")

    # Start background system monitor
    monitor.start()


async def _post_shutdown(app) -> None:
    """Cleanup on bot shutdown."""
    monitor: SystemMonitor = app.bot_data.get("monitor")
    if monitor:
        monitor.stop()
    gateway: GatewayClient = app.bot_data.get("gateway")
    if gateway:
        await gateway.close()


def main() -> None:
    """Start the Telegram bot."""
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set")
        sys.exit(1)

    allowed_users = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    if not allowed_users.strip():
        logger.warning("TELEGRAM_ALLOWED_USERS is empty — bot will reject all messages")

    gateway = GatewayClient()

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # Store shared components in bot_data for access in handlers
    app.bot_data["gateway"] = gateway
    app.bot_data["conversation"] = ConversationStore()
    app.bot_data["monitor"] = SystemMonitor(gateway)

    # Register command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("request", request_command))
    app.add_handler(CommandHandler("think", think_command))
    app.add_handler(CommandHandler("fast", fast_command))
    app.add_handler(CommandHandler("models", models_command))
    app.add_handler(CommandHandler("use", use_command))
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(CommandHandler("bench", bench_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("eviction", eviction_command))

    # Admin: user management
    app.add_handler(CommandHandler("adduser", adduser_command))
    app.add_handler(CommandHandler("removeuser", removeuser_command))
    app.add_handler(CommandHandler("listusers", listusers_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("whois", whois_command))
    app.add_handler(CommandHandler("pending", pending_command))

    # Inline keyboard callbacks (approve/deny/send-request)
    app.add_handler(CallbackQueryHandler(request_callback, pattern=r"^req:"))

    # Register message handlers (order matters — more specific first)
    app.add_handler(MessageHandler(filters.PHOTO, photo_message))
    app.add_handler(MessageHandler(filters.Document.ALL, document_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))

    logger.info("Echeneis Bot starting…")
    app.run_polling()


if __name__ == "__main__":
    main()
