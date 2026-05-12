"""安全策略引擎

参考 OpenClaw 的 Policy Engine：
- 工具调用拦截和审批
- 文件路径访问限制
- 危险操作确认
- 命令黑名单

所有工具调用在执行前都经过 PolicyEngine 审查。
"""

from .policy import PolicyEngine, PolicyDecision

__all__ = ["PolicyEngine", "PolicyDecision"]
