"""工具系统

工具是 Agent 与环境交互的接口。参考 OpenClaw 的 4 个核心工具 + 扩展体系。

核心工具集：
- terminal: 执行 shell 命令
- file: 文件读写和编辑
- computer_use: GUI 桌面操作（截图/点击/输入/滚动）
- memory: 记忆存储和检索
- web: 网页浏览和搜索

每个工具暴露 OpenAI function calling 格式的 schema，Agent Loop 调用时自动分发。
"""

from .registry import ToolRegistry, Tool

__all__ = ["ToolRegistry", "Tool"]
