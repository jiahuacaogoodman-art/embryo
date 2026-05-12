"""API Authentication & Authorization

Web API 必须有基本的鉴权机制：
- X-Embryo-Token header 验证
- localhost-only 默认绑定
- CORS 白名单（不再 allow_origins=["*"]）
- Rate limiting（简单的滑动窗口）
- Audit logging
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field

from ..logging import get_logger

logger = get_logger(__name__)


class AuthConfig(BaseModel):
    """API 鉴权配置"""

    # Token 认证
    enabled: bool = True
    token: str = Field(default_factory=lambda: os.environ.get("EMBRYO_API_TOKEN", ""))
    token_header: str = "X-Embryo-Token"

    # 绑定限制
    bind_host: str = "127.0.0.1"  # 默认只监听 localhost
    bind_port: int = 8642

    # CORS
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:8642", "http://127.0.0.1:8642"]
    )

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60  # 每窗口允许的请求数
    rate_limit_window_sec: int = 60  # 窗口大小（秒）

    def generate_token(self) -> str:
        """生成安全的 API token"""
        token = secrets.token_urlsafe(32)
        self.token = token
        return token

    def get_or_generate_token(self) -> str:
        """获取已有 token 或生成新 token"""
        if not self.token:
            return self.generate_token()
        return self.token


class RateLimiter:
    """简单的滑动窗口 rate limiter"""

    def __init__(self, max_requests: int = 60, window_sec: int = 60):
        self._max_requests = max_requests
        self._window_sec = window_sec
        self._requests: dict[str, deque] = {}  # client_id → timestamps

    def is_allowed(self, client_id: str = "default") -> bool:
        """检查请求是否被允许"""
        now = time.time()
        if client_id not in self._requests:
            self._requests[client_id] = deque()

        window = self._requests[client_id]

        # 移除窗口外的旧请求
        while window and window[0] < now - self._window_sec:
            window.popleft()

        # 检查是否超限
        if len(window) >= self._max_requests:
            return False

        # 记录本次请求
        window.append(now)
        return True

    def remaining(self, client_id: str = "default") -> int:
        """剩余请求数"""
        now = time.time()
        if client_id not in self._requests:
            return self._max_requests

        window = self._requests[client_id]
        while window and window[0] < now - self._window_sec:
            window.popleft()

        return max(0, self._max_requests - len(window))


@dataclass
class AuditEntry:
    """审计日志条目"""

    timestamp: float = field(default_factory=time.time)
    client_ip: str = ""
    method: str = ""
    path: str = ""
    tool_name: str = ""
    arguments_hash: str = ""  # 参数 hash（不记录原始值）
    decision: str = ""  # allow / deny / rate_limited
    user_agent: str = ""


class AuditLog:
    """审计日志

    记录所有 API 请求和工具调用，用于事后审查。
    """

    def __init__(self, max_entries: int = 10000):
        self._entries: deque[AuditEntry] = deque(maxlen=max_entries)

    def record(
        self,
        client_ip: str = "",
        method: str = "",
        path: str = "",
        tool_name: str = "",
        arguments: Optional[dict] = None,
        decision: str = "allow",
        user_agent: str = "",
    ) -> None:
        """记录一条审计日志"""
        args_hash = ""
        if arguments:
            args_str = str(sorted(arguments.items()))
            args_hash = hashlib.sha256(args_str.encode()).hexdigest()[:12]

        entry = AuditEntry(
            client_ip=client_ip,
            method=method,
            path=path,
            tool_name=tool_name,
            arguments_hash=args_hash,
            decision=decision,
            user_agent=user_agent,
        )
        self._entries.append(entry)

        logger.debug(
            "audit_log",
            client_ip=client_ip,
            path=path,
            tool=tool_name,
            decision=decision,
        )

    def get_recent(self, count: int = 100) -> list[AuditEntry]:
        """获取最近的审计条目"""
        entries = list(self._entries)
        return entries[-count:]

    @property
    def total_count(self) -> int:
        return len(self._entries)


class TokenValidator:
    """Token 验证器"""

    def __init__(self, config: AuthConfig):
        self._config = config
        self._rate_limiter = RateLimiter(
            max_requests=config.rate_limit_requests,
            window_sec=config.rate_limit_window_sec,
        )
        self._audit_log = AuditLog()

    @property
    def audit_log(self) -> AuditLog:
        return self._audit_log

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    def validate_request(
        self,
        token: str,
        client_ip: str = "",
        path: str = "",
        method: str = "",
        user_agent: str = "",
    ) -> tuple[bool, str]:
        """验证 API 请求

        Returns:
            (valid, error_message)
        """
        # 如果认证未启用，直接通过
        if not self._config.enabled:
            self._audit_log.record(
                client_ip=client_ip, method=method, path=path, decision="allow_no_auth"
            )
            return True, ""

        # Token 验证
        expected_token = self._config.get_or_generate_token()
        if not token or token != expected_token:
            self._audit_log.record(
                client_ip=client_ip,
                method=method,
                path=path,
                decision="deny_invalid_token",
                user_agent=user_agent,
            )
            logger.warning(
                "auth_failed",
                client_ip=client_ip,
                path=path,
                reason="invalid_token",
            )
            return False, "无效的 API Token"

        # Rate limiting
        if self._config.rate_limit_enabled:
            if not self._rate_limiter.is_allowed(client_ip or "default"):
                self._audit_log.record(
                    client_ip=client_ip,
                    method=method,
                    path=path,
                    decision="deny_rate_limited",
                )
                return False, "请求过于频繁，请稍后再试"

        # 通过
        self._audit_log.record(
            client_ip=client_ip,
            method=method,
            path=path,
            decision="allow",
            user_agent=user_agent,
        )
        return True, ""
