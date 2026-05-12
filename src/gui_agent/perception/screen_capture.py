"""屏幕截图模块

负责采集当前屏幕画面，支持全屏截图、区域截图和窗口截图。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger
from PIL import Image

from ..config import PerceptionConfig


class ScreenCapture:
    """屏幕截图采集器"""

    def __init__(self, config: PerceptionConfig):
        self.config = config
        self._last_screenshot: Optional[np.ndarray] = None
        self._last_capture_time: float = 0.0

    def capture_full_screen(self) -> np.ndarray:
        """全屏截图

        Returns:
            numpy数组格式的屏幕图像 (H, W, 3) BGR格式
        """
        try:
            import pyautogui

            screenshot = pyautogui.screenshot()
            img_array = np.array(screenshot)
            # PIL返回RGB，转换为BGR供OpenCV使用
            img_bgr = img_array[:, :, ::-1].copy()

            self._last_screenshot = img_bgr
            self._last_capture_time = time.time()

            logger.debug(f"全屏截图完成，尺寸: {img_bgr.shape[:2]}")
            return img_bgr

        except Exception as e:
            logger.error(f"全屏截图失败: {e}")
            raise

    def capture_region(self, x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
        """区域截图

        Args:
            x1, y1: 左上角坐标
            x2, y2: 右下角坐标

        Returns:
            numpy数组格式的区域图像
        """
        try:
            import pyautogui

            width = x2 - x1
            height = y2 - y1
            screenshot = pyautogui.screenshot(region=(x1, y1, width, height))
            img_array = np.array(screenshot)
            img_bgr = img_array[:, :, ::-1].copy()

            logger.debug(f"区域截图完成，区域: ({x1},{y1})-({x2},{y2})")
            return img_bgr

        except Exception as e:
            logger.error(f"区域截图失败: {e}")
            raise

    def capture_window(self, window_title: str) -> Optional[np.ndarray]:
        """窗口截图（仅限特定窗口）

        Args:
            window_title: 目标窗口标题（模糊匹配）

        Returns:
            窗口区域图像，未找到窗口则返回None
        """
        try:
            import pyautogui
            import pygetwindow as gw

            windows = gw.getWindowsWithTitle(window_title)
            if not windows:
                logger.warning(f"未找到窗口: {window_title}")
                return None

            win = windows[0]
            region = (win.left, win.top, win.width, win.height)
            screenshot = pyautogui.screenshot(region=region)
            img_array = np.array(screenshot)
            img_bgr = img_array[:, :, ::-1].copy()

            logger.debug(f"窗口截图完成: {window_title}")
            return img_bgr

        except ImportError:
            logger.warning("pygetwindow 不可用，回退到全屏截图")
            return self.capture_full_screen()
        except Exception as e:
            logger.error(f"窗口截图失败: {e}")
            return None

    def save_screenshot(self, image: np.ndarray, save_dir: str, prefix: str = "screen") -> str:
        """保存截图到文件

        Args:
            image: 图像数组
            save_dir: 保存目录
            prefix: 文件名前缀

        Returns:
            保存的文件路径
        """
        save_path = Path(save_dir)
        save_path.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_{timestamp}.png"
        filepath = save_path / filename

        # BGR转RGB再保存
        img_rgb = image[:, :, ::-1]
        Image.fromarray(img_rgb).save(str(filepath))

        logger.debug(f"截图已保存: {filepath}")
        return str(filepath)

    def get_screen_diff(self, img_before: np.ndarray, img_after: np.ndarray) -> float:
        """计算两张截图的差异比例

        Args:
            img_before: 操作前截图
            img_after: 操作后截图

        Returns:
            差异比例 0.0~1.0（0表示完全相同）
        """
        if img_before.shape != img_after.shape:
            logger.warning("前后截图尺寸不同，无法比较")
            return 1.0

        diff = np.abs(img_before.astype(np.float32) - img_after.astype(np.float32))
        # 像素差异超过阈值的比例
        changed_pixels = np.sum(diff > 30) / diff.size
        return float(changed_pixels)

    def get_diff_regions(
        self, img_before: np.ndarray, img_after: np.ndarray, threshold: int = 30
    ) -> list[tuple[int, int, int, int]]:
        """获取发生变化的区域

        Args:
            img_before: 操作前截图
            img_after: 操作后截图
            threshold: 像素差异阈值

        Returns:
            变化区域列表 [(x1, y1, x2, y2), ...]
        """
        import cv2

        if img_before.shape != img_after.shape:
            return []

        # 转灰度计算差异
        gray_before = cv2.cvtColor(img_before, cv2.COLOR_BGR2GRAY)
        gray_after = cv2.cvtColor(img_after, cv2.COLOR_BGR2GRAY)

        diff = cv2.absdiff(gray_before, gray_after)
        _, thresh = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)

        # 膨胀以连接邻近变化区域
        kernel = np.ones((5, 5), np.uint8)
        dilated = cv2.dilate(thresh, kernel, iterations=2)

        # 查找轮廓
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        regions = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w > 10 and h > 10:  # 过滤太小的噪声区域
                regions.append((x, y, x + w, y + h))

        logger.debug(f"检测到 {len(regions)} 个变化区域")
        return regions

    @property
    def last_screenshot(self) -> Optional[np.ndarray]:
        return self._last_screenshot

    @property
    def last_capture_time(self) -> float:
        return self._last_capture_time
