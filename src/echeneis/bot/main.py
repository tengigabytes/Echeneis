"""Telegram Bot entry point.

Initializes the bot application, registers handlers,
and starts polling for updates.
"""

import logging
import os
import sys

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from echeneis.bot.gateway_client import GatewayClient
from echeneis.bot.handlers.commands import (
    fast_command,
    help_command,
    model_command,
    start_command,
    think_command,
)
from echeneis.bot.handlers.messages import (
    document_message,
    photo_message,
    text_message,
)

logger = logging.getLogger(__name__)


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
        logger.warning(
            "TELEGRAM_ALLOWED_USERS is empty — bot will reject all messages"
        )

    gateway = GatewayClient()

    app = ApplicationBuilder().token(token).build()

    # Store gateway client in bot_data for access in handlers
    app.bot_data["gateway"] = gateway

    # Register command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("think", think_command))
    app.add_handler(CommandHandler("fast", fast_command))
    app.add_handler(CommandHandler("model", model_command))

    # Register message handlers (order matters — more specific first)
    app.add_handler(MessageHandler(filters.PHOTO, photo_message))
    app.add_handler(MessageHandler(filters.Document.ALL, document_message))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_message)
    )

    logger.info("Echeneis Bot starting…")
    app.run_polling()


if __name__ == "__main__":
    main()
