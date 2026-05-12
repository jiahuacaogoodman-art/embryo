"""API Key 轮转池


当配置了多个 API Key 时，自动轮换避免单 Key 限流。
支持：
- 轮转（round-robin）
- 限流检测后自动切换
- Key 健康状态追踪
- 失败 Key 暂时禁用（冷却期后恢复）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ..logging import get_logger

logger = get_logger("credential_pool")


@dataclass
class KeyStatus:
    """单个 Key 的状态。"""
    key: str
    healthy: bool = True
    last_used: float = 0.0
    last_error: float = 0.0
    error_count: int = 0
    success_count: int = 0
    cooldown_until: float = 0.0  # 冷却到此时间点前不可用

    @property
    def is_available(self) -> bool:
        if not self.healthy:
            return False
        if self.cooldown_until > time.time():
            return False
        return True


class CredentialPool:
    """API Key 轮转池。

    用法:
        pool = CredentialPool(["sk-key1", "sk-key2", "sk-key3"])
        key = pool.get_next()   # 轮转获取可用 key
        pool.report_success(key)
        # 或
        pool.report_error(key, "rate_limit")
    """

    def __init__(
        self,
        keys: list[str],
        cooldown_seconds: float = 60.0,
        max_errors_before_cooldown: int = 3,
    ):
        """
        Args:
            keys: API Key 列表
            cooldown_seconds: 出错后冷却时间
            max_errors_before_cooldown: 连续错误 N 次后进入冷却
        """
        self._statuses = [KeyStatus(key=k) for k in keys if k]
        self._index = 0
        self._cooldown_seconds = cooldown_seconds
        self._max_errors = max_errors_before_cooldown

        if not self._statuses:
            logger.warning("credential_pool_empty")

    def get_next(self) -> Optional[str]:
        """获取下一个可用 Key（轮转）。

        Returns:
            API Key 字符串，无可用则返回 None
        """
        if not self._statuses:
            return None

        n = len(self._statuses)
        for _ in range(n):
            status = self._statuses[self._index % n]
            self._index += 1

            if status.is_available:
                status.last_used = time.time()
                return status.key

        # 所有 Key 都不可用：找冷却时间最短的
        now = time.time()
        earliest = min(self._statuses, key=lambda s: s.cooldown_until)
        if earliest.cooldown_until <= now + 5:
            # 即将恢复，等一下
            earliest.healthy = True
            earliest.cooldown_until = 0
            earliest.error_count = 0
            return earliest.key

        logger.error("all_keys_exhausted", total=n)
        return None

    def report_success(self, key: str):
        """报告 Key 使用成功。"""
        status = self._find(key)
        if status:
            status.success_count += 1
            status.error_count = 0
            status.healthy = True
            status.cooldown_until = 0

    def report_error(self, key: str, error_type: str = ""):
        """报告 Key 使用失败。

        Args:
            key: 出错的 Key
            error_type: 错误类型（"rate_limit" / "auth" / "other"）
        """
        status = self._find(key)
        if not status:
            return

        status.error_count += 1
        status.last_error = time.time()

        # 认证错误：直接禁用（Key 无效）
        if error_type == "auth":
            status.healthy = False
            status.cooldown_until = float("inf")
            logger.warning("key_disabled_auth_error", key_suffix=key[-4:])
            return

        # 限流或其他错误：达到阈值后冷却
        if status.error_count >= self._max_errors:
            status.cooldown_until = time.time() + self._cooldown_seconds
            logger.info(
                "key_cooldown",
                key_suffix=key[-4:],
                cooldown=self._cooldown_seconds,
                errors=status.error_count,
            )

    def get_status(self) -> list[dict]:
        """获取所有 Key 的状态摘要。"""
        return [
            {
                "key_suffix": f"...{s.key[-4:]}" if len(s.key) > 4 else "****",
                "healthy": s.healthy,
                "available": s.is_available,
                "success_count": s.success_count,
                "error_count": s.error_count,
            }
            for s in self._statuses
        ]

    @property
    def available_count(self) -> int:
        return sum(1 for s in self._statuses if s.is_available)

    @property
    def total_count(self) -> int:
        return len(self._statuses)

    def _find(self, key: str) -> Optional[KeyStatus]:
        for s in self._statuses:
            if s.key == key:
                return s
        return None
