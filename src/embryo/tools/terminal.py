"""终端工具 - 安全的命令执行

设计变更：
- 默认 shell=False + shlex.split（不再 shell=True）
- 通过 CapabilityChecker 白名单校验命令
- 超时控制
- 输出截断

如需恢复旧行为（开发模式），设置 CapabilityConfig.terminal.deny_shell = False
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any, Optional

from ..logging import get_logger
from ..security.capabilities import CapabilityChecker, CapabilityConfig
from .registry import Tool

logger = get_logger(__name__)

# 模块级 capability checker（可被外部替换）
_capability_checker: Optional[CapabilityChecker] = None


def get_capability_checker() -> CapabilityChecker:
    """获取能力检查器实例"""
    global _capability_checker
    if _capability_checker is None:
        _capability_checker = CapabilityChecker()
    return _capability_checker


def set_capability_checker(checker: CapabilityChecker) -> None:
    """替换能力检查器"""
    global _capability_checker
    _capability_checker = checker


def execute_command(command: str, cwd: str = "", timeout: int = 60) -> str:
    """执行命令（安全版）

    流程：
    1. CapabilityChecker 校验命令是否在白名单
    2. 根据 deny_shell 配置选择执行方式
    3. 超时控制
    4. 输出截断

    Args:
        command: 要执行的命令
        cwd: 工作目录（空字符串表示当前目录）
        timeout: 超时时间（秒）

    Returns:
        命令输出（stdout + stderr）
    """
    checker = get_capability_checker()

    # 权限检查
    allowed, reason = checker.check_terminal_command(command)
    if not allowed:
        logger.warning("terminal_command_denied", command=command[:100], reason=reason)
        return f"[Policy] 命令被拒绝: {reason}"

    # 超时限制
    max_timeout = checker.config.terminal.max_timeout_sec
    timeout = min(timeout, max_timeout)

    # 准备命令参数
    try:
        cmd_arg, use_shell = checker.prepare_terminal_command(command)
    except ValueError as e:
        return f"[Policy] {e}"

    try:
        result = subprocess.run(
            cmd_arg,
            shell=use_shell,
            capture_output=True,
            text=True,
            cwd=cwd or None,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}" if output else result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"

        # 输出截断（防止巨量输出）
        max_output = 50000
        if len(output) > max_output:
            output = output[:max_output] + f"\n... [输出被截断，共 {len(output)} 字符]"

        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[Error] 命令超时 ({timeout}s): {command}"
    except FileNotFoundError as e:
        return f"[Error] 命令不存在: {e}"
    except PermissionError as e:
        return f"[Error] 权限不足: {e}"
    except Exception as e:
        return f"[Error] {e}"


TERMINAL_TOOL = Tool(
    name="terminal",
    description="在 shell 中执行命令。用于运行程序、安装依赖、查看系统信息等。受安全策略限制。",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的命令（必须在白名单中）",
            },
            "cwd": {
                "type": "string",
                "description": "工作目录路径（可选）",
                "default": "",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间（秒），默认 60，上限由策略控制",
                "default": 60,
            },
        },
        "required": ["command"],
    },
    handler=execute_command,
    category="terminal",
    requires_confirmation=False,
)
