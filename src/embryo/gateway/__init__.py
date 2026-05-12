"""Gateway 层 — 多通道消息路由

- 单一 Gateway 守护进程接管所有消息通道
- 统一的消息抽象层（不同通道的消息归一化为 IncomingMessage）
- 会话路由：同一用户的消息路由到同一 Session
- 可并发处理多个用户的独立会话

支持的通道：
- CLI（已有的 REPL）
- Web HTTP API（FastAPI）
- Telegram Bot
"""

from .router import GatewayRouter
from .channels.base import Channel, IncomingMessage, OutgoingMessage

__all__ = ["GatewayRouter", "Channel", "IncomingMessage", "OutgoingMessage"]
