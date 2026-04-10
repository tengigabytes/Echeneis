"""Telegram message handlers.

Handles plain text, photos, and document messages.
"""

import logging
from base64 import b64encode
from io import BytesIO
from typing import Any

from telegram import Document, PhotoSize, Update
from telegram.ext import ContextTypes

from echeneis.bot.gateway_client import GatewayClient, GatewayError
from echeneis.bot.middleware import is_authorized

logger = logging.getLogger(__name__)

# File extensions we'll attempt to read as text
_TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".rb",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".xml",
    ".csv",
    ".ini",
    ".cfg",
    ".txt",
    ".md",
    ".rst",
    ".log",
    ".html",
    ".css",
    ".scss",
    ".sql",
    ".dockerfile",
    ".makefile",
}

_MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
_DEFAULT_MAX_TOKENS = 8192
_TG_MSG_LIMIT = 4096


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages — route via default (A tier)."""
    if not is_authorized(update):
        return

    text = update.message.text
    if not text:
        return

    gateway: GatewayClient = context.bot_data["gateway"]
    sent = await update.message.reply_text("處理中…")

    try:
        result = await gateway.chat(
            messages=[{"role": "user", "content": text}],
            max_tokens=_DEFAULT_MAX_TOKENS,
        )
        reply = _format_reply(result)
        await _send_reply(sent, reply)
    except GatewayError as e:
        logger.error("Gateway error for text message: %s", e)
        await sent.edit_text("抱歉，處理請求時發生錯誤。請稍後再試。")


async def photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo messages — route via vision pipeline."""
    if not is_authorized(update):
        return

    # Get the highest-resolution photo
    photo: PhotoSize = update.message.photo[-1]
    caption = update.message.caption or "請描述這張圖片。"

    gateway: GatewayClient = context.bot_data["gateway"]
    sent = await update.message.reply_text("分析圖片中…")

    try:
        file = await photo.get_file()
        buf = BytesIO()
        await file.download_to_memory(buf)
        image_b64 = b64encode(buf.getvalue()).decode("ascii")
        data_url = f"data:image/jpeg;base64,{image_b64}"

        content = [
            {"type": "text", "text": caption},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        result = await gateway.chat(
            messages=[{"role": "user", "content": content}],
            max_tokens=_DEFAULT_MAX_TOKENS,
        )
        reply = _format_reply(result)
        await _send_reply(sent, reply)
    except GatewayError as e:
        logger.error("Gateway error for photo: %s", e)
        await sent.edit_text("抱歉，無法分析這張圖片。請稍後再試。")
    except Exception as e:
        logger.error("Error downloading photo: %s", e)
        await sent.edit_text("抱歉，下載圖片時發生錯誤。")


async def document_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document/file messages — download and process as text context."""
    if not is_authorized(update):
        return

    doc: Document = update.message.document
    caption = update.message.caption or ""
    file_name = doc.file_name or "unknown"
    file_ext = _get_extension(file_name)

    # Check size limit
    if doc.file_size and doc.file_size > _MAX_FILE_SIZE:
        await update.message.reply_text(
            f"檔案太大（{doc.file_size // 1024 // 1024} MB），上限為 5 MB。"
        )
        return

    # Only handle known text file types
    if file_ext not in _TEXT_EXTENSIONS:
        await update.message.reply_text(
            f"不支援的檔案格式：{file_ext}\n" "目前支援：程式碼、設定檔、純文字檔。"
        )
        return

    gateway: GatewayClient = context.bot_data["gateway"]
    sent = await update.message.reply_text("讀取檔案中…")

    try:
        file = await doc.get_file()
        buf = BytesIO()
        await file.download_to_memory(buf)
        file_content = buf.getvalue().decode("utf-8", errors="replace")

        prompt = f"以下是檔案 `{file_name}` 的內容：\n\n```\n{file_content}\n```"
        if caption:
            prompt = f"{caption}\n\n{prompt}"
        else:
            prompt += "\n\n請分析這個檔案。"

        result = await gateway.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=_DEFAULT_MAX_TOKENS,
        )
        reply = _format_reply(result)
        await _send_reply(sent, reply)
    except GatewayError as e:
        logger.error("Gateway error for document: %s", e)
        await sent.edit_text("抱歉，處理檔案時發生錯誤。請稍後再試。")
    except Exception as e:
        logger.error("Error processing document: %s", e)
        await sent.edit_text("抱歉，讀取檔案時發生錯誤。")


def _format_reply(result: dict) -> str:
    """Format gateway response with model name prefix and truncation warning."""
    content = result["choices"][0]["message"]["content"]
    model = result.get("model", "unknown")
    finish = result["choices"][0].get("finish_reason", "")
    header = f"[{model}]"
    if finish == "length":
        header += " ⚠️ 回覆被截斷"
    return f"{header}\n\n{content}"


async def _send_reply(sent_msg: Any, text: str) -> None:
    """Send reply, splitting into multiple messages if over Telegram limit."""
    if len(text) <= _TG_MSG_LIMIT:
        await sent_msg.edit_text(text)
        return

    # First chunk goes into the existing "processing" message
    chunks = _split_text(text)
    await sent_msg.edit_text(chunks[0])
    # Remaining chunks as new messages in the same chat
    chat = sent_msg.chat
    for chunk in chunks[1:]:
        await chat.send_message(chunk)


def _split_text(text: str, limit: int = _TG_MSG_LIMIT) -> list[str]:
    """Split text into chunks that fit within Telegram's message limit."""
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to split at last newline before limit
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def _get_extension(filename: str) -> str:
    """Extract lowercase file extension including the dot."""
    dot_idx = filename.rfind(".")
    if dot_idx == -1:
        return ""
    return filename[dot_idx:].lower()
