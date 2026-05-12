"""Server 模块 - MCP Server + 执行模式管理

- mcp: MCP (Model Context Protocol) server，暴露 Embryo 工具给外部 Agent
- modes: 三种执行模式（tool / plan / supervised）
"""

from .mcp import EmbryoMCPServer
from .modes import ExecutionMode, ModeConfig

__all__ = ["EmbryoMCPServer", "ExecutionMode", "ModeConfig"]
