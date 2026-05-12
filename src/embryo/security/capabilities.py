"""Capability-based Security Model

从"黑名单"改为"能力授权"：明确定义 Agent 被授权做什么。
未被授权的能力默认拒绝。

配置示例 (YAML):
    permissions:
      file:
        read: ["./workspace"]
        write: ["./workspace"]
      terminal:
        allowed_commands: ["python", "pip", "pytest", "ls", "cat"]
        deny_shell: true
      gui:
        allow_click: true
        allow_type: true
        require_confirm_for: ["submit", "payment", "delete", "send"]
      network:
        allowed_hosts: ["localhost"]
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..logging import get_logger

logger = get_logger(__name__)


class FilePermissions(BaseModel):
    """文件系统权限"""

    read_paths: list[str] = Field(default_factory=lambda: ["./workspace"])
    write_paths: list[str] = Field(default_factory=lambda: ["./workspace"])
    denied_paths: list[str] = Field(
        default_factory=lambda: [
            "/proc/sysrq-trigger",
            "/dev/sda",
            "/dev/nvme",
            "/etc/shadow",
            "/etc/passwd",
        ]
    )


class TerminalPermissions(BaseModel):
    """终端命令权限"""

    allowed_commands: list[str] = Field(
        default_factory=lambda: [
            "python",
            "python3",
            "pip",
            "pip3",
            "pytest",
            "ls",
            "cat",
            "head",
            "tail",
            "grep",
            "find",
            "wc",
            "echo",
            "mkdir",
            "cp",
            "mv",
            "touch",
            "git",
            "npm",
            "node",
            "curl",
            "wget",
        ]
    )
    deny_shell: bool = True  # True = 使用 shlex.split + shell=False
    max_timeout_sec: int = 120
    allow_sudo: bool = False


class GUIPermissions(BaseModel):
    """GUI 操作权限"""

    allow_click: bool = True
    allow_type: bool = True
    allow_hotkey: bool = True
    allow_scroll: bool = True
    allow_screenshot: bool = True
    # 这些操作执行前需要用户确认
    require_confirm_for: list[str] = Field(
        default_factory=lambda: [
            "submit",
            "payment",
            "delete",
            "send",
            "confirm",
            "purchase",
            "transfer",
        ]
    )


class NetworkPermissions(BaseModel):
    """网络权限"""

    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    allowed_ports: list[int] = Field(default_factory=lambda: [8642])
    allow_external: bool = False


class CapabilityConfig(BaseModel):
    """完整的能力配置"""

    file: FilePermissions = Field(default_factory=FilePermissions)
    terminal: TerminalPermissions = Field(default_factory=TerminalPermissions)
    gui: GUIPermissions = Field(default_factory=GUIPermissions)
    network: NetworkPermissions = Field(default_factory=NetworkPermissions)

    @classmethod
    def permissive(cls) -> "CapabilityConfig":
        """宽松模式（开发/测试用）"""
        return cls(
            file=FilePermissions(read_paths=["/"], write_paths=["./workspace", "/tmp"]),
            terminal=TerminalPermissions(deny_shell=False, allow_sudo=False),
            gui=GUIPermissions(require_confirm_for=[]),
            network=NetworkPermissions(allow_external=True),
        )

    @classmethod
    def strict(cls) -> "CapabilityConfig":
        """严格模式（生产环境）"""
        return cls(
            terminal=TerminalPermissions(
                allowed_commands=["python", "python3", "pip", "pytest", "ls", "cat"],
                deny_shell=True,
                allow_sudo=False,
            ),
            gui=GUIPermissions(
                require_confirm_for=[
                    "submit", "payment", "delete", "send",
                    "confirm", "purchase", "transfer", "login",
                ]
            ),
            network=NetworkPermissions(
                allowed_hosts=["localhost", "127.0.0.1"],
                allow_external=False,
            ),
        )


class CapabilityChecker:
    """能力检查器

    基于 CapabilityConfig 判断某个操作是否被授权。
    """

    def __init__(self, config: Optional[CapabilityConfig] = None):
        self._config = config or CapabilityConfig()

    @property
    def config(self) -> CapabilityConfig:
        return self._config

    def check_file_read(self, path: str) -> tuple[bool, str]:
        """检查文件读取权限"""
        return self._check_path(path, self._config.file.read_paths, "读取")

    def check_file_write(self, path: str) -> tuple[bool, str]:
        """检查文件写入权限"""
        return self._check_path(path, self._config.file.write_paths, "写入")

    def check_terminal_command(self, command: str) -> tuple[bool, str]:
        """检查终端命令是否允许

        Returns:
            (allowed, reason)
        """
        if not command.strip():
            return False, "空命令"

        # 解析命令获取可执行文件名
        try:
            parts = shlex.split(command)
        except ValueError:
            return False, f"命令解析失败: {command}"

        if not parts:
            return False, "空命令"

        executable = Path(parts[0]).name  # 只取文件名部分

        # sudo 检查
        if executable == "sudo":
            if not self._config.terminal.allow_sudo:
                return False, "sudo 未被授权"
            # 检查 sudo 后面的实际命令
            if len(parts) > 1:
                executable = Path(parts[1]).name

        # 白名单检查
        if executable not in self._config.terminal.allowed_commands:
            return False, f"命令 '{executable}' 不在允许列表中"

        return True, ""

    def check_gui_action(self, action: str, context: str = "") -> tuple[bool, str]:
        """检查 GUI 操作权限

        Args:
            action: 操作类型 (click/type/hotkey/scroll/screenshot)
            context: 操作上下文描述（用于判断是否需要确认）

        Returns:
            (allowed, reason) — 如果需要确认，返回 (False, "需要确认: ...")
        """
        gui = self._config.gui

        # 基本权限检查
        permission_map = {
            "click": gui.allow_click,
            "type_text": gui.allow_type,
            "type": gui.allow_type,
            "hotkey": gui.allow_hotkey,
            "scroll": gui.allow_scroll,
            "screenshot": gui.allow_screenshot,
            "observe": gui.allow_screenshot,
        }

        if action in permission_map and not permission_map[action]:
            return False, f"GUI 操作 '{action}' 未被授权"

        # 检查是否需要确认
        if context and gui.require_confirm_for:
            context_lower = context.lower()
            for keyword in gui.require_confirm_for:
                if keyword.lower() in context_lower:
                    return False, f"需要确认: 操作包含敏感关键词 '{keyword}'"

        return True, ""

    def prepare_terminal_command(self, command: str) -> tuple[list[str] | str, bool]:
        """准备终端命令执行参数

        根据 deny_shell 配置决定是用 shell=True 还是 shlex.split + shell=False

        Returns:
            (command_arg, shell_flag) — 传给 subprocess.run 的参数
        """
        if self._config.terminal.deny_shell:
            try:
                parts = shlex.split(command)
                return parts, False
            except ValueError:
                # 如果 shlex 无法解析（包含管道等），拒绝
                raise ValueError(
                    f"命令包含 shell 特殊语法，deny_shell=True 时不允许: {command}"
                )
        else:
            return command, True

    def _check_path(self, path: str, allowed: list[str], op: str) -> tuple[bool, str]:
        """通用路径权限检查"""
        try:
            resolved = str(Path(path).resolve())
        except Exception:
            return False, f"无效路径: {path}"

        # 禁止路径
        for denied in self._config.file.denied_paths:
            if resolved.startswith(denied):
                return False, f"路径被禁止{op}: {resolved}"

        # 白名单检查
        for allowed_path in allowed:
            allowed_resolved = str(Path(allowed_path).resolve())
            if resolved.startswith(allowed_resolved):
                return True, ""

        return False, f"路径不在{op}允许列表中: {resolved}"
