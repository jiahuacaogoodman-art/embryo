"""Gateway 路由器 — 多通道消息统一调度

管理所有通道的启动/停止，将入站消息路由到 Agent。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional, TYPE_CHECKING

from .channels.base import Channel, IncomingMessage, OutgoingMessage
from ..logging import get_logger

if TYPE_CHECKING:
    from ..agent import EmbryoAgent

logger = get_logger("gateway")


class GatewayRouter:
    """Gateway 路由器

    职责：
    1. 管理多个通道的生命周期
    2. 接收入站消息 → 路由到 Agent
    3. 将 Agent 回复发回对应通道
    """

    def __init__(self, agent: "EmbryoAgent"):
        self.agent = agent
        self._channels: dict[str, Channel] = {}
        self._running = False

    def add_channel(self, channel: Channel):
        """注册通道"""
        self._channels[channel.name] = channel
        channel.set_message_handler(self._handle_message)
        logger.info("gateway_channel_added", channel=channel.name)

    async def start(self):
        """启动所有通道"""
        self._running = True
        logger.info("gateway_starting", channels=list(self._channels.keys()))

        tasks = []
        for name, channel in self._channels.items():
            tasks.append(asyncio.create_task(self._start_channel(name, channel)))

        # 等待所有通道（或直到被停止）
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self):
        """停止所有通道"""
        self._running = False
        for name, channel in self._channels.items():
            try:
                await channel.stop()
                logger.info("gateway_channel_stopped", channel=name)
            except Exception as e:
                logger.error("gateway_channel_stop_error", channel=name, error=str(e))

    async def _start_channel(self, name: str, channel: Channel):
        """启动单个通道（带异常保护）"""
        try:
            await channel.start()
        except Exception as e:
            logger.error("gateway_channel_error", channel=name, error=str(e))

    def _handle_message(self, message: IncomingMessage):
        """处理入站消息（由通道回调调用）"""
        logger.info(
            "gateway_message_received",
            channel=message.channel,
            user_id=message.user_id,
            length=len(message.text),
        )

        # 调用 Agent
        try:
            response = self.agent.chat(message.text)
        except Exception as e:
            response = f"处理失败: {e}"
            logger.error("gateway_chat_error", error=str(e))

        # 回复到对应通道
        outgoing = OutgoingMessage(
            text=response,
            user_id=message.user_id,
            channel=message.channel,
            reply_to=message.message_id,
        )

        channel = self._channels.get(message.channel)
        if channel:
            # 异步发送
            asyncio.create_task(channel.send(outgoing))

    def run(self):
        """同步阻塞方式启动 Gateway"""
        asyncio.run(self.start())

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_channels(self) -> list[str]:
        return [name for name, ch in self._channels.items() if ch.is_running]
