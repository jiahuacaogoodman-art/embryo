"""隔离运行环境模块

在虚拟机/远程桌面/VNC/沙箱中运行GUI任务，隔离对用户主桌面的影响。
"""

from .sandbox_manager import SandboxManager

__all__ = ["SandboxManager"]
