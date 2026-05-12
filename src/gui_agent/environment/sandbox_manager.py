"""隔离运行环境模块

在虚拟机/远程桌面/VNC/沙箱中运行GUI任务，
避免智能体操作抢占用户鼠标、键盘和窗口焦点。

支持的环境类型：
- VNC: 通过VNC连接远程桌面
- Xvfb: Linux虚拟帧缓冲（无头模式）
- RDP: Windows远程桌面
- Local: 本地桌面（调试用，不隔离）
"""

from __future__ import annotations

import subprocess
import time
from enum import Enum
from typing import Optional

from loguru import logger

from ..config import EnvironmentConfig


class EnvironmentType(str, Enum):
    """运行环境类型"""
    VNC = "vnc"
    XVFB = "xvfb"
    RDP = "rdp"
    LOCAL = "local"


class SandboxManager:
    """沙箱环境管理器

    负责创建、管理和销毁隔离的GUI运行环境。
    确保AI操作不会影响用户的正常桌面使用。
    """

    def __init__(self, config: EnvironmentConfig):
        self.config = config
        self._env_type = EnvironmentType(config.type)
        self._is_running: bool = False
        self._process: Optional[subprocess.Popen] = None
        self._display: Optional[str] = None

    def start(self) -> bool:
        """启动隔离环境

        Returns:
            是否成功启动
        """
        logger.info(f"启动隔离环境: 类型={self._env_type.value}")

        if self._is_running:
            logger.warning("环境已在运行中")
            return True

        try:
            handler = {
                EnvironmentType.VNC: self._start_vnc,
                EnvironmentType.XVFB: self._start_xvfb,
                EnvironmentType.RDP: self._start_rdp,
                EnvironmentType.LOCAL: self._start_local,
            }[self._env_type]

            success = handler()
            if success:
                self._is_running = True
                logger.info(f"隔离环境启动成功: {self._env_type.value}")
            return success

        except Exception as e:
            logger.error(f"隔离环境启动失败: {e}")
            return False

    def stop(self):
        """停止并清理隔离环境"""
        logger.info("停止隔离环境")

        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

        self._is_running = False
        self._display = None

    def is_ready(self) -> bool:
        """检查环境是否就绪"""
        if not self._is_running:
            return False

        if self._env_type == EnvironmentType.LOCAL:
            return True

        if self._env_type == EnvironmentType.VNC:
            return self._check_vnc_connection()

        if self._env_type == EnvironmentType.XVFB:
            return self._check_xvfb_running()

        return self._is_running

    def get_display(self) -> Optional[str]:
        """获取显示器标识（用于设置DISPLAY环境变量）

        Returns:
            显示器标识，如 ":99"
        """
        return self._display

    def get_connection_info(self) -> dict:
        """获取环境连接信息"""
        return {
            "type": self._env_type.value,
            "host": self.config.host,
            "port": self.config.port,
            "display": self._display,
            "screen_size": f"{self.config.screen_width}x{self.config.screen_height}",
            "is_running": self._is_running,
        }

    def take_screenshot_via_env(self) -> Optional[str]:
        """通过环境接口截取屏幕（用于VNC等远程场景）

        Returns:
            截图保存路径
        """
        if self._env_type == EnvironmentType.VNC:
            return self._vnc_screenshot()
        elif self._env_type == EnvironmentType.XVFB:
            return self._xvfb_screenshot()
        return None

    # ========== VNC 环境 ==========

    def _start_vnc(self) -> bool:
        """启动VNC服务器或连接到现有VNC"""
        try:
            # 尝试启动 TigerVNC 或 x11vnc
            display_num = 99
            self._display = f":{display_num}"

            # 启动 Xvfb + VNC
            xvfb_cmd = [
                "Xvfb", self._display,
                "-screen", "0",
                f"{self.config.screen_width}x{self.config.screen_height}x24",
            ]

            self._process = subprocess.Popen(
                xvfb_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)

            # 启动VNC服务
            vnc_cmd = [
                "x11vnc",
                "-display", self._display,
                "-forever",
                "-nopw" if not self.config.password else "-passwd", self.config.password or "",
                "-rfbport", str(self.config.port),
            ]

            subprocess.Popen(
                vnc_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)

            logger.info(f"VNC环境已启动: display={self._display}, port={self.config.port}")
            return True

        except FileNotFoundError as e:
            logger.warning(f"VNC工具未安装: {e}")
            logger.info("回退到Xvfb模式")
            return self._start_xvfb()
        except Exception as e:
            logger.error(f"VNC启动失败: {e}")
            return False

    def _check_vnc_connection(self) -> bool:
        """检查VNC连接是否正常"""
        try:
            result = subprocess.run(
                ["xdpyinfo", "-display", self._display or ":99"],
                capture_output=True, timeout=3
            )
            return result.returncode == 0
        except Exception:
            return self._process is not None and self._process.poll() is None

    def _vnc_screenshot(self) -> Optional[str]:
        """通过VNC截图"""
        try:
            output_path = "/tmp/vnc_screenshot.png"
            subprocess.run(
                ["import", "-window", "root", "-display", self._display or ":99", output_path],
                capture_output=True, timeout=5
            )
            return output_path
        except Exception as e:
            logger.error(f"VNC截图失败: {e}")
            return None

    # ========== Xvfb 环境 ==========

    def _start_xvfb(self) -> bool:
        """启动Xvfb虚拟帧缓冲"""
        try:
            display_num = 99
            self._display = f":{display_num}"

            cmd = [
                "Xvfb", self._display,
                "-screen", "0",
                f"{self.config.screen_width}x{self.config.screen_height}x24",
                "-ac",  # disable access control
            ]

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)

            # 检查进程是否启动成功
            if self._process.poll() is not None:
                logger.error("Xvfb进程启动后立即退出")
                return False

            # 设置环境变量
            import os
            os.environ["DISPLAY"] = self._display

            logger.info(f"Xvfb环境已启动: display={self._display}")
            return True

        except FileNotFoundError:
            logger.warning("Xvfb未安装，回退到本地模式")
            return self._start_local()
        except Exception as e:
            logger.error(f"Xvfb启动失败: {e}")
            return False

    def _check_xvfb_running(self) -> bool:
        """检查Xvfb是否运行"""
        return self._process is not None and self._process.poll() is None

    def _xvfb_screenshot(self) -> Optional[str]:
        """通过Xvfb截图"""
        try:
            output_path = "/tmp/xvfb_screenshot.png"
            subprocess.run(
                ["import", "-window", "root", "-display", self._display or ":99", output_path],
                capture_output=True, timeout=5
            )
            return output_path
        except Exception as e:
            logger.error(f"Xvfb截图失败: {e}")
            return None

    # ========== RDP 环境 ==========

    def _start_rdp(self) -> bool:
        """连接Windows远程桌面"""
        logger.info(
            f"RDP模式: 请确保目标机器 {self.config.host}:{self.config.port} "
            f"已开启远程桌面服务"
        )
        # RDP 通常是连接到已有的远程桌面，不需要启动新进程
        # 这里只标记为就绪状态
        self._display = None
        return True

    # ========== 本地模式 ==========

    def _start_local(self) -> bool:
        """本地模式（不隔离，仅用于开发调试）"""
        logger.warning(
            "使用本地模式：AI操作将直接作用于当前桌面！"
            "这可能会干扰你的正常操作。仅建议开发调试时使用。"
        )
        import os
        self._display = os.environ.get("DISPLAY", ":0")
        return True

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
