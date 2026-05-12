"""Sandbox 模块 - Docker 隔离执行环境

不在用户桌面跑 pyautogui。在 Docker 容器里启动带 VNC 的桌面环境，
通过 VNC/screenshot API 获取容器内截图，通过 API 转发鼠标键盘操作。

核心组件：
- DockerSandbox: 管理容器生命周期（创建/启动/停止/销毁）
- SandboxBackend: 实现 ComputerBackend 接口，操作容器内桌面
- SandboxConfig: 容器配置（镜像、端口、分辨率等）
"""

from .docker_sandbox import DockerSandbox, SandboxConfig, SandboxStatus
from .sandbox_backend import SandboxBackend

__all__ = ["DockerSandbox", "SandboxConfig", "SandboxStatus", "SandboxBackend"]
