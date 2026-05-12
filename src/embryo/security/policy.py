"""安全策略引擎 — 工具调用的拦截、审查和限制

参考 OpenClaw 的 Policy Engine 设计：
- 所有工具调用执行前经过策略检查
- 基于规则的白名单/黑名单
- 路径访问控制
- 危险命令识别
- 可配置的确认模式（auto/ask/deny）

策略粒度：
- 全局策略（应用于所有工具）
- 工具级策略（仅某个工具）
- 参数级策略（检查特定参数值）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from ..logging import get_logger

logger = get_logger("policy")


class PolicyDecision(str, Enum):
    """策略决定"""
    ALLOW = "allow"          # 允许执行
    DENY = "deny"            # 拒绝执行
    ASK = "ask"              # 需要用户确认
    WARN = "warn"            # 允许但记录警告


@dataclass
class PolicyRule:
    """单条策略规则"""
    name: str
    description: str = ""
    tools: list[str] = field(default_factory=list)  # 适用的工具（空=所有）
    condition: str = ""  # 条件表达式（简化版）
    decision: PolicyDecision = PolicyDecision.DENY
    priority: int = 0  # 越高优先级越大


@dataclass
class PolicyCheckResult:
    """策略检查结果"""
    decision: PolicyDecision
    rule_name: str = ""
    reason: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)


class PolicyEngine:
    """安全策略引擎

    所有工具调用在执行前经过此引擎审查。
    """

    def __init__(
        self,
        allowed_paths: Optional[list[str]] = None,
        denied_commands: Optional[list[str]] = None,
        require_confirmation_tools: Optional[list[str]] = None,
        auto_allow_tools: Optional[list[str]] = None,
    ):
        """初始化策略引擎

        Args:
            allowed_paths: 允许访问的路径前缀列表（None=不限制）
            denied_commands: 禁止执行的命令模式列表
            require_confirmation_tools: 需要用户确认的工具名列表
            auto_allow_tools: 自动放行的工具名列表
        """
        self.allowed_paths = allowed_paths
        self.denied_commands = denied_commands or DEFAULT_DENIED_COMMANDS
        self.require_confirmation_tools = require_confirmation_tools or []
        self.auto_allow_tools = auto_allow_tools or DEFAULT_SAFE_TOOLS
        self._rules: list[PolicyRule] = []
        self._confirmation_callback: Optional[Any] = None

        # 构建内置规则
        self._build_default_rules()

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PolicyCheckResult:
        """检查工具调用是否允许

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            策略检查结果
        """
        # 1. 自动放行的安全工具
        if tool_name in self.auto_allow_tools:
            return PolicyCheckResult(
                decision=PolicyDecision.ALLOW,
                tool_name=tool_name,
                arguments=arguments,
            )

        # 2. 需要确认的工具
        if tool_name in self.require_confirmation_tools:
            return PolicyCheckResult(
                decision=PolicyDecision.ASK,
                rule_name="require_confirmation",
                reason=f"工具 '{tool_name}' 被配置为需要用户确认",
                tool_name=tool_name,
                arguments=arguments,
            )

        # 3. 检查具体规则
        # 终端命令检查
        if tool_name == "terminal":
            return self._check_terminal_command(arguments)

        # 文件操作路径检查
        if tool_name in ("read_file", "write_file", "edit_file"):
            return self._check_file_path(tool_name, arguments)

        # GUI 操作 — 默认允许但标记警告
        if tool_name in ("click", "type_text", "hotkey", "press_key"):
            return PolicyCheckResult(
                decision=PolicyDecision.WARN,
                rule_name="gui_operation",
                reason=f"GUI 操作: {tool_name}",
                tool_name=tool_name,
                arguments=arguments,
            )

        # 默认允许
        return PolicyCheckResult(
            decision=PolicyDecision.ALLOW,
            tool_name=tool_name,
            arguments=arguments,
        )

    def _check_terminal_command(self, arguments: dict[str, Any]) -> PolicyCheckResult:
        """检查终端命令安全性"""
        command = arguments.get("command", "")
        if not command:
            return PolicyCheckResult(decision=PolicyDecision.ALLOW)

        # 检查黑名单
        for pattern in self.denied_commands:
            if re.search(pattern, command, re.IGNORECASE):
                logger.warning("policy_denied_command", command=command, pattern=pattern)
                return PolicyCheckResult(
                    decision=PolicyDecision.DENY,
                    rule_name="denied_command",
                    reason=f"命令被安全策略拒绝: 匹配模式 '{pattern}'",
                    tool_name="terminal",
                    arguments=arguments,
                )

        # 检查高危命令（需确认）
        for pattern in DANGEROUS_COMMANDS:
            if re.search(pattern, command, re.IGNORECASE):
                return PolicyCheckResult(
                    decision=PolicyDecision.ASK,
                    rule_name="dangerous_command",
                    reason=f"检测到高危命令: {command[:100]}",
                    tool_name="terminal",
                    arguments=arguments,
                )

        return PolicyCheckResult(decision=PolicyDecision.ALLOW, tool_name="terminal")

    def _check_file_path(self, tool_name: str, arguments: dict[str, Any]) -> PolicyCheckResult:
        """检查文件路径是否在允许范围内"""
        path_str = arguments.get("path", "")
        if not path_str:
            return PolicyCheckResult(decision=PolicyDecision.ALLOW)

        # 路径规范化
        try:
            path = Path(path_str).resolve()
        except Exception:
            return PolicyCheckResult(
                decision=PolicyDecision.DENY,
                rule_name="invalid_path",
                reason=f"无效路径: {path_str}",
                tool_name=tool_name,
                arguments=arguments,
            )

        # 禁止访问的路径
        for denied in DENIED_PATHS:
            if str(path).startswith(denied):
                return PolicyCheckResult(
                    decision=PolicyDecision.DENY,
                    rule_name="denied_path",
                    reason=f"路径被安全策略禁止: {path}",
                    tool_name=tool_name,
                    arguments=arguments,
                )

        # 如果配置了 allowed_paths，检查是否在范围内
        if self.allowed_paths is not None:
            allowed = any(str(path).startswith(p) for p in self.allowed_paths)
            if not allowed:
                return PolicyCheckResult(
                    decision=PolicyDecision.DENY,
                    rule_name="path_not_allowed",
                    reason=f"路径不在允许列表中: {path}",
                    tool_name=tool_name,
                    arguments=arguments,
                )

        # 写操作对系统文件需要确认
        if tool_name in ("write_file", "edit_file"):
            system_dirs = ["/etc", "/usr", "/bin", "/sbin", "/boot"]
            if any(str(path).startswith(d) for d in system_dirs):
                return PolicyCheckResult(
                    decision=PolicyDecision.ASK,
                    rule_name="system_file_write",
                    reason=f"正在尝试写入系统目录: {path}",
                    tool_name=tool_name,
                    arguments=arguments,
                )

        return PolicyCheckResult(decision=PolicyDecision.ALLOW, tool_name=tool_name)

    def _build_default_rules(self):
        """构建默认安全规则"""
        pass  # 当前通过硬编码逻辑实现，未来可扩展为规则文件

    def add_rule(self, rule: PolicyRule):
        """添加自定义规则"""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def set_confirmation_callback(self, callback):
        """设置用户确认回调

        callback 签名: (tool_name: str, arguments: dict, reason: str) -> bool
        """
        self._confirmation_callback = callback

    def request_confirmation(self, result: PolicyCheckResult) -> bool:
        """请求用户确认

        Returns:
            True = 允许, False = 拒绝
        """
        if self._confirmation_callback:
            return self._confirmation_callback(
                result.tool_name,
                result.arguments,
                result.reason,
            )
        # 无回调时默认拒绝
        return False


# ===== 常量 =====

# 自动放行的安全工具（只读或低风险）
DEFAULT_SAFE_TOOLS = [
    "read_file",
    "list_directory",
    "recall",
    "screenshot",
    "ocr_screen",
    "find_text_on_screen",
    "scroll",
    "load_skill",
]

# 禁止执行的命令模式
DEFAULT_DENIED_COMMANDS = [
    r"\brm\s+-rf\s+/\s*$",         # rm -rf /
    r"\bmkfs\b",                     # 格式化
    r"\bdd\s+if=.*of=/dev/",        # dd 覆写设备
    r":(){ :\|:& };:",              # fork bomb
    r"\bshutdown\b",                 # 关机
    r"\breboot\b",                   # 重启
    r"\binit\s+0\b",                # 关机
    r">\s*/dev/sd[a-z]",            # 覆写磁盘
    r"\bchmod\s+777\s+/",           # 全局权限
    r"\bcurl.*\|\s*(ba)?sh",        # 管道执行远程脚本
    r"\bwget.*\|\s*(ba)?sh",        # 管道执行远程脚本
]

# 高危命令（需确认但不禁止）
DANGEROUS_COMMANDS = [
    r"\brm\s+-rf\b",               # 递归删除
    r"\bgit\s+push\s+.*--force",   # 强制推送
    r"\bgit\s+reset\s+--hard",     # 硬重置
    r"\bdrop\s+database\b",        # 删库
    r"\bdrop\s+table\b",           # 删表
    r"\btruncate\b",               # 清空表
    r"\bsudo\b",                   # 提权
    r"\bchown\s+-R\b",             # 递归改所有者
    r"\bsystemctl\s+(stop|disable|restart)\b",  # 服务操作
    r"\bkill\s+-9\b",             # 强杀进程
    r"\bpip\s+install\b.*--break-system",  # 破坏系统 pip
]

# 绝对禁止访问的路径
DENIED_PATHS = [
    "/proc/sysrq-trigger",
    "/dev/sda",
    "/dev/nvme",
]
