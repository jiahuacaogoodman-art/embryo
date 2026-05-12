"""SandboxBackend - Docker 容器内的 ComputerBackend 实现

实现 ComputerBackend 接口，所有操作转发到 Docker 容器内。
截图通过 docker exec 获取，鼠标键盘通过 xdotool 执行。

与 PyAutoGUIBackend 的区别：
- 不抢用户桌面焦点
- 分辨率固定（容器内 Xvfb）
- 完全隔离（安全）
- 可以并行多个容器执行不同任务
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Optional

from ..action.backend import ActionResult, ActionStatus, ComputerBackend, ScreenInfo
from ..logging import get_logger
from .docker_sandbox import DockerSandbox, SandboxConfig

logger = get_logger(__name__)


class SandboxBackend(ComputerBackend):
    """Docker 容器内的 GUI 操作后端

    封装 DockerSandbox 为标准 ComputerBackend 接口。
    ReactLoop / MCP Server 可以透明切换到这个后端。

    用法：
        backend = SandboxBackend(SandboxConfig(image="kasmweb/chrome:1.15.0"))
        backend.setup()  # 启动容器

        result = backend.screenshot()
        result = backend.click(x=500, y=300)
        result = backend.type_text("hello world")

        backend.teardown()  # 销毁容器
    """

    def __init__(
        self,
        config: Optional[SandboxConfig] = None,
        sandbox: Optional[DockerSandbox] = None,
        screenshot_dir: Optional[Path] = None,
    ):
        """
        Args:
            config: 容器配置（如果不提供 sandbox）
            sandbox: 已有的 DockerSandbox 实例（优先使用）
            screenshot_dir: 截图保存目录
        """
        self._config = config or SandboxConfig()
        self._sandbox = sandbox or DockerSandbox(self._config)
        self._screenshot_dir = screenshot_dir or Path.home() / ".embryo" / "sandbox_screenshots"
        self._owns_sandbox = sandbox is None  # 如果我们创建了 sandbox，负责清理

    @property
    def sandbox(self) -> DockerSandbox:
        return self._sandbox

    # ============================================================
    # ComputerBackend 实现
    # ============================================================

    def setup(self) -> None:
        """启动 Docker 容器"""
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        if self._owns_sandbox:
            self._sandbox.start()

    def teardown(self) -> None:
        """销毁 Docker 容器"""
        if self._owns_sandbox:
            self._sandbox.stop()

    def get_screen_info(self) -> ScreenInfo:
        return ScreenInfo(
            width=self._config.screen_width,
            height=self._config.screen_height,
            scale_factor=1.0,
            platform="docker-sandbox",
        )

    def screenshot(self, region: tuple[int, int, int, int] | None = None) -> ActionResult:
        try:
            png_bytes = self._sandbox.screenshot()

            # 保存到文件
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)
            filename = f"sandbox_{int(time.time() * 1000)}.png"
            filepath = self._screenshot_dir / filename
            filepath.write_bytes(png_bytes)

            # 获取尺寸
            width, height = self._config.screen_width, self._config.screen_height
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(png_bytes))
                width, height = img.size

                # 如果指定了区域，裁剪
                if region:
                    x1, y1, x2, y2 = region
                    img = img.crop((x1, y1, x2, y2))
                    cropped_path = self._screenshot_dir / f"sandbox_{int(time.time() * 1000)}_crop.png"
                    img.save(cropped_path)
                    filepath = cropped_path
                    width, height = img.size
            except ImportError:
                pass

            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"截图已保存: {filepath} ({width}x{height})",
                screenshot_after=str(filepath),
                metadata={"path": str(filepath), "width": width, "height": height},
            )
        except Exception as e:
            return ActionResult(status=ActionStatus.FAILED, message=f"容器截图失败: {e}")

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
    ) -> ActionResult:
        try:
            # 截图前 hash（验证用）
            before_bytes = self._sandbox.screenshot()
            before_hash = hashlib.md5(before_bytes[:5000]).hexdigest()

            # 映射按钮
            button_map = {"left": 1, "middle": 2, "right": 3}
            btn = button_map.get(button, 1)

            self._sandbox.mouse_click(x=x, y=y, button=btn, clicks=clicks)
            time.sleep(0.3)

            # 截图后 hash
            after_bytes = self._sandbox.screenshot()
            after_hash = hashlib.md5(after_bytes[:5000]).hexdigest()

            if before_hash != after_hash:
                return ActionResult(
                    status=ActionStatus.SUCCESS,
                    message=f"已点击 ({x}, {y})，界面已响应",
                    metadata={"x": x, "y": y, "button": button, "clicks": clicks},
                )
            else:
                return ActionResult(
                    status=ActionStatus.NO_EFFECT,
                    message=f"已点击 ({x}, {y})，但界面未变化",
                    metadata={"x": x, "y": y},
                )
        except Exception as e:
            return ActionResult(status=ActionStatus.FAILED, message=f"点击失败: {e}")

    def type_text(self, text: str, interval: float = 0.02) -> ActionResult:
        try:
            delay_ms = max(1, int(interval * 1000))
            self._sandbox.type_text(text=text, delay_ms=delay_ms)
            time.sleep(0.2)

            display = text[:40] + ("..." if len(text) > 40 else "")
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"已输入: '{display}'",
                metadata={"text": text},
            )
        except Exception as e:
            return ActionResult(status=ActionStatus.FAILED, message=f"输入失败: {e}")

    def hotkey(self, keys: list[str]) -> ActionResult:
        try:
            self._sandbox.hotkey(keys=keys)
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"已按下: {'+'.join(keys)}",
                metadata={"keys": keys},
            )
        except Exception as e:
            return ActionResult(status=ActionStatus.FAILED, message=f"快捷键失败: {e}")

    def press_key(self, key: str) -> ActionResult:
        try:
            # 映射常见按键名到 xdotool 格式
            key_map = {
                "enter": "Return",
                "tab": "Tab",
                "escape": "Escape",
                "backspace": "BackSpace",
                "delete": "Delete",
                "up": "Up",
                "down": "Down",
                "left": "Left",
                "right": "Right",
                "space": "space",
                "home": "Home",
                "end": "End",
                "pageup": "Page_Up",
                "pagedown": "Page_Down",
            }
            xdotool_key = key_map.get(key.lower(), key)
            self._sandbox.key_press(xdotool_key)
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"已按下: {key}",
                metadata={"key": key},
            )
        except Exception as e:
            return ActionResult(status=ActionStatus.FAILED, message=f"按键失败: {e}")

    def scroll(
        self,
        direction: str = "down",
        amount: int = 3,
        x: int = 0,
        y: int = 0,
    ) -> ActionResult:
        try:
            self._sandbox.scroll(direction=direction, amount=amount, x=x, y=y)
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"已滚动: {direction} x{amount}",
                metadata={"direction": direction, "amount": amount},
            )
        except Exception as e:
            return ActionResult(status=ActionStatus.FAILED, message=f"滚动失败: {e}")

    def mouse_move(self, x: int, y: int) -> ActionResult:
        try:
            self._sandbox.mouse_move(x=x, y=y)
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"鼠标移动到 ({x}, {y})",
                metadata={"x": x, "y": y},
            )
        except Exception as e:
            return ActionResult(status=ActionStatus.FAILED, message=f"移动失败: {e}")

    def ocr(
        self,
        region: tuple[int, int, int, int] | None = None,
        language: str = "chi_sim+eng",
    ) -> ActionResult:
        """在容器截图上做 OCR

        先截图保存到本地，再用 pytesseract 识别。
        """
        try:
            import pytesseract
            from PIL import Image
            import io

            png_bytes = self._sandbox.screenshot()
            img = Image.open(io.BytesIO(png_bytes))

            if region:
                x1, y1, x2, y2 = region
                img = img.crop((x1, y1, x2, y2))

            text = pytesseract.image_to_string(img, lang=language).strip()

            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"OCR 完成，{len(text)} 字符",
                metadata={"text": text, "boxes": []},
            )
        except ImportError:
            return ActionResult(
                status=ActionStatus.FAILED,
                message="OCR 需要 pytesseract + pillow",
            )
        except Exception as e:
            return ActionResult(status=ActionStatus.FAILED, message=f"OCR 失败: {e}")

    def find_text(
        self,
        target_text: str,
        language: str = "chi_sim+eng",
    ) -> ActionResult:
        """在容器截图中查找文字位置"""
        try:
            import pytesseract
            from PIL import Image
            import io

            png_bytes = self._sandbox.screenshot()
            img = Image.open(io.BytesIO(png_bytes))

            data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)

            matches = []
            n = len(data["text"])
            for i in range(n):
                text = data["text"][i].strip()
                if target_text in text or text in target_text:
                    conf = int(data["conf"][i])
                    if conf > 50:
                        x = data["left"][i]
                        y = data["top"][i]
                        w = data["width"][i]
                        h = data["height"][i]
                        matches.append({
                            "text": text,
                            "cx": x + w // 2,
                            "cy": y + h // 2,
                            "x": x, "y": y, "w": w, "h": h,
                            "confidence": conf,
                        })

            if not matches:
                return ActionResult(
                    status=ActionStatus.TARGET_NOT_FOUND,
                    message=f"未找到 '{target_text}'",
                    metadata={"matches": []},
                )

            match_strs = [f"'{m['text']}' @ ({m['cx']},{m['cy']})" for m in matches[:5]]
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"找到 {len(matches)} 处: " + ", ".join(match_strs),
                metadata={"matches": matches},
            )
        except ImportError:
            return ActionResult(status=ActionStatus.FAILED, message="需要 pytesseract + pillow")
        except Exception as e:
            return ActionResult(status=ActionStatus.FAILED, message=f"查找失败: {e}")
