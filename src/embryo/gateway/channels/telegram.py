"""Telegram Bot 通道

通过 python-telegram-bot 库接入 Telegram。
支持：
- 文本消息收发
- 长消息自动分段
- 多用户独立会话
- /start /new /memory /skills 命令
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional, TYPE_CHECKING

from .base import Channel, IncomingMessage, OutgoingMessage
from ...logging import get_logger

if TYPE_CHECKING:
    from ...agent import EmbryoAgent

logger = get_logger("telegram_channel")

# Telegram 单条消息最大长度
MAX_MESSAGE_LENGTH = 4096


class TelegramChannel(Channel):
    """Telegram Bot 通道"""

    def __init__(self, agent: "EmbryoAgent", token: str):
        """
        Args:
            agent: EmbryoAgent 实例
            token: Telegram Bot Token (从 @BotFather 获取)
        """
        super().__init__("telegram")
        self.agent = agent
        self.token = token
        self._app = None
        self._running = False
        # 每个 user_id 维护独立会话状态
        self._user_sessions: dict[int, str] = {}  # telegram_user_id → session_id

    async def start(self):
        """启动 Telegram Bot"""
        try:
            from telegram import Update
            from telegram.ext import (
                Application,
                CommandHandler,
                MessageHandler,
                filters,
            )
        except ImportError:
            raise ImportError(
                "Telegram 通道需要 python-telegram-bot: "
                "pip install python-telegram-bot"
            )

        self._app = Application.builder().token(self.token).build()

        # 注册处理器
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("new", self._handle_new_session))
        self._app.add_handler(CommandHandler("memory", self._handle_memory))
        self._app.add_handler(CommandHandler("skills", self._handle_skills))
        self._app.add_handler(CommandHandler("status", self._handle_status))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        self._running = True
        logger.info("telegram_bot_starting")

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

        logger.info("telegram_bot_started")

    async def stop(self):
        """停止 Telegram Bot"""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        self._running = False
        logger.info("telegram_bot_stopped")

    async def send(self, message: OutgoingMessage):
        """发送消息到 Telegram"""
        if not self._app:
            return

        bot = self._app.bot
        text = message.text
        chat_id = int(message.user_id)

        # 长消息分段发送
        chunks = self._split_message(text)
        for chunk in chunks:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode="Markdown" if "```" in chunk or "**" in chunk else None,
            )

    @property
    def is_running(self) -> bool:
        return self._running

    # === 消息处理器 ===

    async def _handle_start(self, update, context):
        """处理 /start 命令"""
        user = update.effective_user
        await update.message.reply_text(
            f"你好 {user.first_name}！我是 Embryo Agent。\n\n"
            f"直接发送消息即可对话。可用命令：\n"
            f"/new - 开始新会话\n"
            f"/memory - 查看记忆\n"
            f"/skills - 查看技能\n"
            f"/status - Agent 状态"
        )

    async def _handle_new_session(self, update, context):
        """处理 /new 命令"""
        user_id = update.effective_user.id
        self._user_sessions.pop(user_id, None)
        self.agent.new_session()
        await update.message.reply_text("✓ 新会话已创建")

    async def _handle_memory(self, update, context):
        """处理 /memory 命令"""
        entries = self.agent.memory.recall_all()
        if not entries:
            await update.message.reply_text("(记忆为空)")
            return

        text = f"共 {len(entries)} 条记忆：\n\n"
        for e in entries[-10:]:
            text += f"• [{e.category}] {e.content[:60]}\n"
        await update.message.reply_text(text)

    async def _handle_skills(self, update, context):
        """处理 /skills 命令"""
        skills = self.agent.skills.list_skills()
        if not skills:
            await update.message.reply_text("(暂无 Skill)")
            return

        text = f"共 {len(skills)} 个 Skill：\n\n"
        for s in skills:
            text += f"• **{s.name}** - {s.description}\n"
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _handle_status(self, update, context):
        """处理 /status 命令"""
        text = (
            f"🤖 Embryo Agent v0.2.0\n"
            f"模型: {self.agent.config.llm.model}\n"
            f"工具: {self.agent.tools.count} 个\n"
            f"记忆: {self.agent.memory.count} 条\n"
            f"技能: {len(self.agent.skills.list_skills())} 个"
        )
        await update.message.reply_text(text)

    async def _handle_message(self, update, context):
        """处理普通文本消息"""
        user_id = update.effective_user.id
        text = update.message.text

        if not text:
            return

        logger.info("telegram_message_received", user_id=user_id, length=len(text))

        # 发送"正在思考"提示
        await update.message.chat.send_action("typing")

        # 在线程中执行 Agent（避免阻塞 event loop）
        try:
            response = await asyncio.to_thread(self.agent.chat, text)
        except Exception as e:
            response = f"处理失败: {e}"
            logger.error("telegram_chat_error", user_id=user_id, error=str(e))

        # 发送回复
        await self.send(OutgoingMessage(
            text=response,
            user_id=str(user_id),
            channel="telegram",
        ))

    # === 工具方法 ===

    def _split_message(self, text: str) -> list[str]:
        """将长消息拆分为多段（不超过 Telegram 限制）"""
        if len(text) <= MAX_MESSAGE_LENGTH:
            return [text]

        chunks = []
        while text:
            if len(text) <= MAX_MESSAGE_LENGTH:
                chunks.append(text)
                break

            # 尝试在换行处切分
            split_pos = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
            if split_pos == -1 or split_pos < MAX_MESSAGE_LENGTH // 2:
                split_pos = MAX_MESSAGE_LENGTH

            chunks.append(text[:split_pos])
            text = text[split_pos:].lstrip("\n")

        return chunks
