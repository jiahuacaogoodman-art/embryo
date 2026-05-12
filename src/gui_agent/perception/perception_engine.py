"""感知引擎 - 界面感知模块的总调度

整合截图、OCR、控件树和鼠标坐标，输出结构化的屏幕状态。
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from loguru import logger

from ..config import PerceptionConfig
from ..models import ScreenState, UIElement
from .element_detector import ElementDetector
from .ocr_engine import OCREngine
from .screen_capture import ScreenCapture


class PerceptionEngine:
    """界面感知引擎 - GUI Agent 的"眼睛"

    综合使用截图、OCR、鼠标坐标、控件树等信息，
    将当前界面状态转化为结构化表示。
    """

    def __init__(self, config: PerceptionConfig):
        self.config = config
        self.screen_capture = ScreenCapture(config)
        self.ocr_engine = OCREngine(config)
        self.element_detector = ElementDetector(config, self.ocr_engine)
        self._last_state: Optional[ScreenState] = None

    def perceive(self, save_screenshot: bool = False, screenshot_dir: str = "./screenshots") -> ScreenState:
        """执行一次完整的界面感知

        流程：
        1. 截取当前屏幕
        2. 获取鼠标位置
        3. 获取窗口标题
        4. OCR 识别文字
        5. 检测 UI 元素
        6. 组装为 ScreenState

        Args:
            save_screenshot: 是否保存截图文件
            screenshot_dir: 截图保存目录

        Returns:
            当前屏幕状态的结构化表示
        """
        logger.info("开始界面感知...")

        # 1. 截图
        screenshot = self.screen_capture.capture_full_screen()

        # 2. 鼠标位置
        mouse_pos = self._get_mouse_position()

        # 3. 窗口标题
        window_title = self._get_active_window_title()

        # 4. OCR 文字识别
        detected_text = self.ocr_engine.get_all_text(screenshot)

        # 5. UI 元素检测
        elements = self.element_detector.detect_elements(screenshot)

        # 6. 保存截图（如果需要）
        screenshot_path = None
        if save_screenshot:
            screenshot_path = self.screen_capture.save_screenshot(
                screenshot, screenshot_dir, prefix="perceive"
            )

        # 7. 组装状态
        state = ScreenState(
            window_title=window_title,
            mouse_position=mouse_pos,
            screen_size=(screenshot.shape[1], screenshot.shape[0]),
            detected_text=detected_text,
            elements=elements,
            screenshot_path=screenshot_path,
            timestamp=time.time(),
        )

        self._last_state = state
        logger.info(
            f"界面感知完成: 窗口='{window_title}', "
            f"文字={len(detected_text)}项, 元素={len(elements)}个"
        )
        return state

    def quick_perceive(self) -> ScreenState:
        """快速感知（仅截图和基本信息，跳过深度OCR）

        用于操作后的快速验证场景。
        """
        screenshot = self.screen_capture.capture_full_screen()
        mouse_pos = self._get_mouse_position()
        window_title = self._get_active_window_title()

        state = ScreenState(
            window_title=window_title,
            mouse_position=mouse_pos,
            screen_size=(screenshot.shape[1], screenshot.shape[0]),
            detected_text=[],
            elements=[],
            timestamp=time.time(),
        )
        return state

    def find_target(self, text: str) -> list[UIElement]:
        """在当前界面中查找目标元素

        Args:
            text: 目标文字描述

        Returns:
            匹配的UI元素列表
        """
        screenshot = self.screen_capture.capture_full_screen()
        return self.element_detector.find_element_by_text(screenshot, text)

    def compare_states(self, state_before: ScreenState, state_after: ScreenState) -> dict:
        """比较两个界面状态的差异

        Args:
            state_before: 操作前状态
            state_after: 操作后状态

        Returns:
            差异描述字典
        """
        changes = {
            "title_changed": state_before.window_title != state_after.window_title,
            "mouse_moved": state_before.mouse_position != state_after.mouse_position,
            "new_texts": [],
            "removed_texts": [],
            "element_count_change": len(state_after.elements) - len(state_before.elements),
        }

        # 文字差异
        texts_before = set(state_before.detected_text)
        texts_after = set(state_after.detected_text)
        changes["new_texts"] = list(texts_after - texts_before)
        changes["removed_texts"] = list(texts_before - texts_after)

        return changes

    def get_screen_diff_score(self) -> float:
        """获取当前截图与上一次截图的差异分数

        Returns:
            差异比例 0.0~1.0
        """
        current = self.screen_capture.capture_full_screen()
        last = self.screen_capture.last_screenshot

        if last is None:
            return 1.0

        return self.screen_capture.get_screen_diff(last, current)

    def _get_mouse_position(self) -> tuple[int, int]:
        """获取当前鼠标位置"""
        try:
            import pyautogui
            pos = pyautogui.position()
            return (pos.x, pos.y)
        except Exception:
            return (0, 0)

    def _get_active_window_title(self) -> str:
        """获取当前活动窗口标题"""
        try:
            import pygetwindow as gw
            win = gw.getActiveWindow()
            return win.title if win else ""
        except Exception:
            # Linux 下尝试 xdotool
            try:
                import subprocess
                result = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True, text=True, timeout=2
                )
                return result.stdout.strip()
            except Exception:
                return ""

    @property
    def last_state(self) -> Optional[ScreenState]:
        return self._last_state
