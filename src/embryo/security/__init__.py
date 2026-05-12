"""安全模块

- policy: 旧版黑名单策略引擎（保留兼容）
- capabilities: 新版能力授权模型（白名单优先）
- auth: API 鉴权（token + rate limit + audit log）
- prompt_guard: 提示注入防护

安全架构：
- CapabilityChecker: 判断操作是否被授权
- TokenValidator: 验证 API 请求
- AuditLog: 审计日志
- PolicyEngine: 旧版兼容（逐步迁移到 capabilities）
"""

from .policy import PolicyEngine, PolicyDecision
from .capabilities import (
    CapabilityChecker,
    CapabilityConfig,
    FilePermissions,
    TerminalPermissions,
    GUIPermissions,
    NetworkPermissions,
)
from .auth import AuthConfig, TokenValidator, RateLimiter, AuditLog

__all__ = [
    # 旧版
    "PolicyEngine",
    "PolicyDecision",
    # 新版能力模型
    "CapabilityChecker",
    "CapabilityConfig",
    "FilePermissions",
    "TerminalPermissions",
    "GUIPermissions",
    "NetworkPermissions",
    # 鉴权
    "AuthConfig",
    "TokenValidator",
    "RateLimiter",
    "AuditLog",
]
