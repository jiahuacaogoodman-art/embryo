"""通道抽象基类 — 所有通道的统一接口"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
import time


@dataclass
class IncomingMessage:
    """归一化的入站消息"""
    text: str
    user_id: str
    channel: str  # "cli" / "web" / "telegram"
    message_id: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    # 附件（图片等）
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OutgoingMessage:
    """归一化的出站消息"""
    text: str
    user_id: str
    channel: str
    reply_to: str = ""  # 回复哪条消息
    metadata: dict[str, Any] = field(default_factory=dict)


class Channel(ABC):
    """通道抽象基类

    每个通道实现必须：
    1. 能启动监听（start）
    2. 收到消息时调用 on_message 回调
    3. 能发送回复（send）
    4. 能优雅停止（stop）
    """

    def __init__(self, name: str):
        self.name = name
        self._on_message: Optional[Callable[[IncomingMessage], None]] = None

    def set_message_handler(self, handler: Callable[[IncomingMessage], None]):
        """设置消息处理回调"""
        self._on_message = handler

    @abstractmethod
    async def start(self):
        """启动通道监听"""
        ...

    @abstractmethod
    async def stop(self):
        """停止通道"""
        ...

    @abstractmethod
    async def send(self, message: OutgoingMessage):
        """发送消息到通道"""
        ...

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """通道是否在运行"""
        ...
