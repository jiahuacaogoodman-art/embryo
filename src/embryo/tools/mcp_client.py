"""MCP (Model Context Protocol) 客户端

支持连接外部 MCP Server，将其工具集成到 Agent 的工具列表中。
参考 OpenClaw 的 MCP 工具连接设计。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .registry import Tool, ToolRegistry


@dataclass
class MCPServerConfig:
    """MCP Server 配置"""
    name: str
    command: str  # 启动命令（如 "npx -y @modelcontextprotocol/server-filesystem"）
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


class MCPClient:
    """MCP 客户端

    负责：
    1. 启动/连接 MCP Server 进程
    2. 获取 Server 提供的工具列表
    3. 将 MCP 工具注册到 Agent 的 ToolRegistry
    4. 代理工具调用

    当前实现为简化版（通过 stdin/stdout JSON-RPC 通信）。
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._servers: dict[str, MCPServerConfig] = {}
        self._processes: dict[str, subprocess.Popen] = {}

    def add_server(self, config: MCPServerConfig):
        """添加 MCP Server 配置"""
        self._servers[config.name] = config

    def load_config_file(self, config_path: Path):
        """从配置文件加载 MCP Server 列表

        配置文件格式（JSON）：
        {
          "mcpServers": {
            "filesystem": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"]
            }
          }
        }
        """
        if not config_path.exists():
            return

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {})
            for name, server_data in servers.items():
                config = MCPServerConfig(
                    name=name,
                    command=server_data.get("command", ""),
                    args=server_data.get("args", []),
                    env=server_data.get("env", {}),
                    enabled=server_data.get("enabled", True),
                )
                self.add_server(config)
        except Exception as e:
            print(f"[MCP] 加载配置失败: {e}")

    def connect_all(self):
        """连接所有已配置的 MCP Server"""
        for name, config in self._servers.items():
            if config.enabled:
                self._connect_server(name, config)

    def disconnect_all(self):
        """断开所有 MCP Server"""
        for name, proc in self._processes.items():
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        self._processes.clear()

    def _connect_server(self, name: str, config: MCPServerConfig):
        """连接单个 MCP Server

        注意：这是简化实现。完整的 MCP 协议需要 JSON-RPC over stdio。
        这里先注册占位工具，实际调用时通过子进程通信。
        """
        try:
            # 注册一个通用的 MCP 调用工具
            mcp_tool = Tool(
                name=f"mcp_{name}",
                description=f"调用 MCP Server '{name}' 的功能。传入 tool_name 和 arguments。",
                parameters={
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string", "description": f"{name} server 中的工具名"},
                        "arguments": {
                            "type": "object",
                            "description": "工具参数",
                            "additionalProperties": True,
                        },
                    },
                    "required": ["tool_name"],
                },
                handler=lambda tool_name, arguments=None, _name=name: self._call_mcp_tool(
                    _name, tool_name, arguments or {}
                ),
                category="mcp",
            )
            self.registry.register(mcp_tool)
            print(f"[MCP] 已注册 Server: {name}")

        except Exception as e:
            print(f"[MCP] 连接 {name} 失败: {e}")

    def _call_mcp_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """调用 MCP Server 的工具

        简化实现：通过一次性子进程调用。
        完整实现应维护持久连接和 JSON-RPC 通信。
        """
        config = self._servers.get(server_name)
        if not config:
            return f"[Error] MCP Server '{server_name}' 不存在"

        try:
            # 构造调用请求
            request = json.dumps({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
                "id": 1,
            })

            cmd = [config.command] + config.args
            env = {**dict(__import__("os").environ), **config.env}

            result = subprocess.run(
                cmd,
                input=request,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )

            if result.returncode == 0 and result.stdout:
                try:
                    response = json.loads(result.stdout)
                    return str(response.get("result", result.stdout))
                except json.JSONDecodeError:
                    return result.stdout
            else:
                return f"[Error] MCP 调用失败: {result.stderr or 'unknown error'}"

        except subprocess.TimeoutExpired:
            return "[Error] MCP 调用超时"
        except FileNotFoundError:
            return f"[Error] MCP Server 命令未找到: {config.command}"
        except Exception as e:
            return f"[Error] MCP 调用异常: {e}"

    @property
    def connected_servers(self) -> list[str]:
        return list(self._servers.keys())
