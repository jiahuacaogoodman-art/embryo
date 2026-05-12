"""Docker Sandbox - 容器化隔离执行环境

启动一个带 VNC + noVNC 的 Docker 容器，内部运行 Xvfb 虚拟桌面。
Embryo 通过 HTTP API 获取截图、发送鼠标键盘事件。

容器内部服务架构：
┌──────────────────────────────────────┐
│  Docker Container                     │
│                                       │
│  Xvfb (:99)  →  x11vnc  →  noVNC    │
│  ↕                                    │
│  应用进程（浏览器/桌面应用）           │
│  ↕                                    │
│  HTTP Screenshot API (端口 6080)      │
│  HTTP Input API (端口 6081)           │
└──────────────────────────────────────┘

推荐镜像：
- kasmweb/chrome:1.15.0 (浏览器任务)
- kasmweb/ubuntu-jammy-desktop:1.15.0 (桌面任务)
- embryo/sandbox:latest (自定义轻量镜像)

自定义轻量镜像 Dockerfile 见 docker/sandbox/Dockerfile。
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..logging import get_logger

logger = get_logger(__name__)


class SandboxStatus(str, Enum):
    """容器状态"""
    CREATING = "creating"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    DESTROYED = "destroyed"


class SandboxConfig(BaseModel):
    """Docker Sandbox 配置"""
    # 镜像
    image: str = "kasmweb/chrome:1.15.0"
    # 网络
    vnc_port: int = 6901  # noVNC websocket 端口
    api_port: int = 6080  # Screenshot/Input HTTP API 端口
    # 分辨率
    screen_width: int = 1920
    screen_height: int = 1080
    color_depth: int = 24
    # 资源限制
    memory_limit: str = "2g"
    cpu_count: int = 2
    # 超时
    startup_timeout_sec: int = 30
    idle_timeout_sec: int = 300  # 空闲超时自动销毁
    # 安全
    network_enabled: bool = True
    privileged: bool = False
    # 挂载
    shared_dir: Optional[str] = None  # 宿主机共享目录
    # 容器环境变量
    env_vars: dict[str, str] = Field(default_factory=lambda: {
        "VNC_PW": "embryo",
        "VNC_RESOLUTION": "1920x1080",
    })


class DockerSandbox:
    """Docker 容器沙箱管理器

    管理容器生命周期，提供截图和输入 API。

    用法：
        sandbox = DockerSandbox(SandboxConfig(image="kasmweb/chrome:1.15.0"))
        sandbox.start()
        screenshot_bytes = sandbox.screenshot()
        sandbox.mouse_click(500, 300)
        sandbox.type_text("hello")
        sandbox.stop()
    """

    def __init__(self, config: Optional[SandboxConfig] = None):
        self._config = config or SandboxConfig()
        self._container = None
        self._container_id: str = ""
        self._status = SandboxStatus.STOPPED
        self._host_api_port: int = 0
        self._host_vnc_port: int = 0
        self._docker_client = None

    @property
    def status(self) -> SandboxStatus:
        return self._status

    @property
    def container_id(self) -> str:
        return self._container_id

    @property
    def api_base_url(self) -> str:
        """容器内 HTTP API 的宿主机访问地址"""
        return f"http://localhost:{self._host_api_port}"

    @property
    def vnc_url(self) -> str:
        """noVNC 访问地址（浏览器可直接打开）"""
        return f"http://localhost:{self._host_vnc_port}"

    def _get_docker(self):
        """延迟加载 docker SDK"""
        if self._docker_client is None:
            try:
                import docker
                self._docker_client = docker.from_env()
            except ImportError:
                raise ImportError("docker SDK 未安装: pip install docker")
            except Exception as e:
                raise RuntimeError(f"Docker 连接失败: {e}. 确保 Docker daemon 正在运行。")
        return self._docker_client

    def start(self) -> None:
        """启动容器

        1. 拉取镜像（如果本地没有）
        2. 创建并启动容器
        3. 等待服务就绪
        """
        client = self._get_docker()
        self._status = SandboxStatus.CREATING

        logger.info("sandbox_starting", image=self._config.image)

        # 构建容器配置
        ports = {
            f"{self._config.api_port}/tcp": None,  # 随机映射
            f"{self._config.vnc_port}/tcp": None,
        }

        environment = dict(self._config.env_vars)
        environment["VNC_RESOLUTION"] = f"{self._config.screen_width}x{self._config.screen_height}"

        volumes = {}
        if self._config.shared_dir:
            volumes[self._config.shared_dir] = {"bind": "/workspace", "mode": "rw"}

        try:
            self._container = client.containers.run(
                image=self._config.image,
                detach=True,
                ports=ports,
                environment=environment,
                volumes=volumes or None,
                mem_limit=self._config.memory_limit,
                nano_cpus=self._config.cpu_count * 1_000_000_000,
                privileged=self._config.privileged,
                network_mode="bridge" if self._config.network_enabled else "none",
                shm_size="512m",  # 浏览器需要 shared memory
                remove=True,  # 停止后自动删除
            )
            self._container_id = self._container.id[:12]
        except Exception as e:
            self._status = SandboxStatus.ERROR
            raise RuntimeError(f"容器启动失败: {e}")

        # 获取映射后的端口
        self._container.reload()
        port_bindings = self._container.attrs["NetworkSettings"]["Ports"]

        api_binding = port_bindings.get(f"{self._config.api_port}/tcp")
        vnc_binding = port_bindings.get(f"{self._config.vnc_port}/tcp")

        if api_binding:
            self._host_api_port = int(api_binding[0]["HostPort"])
        if vnc_binding:
            self._host_vnc_port = int(vnc_binding[0]["HostPort"])

        # 等待服务就绪
        self._wait_ready()
        self._status = SandboxStatus.RUNNING

        logger.info(
            "sandbox_started",
            container_id=self._container_id,
            api_port=self._host_api_port,
            vnc_port=self._host_vnc_port,
        )

    def stop(self) -> None:
        """停止并销毁容器"""
        if self._container:
            try:
                self._container.stop(timeout=5)
            except Exception as e:
                logger.warning("sandbox_stop_error", error=str(e))
                try:
                    self._container.kill()
                except Exception:
                    pass

        self._status = SandboxStatus.DESTROYED
        self._container = None
        logger.info("sandbox_stopped", container_id=self._container_id)

    def _wait_ready(self) -> None:
        """等待容器内服务就绪"""
        import urllib.request
        import urllib.error

        deadline = time.time() + self._config.startup_timeout_sec
        url = f"http://localhost:{self._host_api_port}/"

        while time.time() < deadline:
            try:
                urllib.request.urlopen(url, timeout=2)
                return
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)

        raise TimeoutError(
            f"容器服务未就绪（{self._config.startup_timeout_sec}s 超时）. "
            f"端口 {self._host_api_port} 无响应。"
        )

    # ============================================================
    # 截图 API
    # ============================================================

    def screenshot(self) -> bytes:
        """获取容器内桌面截图（PNG bytes）

        通过 VNC 或 HTTP API 获取。
        """
        if self._status != SandboxStatus.RUNNING:
            raise RuntimeError(f"容器未运行: {self._status.value}")

        # 方式 1: 通过 docker exec + scrot/import 命令截图
        return self._screenshot_via_exec()

    def _screenshot_via_exec(self) -> bytes:
        """通过 docker exec 在容器内截图"""
        if not self._container:
            raise RuntimeError("容器不存在")

        # 在容器内执行截图命令
        # 大多数 kasm 镜像有 xdotool / import
        result = self._container.exec_run(
            cmd=["bash", "-c", "import -window root -quality 85 png:- 2>/dev/null || "
                 "scrot -o /tmp/screen.png && cat /tmp/screen.png 2>/dev/null || "
                 "xwd -root -silent | convert xwd:- png:-"],
            environment={"DISPLAY": ":1"},
        )

        if result.exit_code == 0 and result.output:
            return result.output

        # 备选：用 python 在容器内截图
        result = self._container.exec_run(
            cmd=["python3", "-c",
                 "import subprocess; "
                 "subprocess.run(['apt-get','install','-y','scrot'], capture_output=True); "
                 "import os; os.environ['DISPLAY']=':1'; "
                 "subprocess.run(['scrot','/tmp/s.png']); "
                 "import sys; sys.stdout.buffer.write(open('/tmp/s.png','rb').read())"],
        )

        if result.exit_code == 0 and result.output:
            return result.output

        raise RuntimeError("容器内截图失败。镜像可能缺少截图工具。")

    def screenshot_to_file(self, output_path: str | Path) -> str:
        """截图并保存到文件

        Returns:
            保存路径
        """
        png_bytes = self.screenshot()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes)
        return str(path)

    # ============================================================
    # 输入 API
    # ============================================================

    def mouse_click(self, x: int, y: int, button: int = 1, clicks: int = 1) -> None:
        """在容器内点击

        Args:
            x, y: 坐标
            button: 1=左键, 2=中键, 3=右键
            clicks: 点击次数
        """
        if not self._container:
            raise RuntimeError("容器不存在")

        cmd = f"xdotool mousemove {x} {y} && xdotool click --repeat {clicks} {button}"
        result = self._container.exec_run(
            cmd=["bash", "-c", cmd],
            environment={"DISPLAY": ":1"},
        )
        if result.exit_code != 0:
            logger.warning("sandbox_click_failed", x=x, y=y, output=result.output.decode()[:100])

    def mouse_move(self, x: int, y: int) -> None:
        """移动鼠标"""
        if not self._container:
            raise RuntimeError("容器不存在")

        self._container.exec_run(
            cmd=["xdotool", "mousemove", str(x), str(y)],
            environment={"DISPLAY": ":1"},
        )

    def type_text(self, text: str, delay_ms: int = 12) -> None:
        """输入文字

        Args:
            text: 要输入的文字
            delay_ms: 按键间隔（毫秒）
        """
        if not self._container:
            raise RuntimeError("容器不存在")

        # xdotool type 对 ASCII 有效
        # 中文/特殊字符用 xdotool key + xclip 粘贴
        if all(ord(c) < 128 for c in text):
            self._container.exec_run(
                cmd=["xdotool", "type", "--delay", str(delay_ms), text],
                environment={"DISPLAY": ":1"},
            )
        else:
            # 通过剪贴板粘贴
            self._container.exec_run(
                cmd=["bash", "-c", f"echo -n '{text}' | xclip -selection clipboard && "
                     "xdotool key ctrl+v"],
                environment={"DISPLAY": ":1"},
            )

    def key_press(self, key: str) -> None:
        """按键

        Args:
            key: 按键名（xdotool 格式：Return, Tab, Escape, BackSpace, etc）
        """
        if not self._container:
            raise RuntimeError("容器不存在")

        self._container.exec_run(
            cmd=["xdotool", "key", key],
            environment={"DISPLAY": ":1"},
        )

    def hotkey(self, keys: list[str]) -> None:
        """快捷键

        Args:
            keys: 按键列表，如 ["ctrl", "a"] → "ctrl+a"
        """
        if not self._container:
            raise RuntimeError("容器不存在")

        # xdotool 格式：ctrl+a, super+l, alt+F4
        combo = "+".join(keys)
        self._container.exec_run(
            cmd=["xdotool", "key", combo],
            environment={"DISPLAY": ":1"},
        )

    def scroll(self, direction: str = "down", amount: int = 3, x: int = 0, y: int = 0) -> None:
        """滚动

        Args:
            direction: up/down
            amount: 滚动量
            x, y: 滚动位置（0=当前位置）
        """
        if not self._container:
            raise RuntimeError("容器不存在")

        cmd_parts = []
        if x and y:
            cmd_parts.append(f"xdotool mousemove {x} {y}")

        # xdotool: button 4=scroll up, 5=scroll down
        button = "4" if direction == "up" else "5"
        cmd_parts.append(f"xdotool click --repeat {amount} {button}")

        self._container.exec_run(
            cmd=["bash", "-c", " && ".join(cmd_parts)],
            environment={"DISPLAY": ":1"},
        )

    def run_command(self, command: str, timeout: int = 30) -> tuple[int, str]:
        """在容器内执行命令

        Args:
            command: shell 命令

        Returns:
            (exit_code, output)
        """
        if not self._container:
            raise RuntimeError("容器不存在")

        result = self._container.exec_run(
            cmd=["bash", "-c", command],
            environment={"DISPLAY": ":1"},
        )
        return result.exit_code, result.output.decode("utf-8", errors="replace")

    def open_url(self, url: str) -> None:
        """在容器内打开 URL（使用默认浏览器）"""
        self.run_command(f"xdg-open '{url}' &")
        time.sleep(2)  # 等浏览器启动

    # ============================================================
    # 生命周期
    # ============================================================

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    def is_running(self) -> bool:
        """检查容器是否还在运行"""
        if not self._container:
            return False
        try:
            self._container.reload()
            return self._container.status == "running"
        except Exception:
            return False

    def get_info(self) -> dict[str, Any]:
        """获取容器信息"""
        return {
            "container_id": self._container_id,
            "status": self._status.value,
            "image": self._config.image,
            "api_url": self.api_base_url,
            "vnc_url": self.vnc_url,
            "resolution": f"{self._config.screen_width}x{self._config.screen_height}",
        }
