"""终端工具 - 执行 Shell 命令

参考 OpenClaw 的 Bash 工具：能执行任意 shell 命令并返回输出。
"""

from __future__ import annotations

import subprocess
from typing import Any

from .registry import Tool


def execute_command(command: str, cwd: str = "", timeout: int = 60) -> str:
    """执行 shell 命令

    Args:
        command: 要执行的命令
        cwd: 工作目录（空字符串表示当前目录）
        timeout: 超时时间（秒）

    Returns:
        命令输出（stdout + stderr）
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
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
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[Error] 命令超时 ({timeout}s): {command}"
    except Exception as e:
        return f"[Error] {e}"


TERMINAL_TOOL = Tool(
    name="terminal",
    description="在 shell 中执行命令。用于运行程序、安装依赖、查看系统信息等。",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
            "cwd": {
                "type": "string",
                "description": "工作目录路径（可选）",
                "default": "",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间（秒），默认 60",
                "default": 60,
            },
        },
        "required": ["command"],
    },
    handler=execute_command,
    category="terminal",
)
