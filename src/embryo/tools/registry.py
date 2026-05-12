"""工具注册表

管理所有可用工具，提供 schema 导出和调用分发。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime.session import Session


@dataclass
class Tool:
    """工具定义"""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema format
    handler: Callable[..., Any] = field(repr=False)
    category: str = "general"  # terminal / file / computer_use / memory / web
    requires_confirmation: bool = False  # 高风险工具需要确认

    def get_openai_schema(self) -> dict[str, Any]:
        """导出为 OpenAI function calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表

    集中管理所有工具的注册、查询和调用。
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        """注册一个工具"""
        self._tools[tool.name] = tool

    def register_function(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable,
        category: str = "general",
        requires_confirmation: bool = False,
    ):
        """便捷注册方法"""
        tool = Tool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            category=category,
            requires_confirmation=requires_confirmation,
        )
        self.register(tool)

    def execute(self, name: str, arguments: dict[str, Any], session: Optional["Session"] = None) -> Any:
        """执行指定工具

        Args:
            name: 工具名称
            arguments: 参数字典
            session: 当前会话（部分工具需要）

        Returns:
            工具执行结果

        Raises:
            KeyError: 工具不存在
            Exception: 执行失败
        """
        if name not in self._tools:
            raise KeyError(f"未知工具: {name}。可用工具: {list(self._tools.keys())}")

        tool = self._tools[name]
        handler = tool.handler

        # 检查 handler 是否接受 session 参数
        sig = inspect.signature(handler)
        if "session" in sig.parameters:
            return handler(session=session, **arguments)
        else:
            return handler(**arguments)

    def get_openai_tools_schema(self) -> list[dict[str, Any]]:
        """导出所有工具的 OpenAI schema 列表"""
        return [tool.get_openai_schema() for tool in self._tools.values()]

    def get_tool(self, name: str) -> Optional[Tool]:
        """获取指定工具"""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """列出所有工具名称"""
        return list(self._tools.keys())

    def list_by_category(self, category: str) -> list[Tool]:
        """按类别列出工具"""
        return [t for t in self._tools.values() if t.category == category]

    @property
    def count(self) -> int:
        return len(self._tools)
