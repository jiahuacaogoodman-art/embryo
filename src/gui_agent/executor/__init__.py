"""动作执行模块

将AI生成的JSON指令转化为真实GUI操作（点击/输入/滚动/快捷键等）。
"""

from .action_executor import ActionExecutor

__all__ = ["ActionExecutor"]
