"""MCP (Model Context Protocol) 客户端 — 完整实现

实现 MCP 规范的客户端，通过 JSON-RPC 2.0 over stdio 与 Server 通信。

生命周期：
1. 启动 Server 子进程（stdin/stdout pipe）
2. 发送 initialize 请求，协商能力
3. 发送 tools/list 获取工具列表
4. 将每个工具注册到 ToolRegistry
5. 运行期间通过 tools/call 代理调用
6. 关闭时发送 shutdown + 终止进程

参考：
- https://spec.modelcontextprotocol.io/
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..logging import get_logger
from .registry import Tool, ToolRegistry

logger = get_logger("mcp_client")


@dataclass
class MCPServerConfig:
    """MCP Server 配置"""
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout: float = 30.0  # 单次调用超时


@dataclass
class MCPToolSchema:
    """从 Server 发现的工具 schema"""
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


class MCPConnection:
    """单个 MCP Server 的持久连接

    维护子进程和 stdin/stdout 通信管道。
    线程安全：所有 IO 操作加锁。
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._initialized = False
        self.server_info: dict[str, Any] = {}
        self.tools: list[MCPToolSchema] = []

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """启动 Server 进程并完成初始化握手"""
        try:
            cmd = [self.config.command] + self.config.args
            env = {**os.environ, **self.config.env}

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                bufsize=1,  # line buffered
            )

            # 等待进程启动
            time.sleep(0.5)
            if self._process.poll() is not None:
                stderr = self._process.stderr.read() if self._process.stderr else ""
                logger.error("mcp_server_start_failed", server=self.config.name, stderr=stderr[:200])
                return False

            # 发送 initialize
            init_result = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "embryo", "version": "0.2.0"},
            })

            if init_result is None:
                logger.error("mcp_initialize_failed", server=self.config.name)
                self.stop()
                return False

            self.server_info = init_result
            self._initialized = True

            # 发送 initialized 通知
            self._send_notification("notifications/initialized", {})

            # 获取工具列表
            self._discover_tools()

            logger.info(
                "mcp_server_connected",
                server=self.config.name,
                tools_count=len(self.tools),
                server_info=self.server_info.get("serverInfo", {}),
            )
            return True

        except FileNotFoundError:
            logger.error("mcp_command_not_found", server=self.config.name, command=self.config.command)
            return False
        except Exception as e:
            logger.error("mcp_start_error", server=self.config.name, error=str(e))
            return False

    def stop(self):
        """优雅关闭连接"""
        if self._process and self._process.poll() is None:
            try:
                # 发送 shutdown
                self._send_notification("shutdown", {})
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                if self._process.poll() is None:
                    self._process.kill()
        self._process = None
        self._initialized = False

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """调用 Server 上的工具

        Args:
            tool_name: 工具名称
            arguments: 参数字典

        Returns:
            工具执行结果文本
        """
        if not self.is_alive:
            return f"[Error] MCP Server '{self.config.name}' 未运行"

        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })

        if result is None:
            return f"[Error] MCP 工具调用无响应: {tool_name}"

        # 解析 content 数组
        content = result.get("content", [])
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        texts.append(item.get("text", ""))
                    elif item.get("type") == "image":
                        texts.append(f"[image: {item.get('mimeType', 'unknown')}]")
                    else:
                        texts.append(str(item))
                else:
                    texts.append(str(item))
            return "\n".join(texts) if texts else "(empty response)"
        else:
            return str(result)

    def _discover_tools(self):
        """发送 tools/list 获取可用工具"""
        result = self._send_request("tools/list", {})
        if result is None:
            return

        raw_tools = result.get("tools", [])
        self.tools = []
        for t in raw_tools:
            self.tools.append(MCPToolSchema(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {"type": "object", "properties": {}}),
                server_name=self.config.name,
            ))

    def _send_request(self, method: str, params: dict[str, Any]) -> Optional[dict[str, Any]]:
        """发送 JSON-RPC 请求并等待响应"""
        with self._lock:
            if not self.is_alive:
                return None

            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }

            try:
                request_line = json.dumps(request) + "\n"
                self._process.stdin.write(request_line)
                self._process.stdin.flush()

                # 读取响应（带超时）
                # 简化实现：假设一行一个响应
                response_line = self._read_line_timeout(self.config.timeout)
                if response_line is None:
                    return None

                response = json.loads(response_line)

                if "error" in response:
                    error = response["error"]
                    logger.warning(
                        "mcp_rpc_error",
                        server=self.config.name,
                        method=method,
                        error=error,
                    )
                    return None

                return response.get("result", {})

            except (json.JSONDecodeError, BrokenPipeError, OSError) as e:
                logger.error("mcp_io_error", server=self.config.name, method=method, error=str(e))
                return None

    def _send_notification(self, method: str, params: dict[str, Any]):
        """发送 JSON-RPC 通知（无 id，不期待响应）"""
        with self._lock:
            if not self.is_alive:
                return

            notification = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }

            try:
                line = json.dumps(notification) + "\n"
                self._process.stdin.write(line)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def _read_line_timeout(self, timeout: float) -> Optional[str]:
        """带超时的行读取

        使用 threading 实现超时（跨平台兼容）。
        """
        result = [None]

        def _read():
            try:
                line = self._process.stdout.readline()
                if line:
                    result[0] = line.strip()
            except Exception:
                pass

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout=timeout)

        return result[0]


