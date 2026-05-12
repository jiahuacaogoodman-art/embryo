"""PyAutoGUI 后端 - 前台坐标操作

将现有 pyautogui 实现封装为 ComputerBackend 接口。
适用场景：本地桌面前台操作，作为其他后端不可用时的 fallback。

限制：
- 前台操作，会抢占用户鼠标/键盘焦点
- 依赖坐标，受缩放/分辨率影响
- 安全隔离弱
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from ..logging import get_logger
from .backend import ActionResult, ActionStatus, ComputerBackend, ScreenInfo

logger = get_logger(__name__)

# 操作后等待界面响应的时间
_ACTION_DELAY = 0.3
# 重试延时
_RETRY_DELAY = 0.5


class PyAutoGUIBackend(ComputerBackend):
    """基于 pyautogui 的 GUI 操作后端

    直接使用 pyautogui 进行截图、点击、输入等操作。
    每个操作前后都做截图 hash 对比来验证效果。
    """

    def __init__(self, screenshot_dir: str | Path | None = None):
        """
        Args:
            screenshot_dir: 截图保存目录，默认 ~/.embryo/screenshots
        """
        if screenshot_dir is None:
            self._screenshot_dir = Path.home() / ".embryo" / "screenshots"
        else:
            self._screenshot_dir = Path(screenshot_dir)

    def setup(self) -> None:
        """确保截图目录存在"""
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _import_pyautogui(self):
        """延迟导入 pyautogui，避免无 GUI 环境崩溃"""
        try:
            import pyautogui

            pyautogui.FAILSAFE = True
            return pyautogui
        except ImportError:
            raise ImportError(
                "pyautogui 未安装。运行: pip install pyautogui pillow"
            )

    def _screenshot_hash(self, img) -> str:
        """计算截图前 10000 字节的 MD5，用于快速比对"""
        return hashlib.md5(img.tobytes()[:10000]).hexdigest()

    def _save_screenshot(self, img, suffix: str = "") -> str:
        """保存截图并返回路径"""
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        filename = f"screen_{int(time.time() * 1000)}{suffix}.png"
        path = str(self._screenshot_dir / filename)
        img.save(path)
        return path

    # ========================================
    # 接口实现
    # ========================================

    def get_screen_info(self) -> ScreenInfo:
        pyautogui = self._import_pyautogui()
        size = pyautogui.size()
        return ScreenInfo(
            width=size.width,
            height=size.height,
            scale_factor=1.0,
            platform="desktop-pyautogui",
        )

    def screenshot(self, region: tuple[int, int, int, int] | None = None) -> ActionResult:
        try:
            pyautogui = self._import_pyautogui()

            if region:
                x1, y1, x2, y2 = region
                img = pyautogui.screenshot(region=(x1, y1, x2 - x1, y2 - y1))
            else:
                img = pyautogui.screenshot()

            path = self._save_screenshot(img)
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"截图已保存: {path} ({img.size[0]}x{img.size[1]})",
                screenshot_after=path,
                metadata={
                    "path": path,
                    "width": img.size[0],
                    "height": img.size[1],
                },
            )
        except ImportError as e:
            return ActionResult(status=ActionStatus.FAILED, message=str(e))
        except Exception as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"截图失败: {e}",
            )

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
    ) -> ActionResult:
        try:
            pyautogui = self._import_pyautogui()

            # 操作前截图
            before_img = pyautogui.screenshot()
            before_hash = self._screenshot_hash(before_img)
            before_path = self._save_screenshot(before_img, "_before")

            # 执行点击
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            time.sleep(_ACTION_DELAY)

            # 操作后截图验证
            after_img = pyautogui.screenshot()
            after_hash = self._screenshot_hash(after_img)
            after_path = self._save_screenshot(after_img, "_after")

            if before_hash != after_hash:
                logger.debug("click_verified", x=x, y=y, result="界面已变化")
                return ActionResult(
                    status=ActionStatus.SUCCESS,
                    message=f"已点击 ({x}, {y})，界面已响应变化",
                    screenshot_before=before_path,
                    screenshot_after=after_path,
                    metadata={"x": x, "y": y, "button": button, "clicks": clicks},
                )

            # 重试一次
            time.sleep(_RETRY_DELAY)
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            time.sleep(_ACTION_DELAY * 2)

            retry_img = pyautogui.screenshot()
            retry_hash = self._screenshot_hash(retry_img)
            retry_path = self._save_screenshot(retry_img, "_retry")

            if retry_hash != before_hash:
                return ActionResult(
                    status=ActionStatus.SUCCESS,
                    message=f"已点击 ({x}, {y})，重试后界面已响应",
                    screenshot_before=before_path,
                    screenshot_after=retry_path,
                    metadata={"x": x, "y": y, "retried": True},
                )

            logger.warning("click_no_change", x=x, y=y)
            return ActionResult(
                status=ActionStatus.NO_EFFECT,
                message=(
                    f"已点击 ({x}, {y})，但界面未发生变化。"
                    f"可能原因：坐标偏移、元素未加载、按钮被遮挡。"
                ),
                screenshot_before=before_path,
                screenshot_after=retry_path,
                metadata={"x": x, "y": y, "retried": True},
            )
        except ImportError as e:
            return ActionResult(status=ActionStatus.FAILED, message=str(e))
        except Exception as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"点击失败: {e}",
            )

    def type_text(self, text: str, interval: float = 0.02) -> ActionResult:
        try:
            pyautogui = self._import_pyautogui()

            # 操作前截图
            before_img = pyautogui.screenshot()
            before_hash = self._screenshot_hash(before_img)
            before_path = self._save_screenshot(before_img, "_before")

            # 中文等非 ASCII 字符使用粘贴方式
            if any(ord(c) > 127 for c in text):
                try:
                    import pyperclip

                    pyperclip.copy(text)
                    pyautogui.hotkey("ctrl", "v")
                except ImportError:
                    return ActionResult(
                        status=ActionStatus.FAILED,
                        message="输入中文需要 pyperclip 包。运行 pip install pyperclip",
                    )
            else:
                pyautogui.typewrite(text, interval=interval)

            time.sleep(_ACTION_DELAY)

            # 操作后验证
            after_img = pyautogui.screenshot()
            after_hash = self._screenshot_hash(after_img)
            after_path = self._save_screenshot(after_img, "_after")

            display_text = text[:50] + ("..." if len(text) > 50 else "")

            if before_hash != after_hash:
                return ActionResult(
                    status=ActionStatus.SUCCESS,
                    message=f"已输入: '{display_text}'，界面已响应",
                    screenshot_before=before_path,
                    screenshot_after=after_path,
                    metadata={"text": text},
                )
            else:
                return ActionResult(
                    status=ActionStatus.NO_EFFECT,
                    message=(
                        f"已输入: '{display_text}'，但界面未变化。"
                        f"可能原因：输入框未获得焦点。"
                    ),
                    screenshot_before=before_path,
                    screenshot_after=after_path,
                    metadata={"text": text},
                )
        except ImportError as e:
            return ActionResult(status=ActionStatus.FAILED, message=str(e))
        except Exception as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"输入失败: {e}",
            )

    def hotkey(self, keys: list[str]) -> ActionResult:
        try:
            pyautogui = self._import_pyautogui()
            pyautogui.hotkey(*keys)
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"已按下快捷键: {'+'.join(keys)}",
                metadata={"keys": keys},
            )
        except ImportError as e:
            return ActionResult(status=ActionStatus.FAILED, message=str(e))
        except Exception as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"快捷键失败: {e}",
            )

    def press_key(self, key: str) -> ActionResult:
        try:
            pyautogui = self._import_pyautogui()
            pyautogui.press(key)
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"已按下: {key}",
                metadata={"key": key},
            )
        except ImportError as e:
            return ActionResult(status=ActionStatus.FAILED, message=str(e))
        except Exception as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"按键失败: {e}",
            )

    def scroll(
        self,
        direction: str = "down",
        amount: int = 3,
        x: int = 0,
        y: int = 0,
    ) -> ActionResult:
        try:
            pyautogui = self._import_pyautogui()
            if x and y:
                pyautogui.moveTo(x, y)

            scroll_amount = amount if direction == "up" else -amount
            pyautogui.scroll(scroll_amount)
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"已滚动: 方向={direction}, 量={amount}",
                metadata={"direction": direction, "amount": amount, "x": x, "y": y},
            )
        except ImportError as e:
            return ActionResult(status=ActionStatus.FAILED, message=str(e))
        except Exception as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"滚动失败: {e}",
            )

    def mouse_move(self, x: int, y: int) -> ActionResult:
        try:
            pyautogui = self._import_pyautogui()
            pyautogui.moveTo(x=x, y=y, duration=0.2)
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"鼠标已移动到 ({x}, {y})",
                metadata={"x": x, "y": y},
            )
        except ImportError as e:
            return ActionResult(status=ActionStatus.FAILED, message=str(e))
        except Exception as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"移动失败: {e}",
            )

    def ocr(
        self,
        region: tuple[int, int, int, int] | None = None,
        language: str = "chi_sim+eng",
    ) -> ActionResult:
        try:
            pyautogui = self._import_pyautogui()
            import pytesseract

            if region:
                x1, y1, x2, y2 = region
                img = pyautogui.screenshot(region=(x1, y1, x2 - x1, y2 - y1))
            else:
                img = pyautogui.screenshot()

            text = pytesseract.image_to_string(img, lang=language).strip()

            if not text:
                return ActionResult(
                    status=ActionStatus.SUCCESS,
                    message="(未识别到文字)",
                    metadata={"text": "", "boxes": []},
                )

            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"OCR 识别完成，共 {len(text)} 字符",
                metadata={"text": text, "boxes": []},
            )
        except ImportError as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"依赖缺失: {e}。需要 pyautogui, pytesseract, pillow",
            )
        except Exception as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"OCR 失败: {e}",
            )

    def find_text(
        self,
        target_text: str,
        language: str = "chi_sim+eng",
    ) -> ActionResult:
        try:
            pyautogui = self._import_pyautogui()
            import pytesseract

            img = pyautogui.screenshot()
            data = pytesseract.image_to_data(
                img, lang=language, output_type=pytesseract.Output.DICT
            )

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
                        cx = x + w // 2
                        cy = y + h // 2
                        matches.append(
                            {
                                "text": text,
                                "cx": cx,
                                "cy": cy,
                                "x": x,
                                "y": y,
                                "w": w,
                                "h": h,
                                "confidence": conf,
                            }
                        )

            if not matches:
                return ActionResult(
                    status=ActionStatus.TARGET_NOT_FOUND,
                    message=f"未在屏幕上找到 '{target_text}'",
                    metadata={"matches": []},
                )

            # 格式化消息
            match_strs = [
                f"'{m['text']}' 位置=({m['cx']},{m['cy']}) 置信度={m['confidence']}%"
                for m in matches
            ]
            return ActionResult(
                status=ActionStatus.SUCCESS,
                message=f"找到 {len(matches)} 处匹配:\n" + "\n".join(match_strs),
                metadata={"matches": matches},
            )
        except ImportError as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"依赖缺失: {e}",
            )
        except Exception as e:
            return ActionResult(
                status=ActionStatus.FAILED,
                message=f"查找失败: {e}",
            )