class MCPClient:
    """MCP 客户端管理器

    管理多个 MCP Server 连接，自动发现工具并注册到 ToolRegistry。
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._servers: dict[str, MCPServerConfig] = {}
        self._connections: dict[str, MCPConnection] = {}

    def add_server(self, config: MCPServerConfig):
        """添加 Server 配置"""
        self._servers[config.name] = config

    def load_config_file(self, config_path: Path):
        """从 JSON 配置文件加载 MCP Server 列表

        兼容标准 mcp.json 格式：
        {
          "mcpServers": {
            "filesystem": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home"],
              "env": {"NODE_ENV": "production"},
              "enabled": true
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
                    timeout=server_data.get("timeout", 30.0),
                )
                if config.command:
                    self.add_server(config)
            logger.info("mcp_config_loaded", path=str(config_path), servers=len(servers))
        except Exception as e:
            logger.error("mcp_config_load_error", path=str(config_path), error=str(e))

    def connect_all(self):
        """启动并连接所有已配置的 Server"""
        for name, config in self._servers.items():
            if not config.enabled:
                continue

            conn = MCPConnection(config)
            if conn.start():
                self._connections[name] = conn
                # 注册发现到的工具
                self._register_tools(conn)
            else:
                logger.warning("mcp_connect_skipped", server=name)

    def disconnect_all(self):
        """优雅关闭所有连接"""
        for name, conn in self._connections.items():
            conn.stop()
            logger.info("mcp_server_disconnected", server=name)
        self._connections.clear()

    def reconnect(self, server_name: str) -> bool:
        """重连指定 Server"""
        if server_name in self._connections:
            self._connections[server_name].stop()

        config = self._servers.get(server_name)
        if not config:
            return False

        conn = MCPConnection(config)
        if conn.start():
            self._connections[server_name] = conn
            self._register_tools(conn)
            return True
        return False

    def _register_tools(self, conn: MCPConnection):
        """将 Server 发现的工具注册到 ToolRegistry"""
        for tool_schema in conn.tools:
            # 创建闭包捕获正确的变量
            server_name = conn.config.name
            mcp_tool_name = tool_schema.name

            def make_handler(sn: str, tn: str):
                def handler(**kwargs):
                    c = self._connections.get(sn)
                    if c is None or not c.is_alive:
                        return f"[Error] MCP Server '{sn}' 未连接"
                    return c.call_tool(tn, kwargs)
                return handler

            # 全局唯一名称：mcp_serverName_toolName
            registered_name = f"mcp_{server_name}_{mcp_tool_name}"

            tool = Tool(
                name=registered_name,
                description=f"[MCP:{server_name}] {tool_schema.description}",
                parameters=tool_schema.input_schema,
                handler=make_handler(server_name, mcp_tool_name),
                category="mcp",
            )
            self.registry.register(tool)

        logger.info(
            "mcp_tools_registered",
            server=conn.config.name,
            count=len(conn.tools),
            tools=[t.name for t in conn.tools],
        )

    @property
    def connected_servers(self) -> list[str]:
        return [name for name, conn in self._connections.items() if conn.is_alive]

    @property
    def all_mcp_tools(self) -> list[MCPToolSchema]:
        """获取所有已发现的 MCP 工具"""
        tools = []
        for conn in self._connections.values():
            tools.extend(conn.tools)
        return tools

    def get_status(self) -> dict[str, Any]:
        """获取所有 Server 的状态摘要"""
        status = {}
        for name, config in self._servers.items():
            conn = self._connections.get(name)
            status[name] = {
                "configured": True,
                "enabled": config.enabled,
                "connected": conn is not None and conn.is_alive if conn else False,
                "tools_count": len(conn.tools) if conn else 0,
                "command": config.command,
            }
        return status
