"""动作执行模块

将AI生成的JSON指令转化为真实GUI操作。
支持多种执行后端：pyautogui、Playwright、pywinauto 等。
"""

from __future__ import annotations

import time
from typing import Optional

from loguru import logger

from ..config import ExecutionConfig
from ..models import Action, ActionType


class ActionExecutor:
    """动作执行器

    负责将结构化动作指令转化为真实 GUI 操作。
    执行优先级：API/CLI > Playwright > UI Automation > OCR+坐标操作
    """

    def __init__(self, config: ExecutionConfig):
        self.config = config
        self._execution_log: list[dict] = []

    def execute(self, action: Action) -> bool:
        """执行动作指令

        Args:
            action: 结构化动作指令

        Returns:
            执行是否成功（不代表结果正确，只是操作本身是否完成）
        """
        logger.info(
            f"执行动作: {action.action_type.value} "
            f"目标='{action.target}' 坐标=({action.x}, {action.y})"
        )

        start_time = time.time()
        success = False

        try:
            handler = self._get_handler(action.action_type)
            success = handler(action)

        except Exception as e:
            logger.error(f"动作执行异常: {e}")
            success = False

        duration = time.time() - start_time
        self._execution_log.append({
            "action": action.action_type.value,
            "target": action.target,
            "success": success,
            "duration": duration,
        })

        # 执行后短暂等待，让界面响应
        time.sleep(self.config.click_delay)

        return success

    def _get_handler(self, action_type: ActionType):
        """获取对应的动作处理函数"""
        handlers = {
            ActionType.CLICK: self._do_click,
            ActionType.DOUBLE_CLICK: self._do_double_click,
            ActionType.RIGHT_CLICK: self._do_right_click,
            ActionType.TYPE: self._do_type,
            ActionType.HOTKEY: self._do_hotkey,
            ActionType.SCROLL: self._do_scroll,
            ActionType.WAIT: self._do_wait,
            ActionType.BACK: self._do_back,
            ActionType.STOP: self._do_stop,
            ActionType.ASK_HUMAN: self._do_ask_human,
            ActionType.DRAG: self._do_drag,
        }
        return handlers.get(action_type, self._do_unknown)

    def _do_click(self, action: Action) -> bool:
        """执行单击操作"""
        import pyautogui

        if action.x is None or action.y is None:
            logger.error("click 操作缺少坐标")
            return False

        pyautogui.click(action.x, action.y)
        logger.debug(f"单击坐标 ({action.x}, {action.y})")
        return True

    def _do_double_click(self, action: Action) -> bool:
        """执行双击操作"""
        import pyautogui

        if action.x is None or action.y is None:
            logger.error("double_click 操作缺少坐标")
            return False

        pyautogui.doubleClick(action.x, action.y)
        logger.debug(f"双击坐标 ({action.x}, {action.y})")
        return True

    def _do_right_click(self, action: Action) -> bool:
        """执行右键点击"""
        import pyautogui

        if action.x is None or action.y is None:
            logger.error("right_click 操作缺少坐标")
            return False

        pyautogui.rightClick(action.x, action.y)
        logger.debug(f"右键点击 ({action.x}, {action.y})")
        return True

    def _do_type(self, action: Action) -> bool:
        """执行文字输入"""
        import pyautogui

        if not action.text:
            logger.error("type 操作缺少输入文字")
            return False

        # 如果指定了坐标，先点击获取焦点
        if action.x is not None and action.y is not None:
            pyautogui.click(action.x, action.y)
            time.sleep(0.2)

        # 先清空可能存在的内容（Ctrl+A 全选后输入）
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)

        # 使用 pyperclip 粘贴方式输入（支持中文）
        try:
            import pyperclip
            pyperclip.copy(action.text)
            pyautogui.hotkey("ctrl", "v")
        except ImportError:
            # 回退到逐字符输入（不支持中文）
            pyautogui.typewrite(action.text, interval=self.config.type_interval)

        logger.debug(f"输入文字: '{action.text}'")
        return True

    def _do_hotkey(self, action: Action) -> bool:
        """执行快捷键"""
        import pyautogui

        if not action.text:
            logger.error("hotkey 操作缺少按键描述")
            return False

        # 解析快捷键组合（如 "ctrl+c", "alt+f4", "enter"）
        keys = [k.strip().lower() for k in action.text.split("+")]
        pyautogui.hotkey(*keys)
        logger.debug(f"快捷键: {'+'.join(keys)}")
        return True

    def _do_scroll(self, action: Action) -> bool:
        """执行滚动操作"""
        import pyautogui

        direction = action.parameters.get("direction", "down")
        amount = action.parameters.get("amount", self.config.scroll_amount)

        # 如果指定了坐标，先移动鼠标
        if action.x is not None and action.y is not None:
            pyautogui.moveTo(action.x, action.y)

        if direction in ("down", "d"):
            pyautogui.scroll(-amount)
        elif direction in ("up", "u"):
            pyautogui.scroll(amount)
        elif direction in ("left", "l"):
            pyautogui.hscroll(-amount)
        elif direction in ("right", "r"):
            pyautogui.hscroll(amount)

        logger.debug(f"滚动: 方向={direction}, 量={amount}")
        return True

    def _do_wait(self, action: Action) -> bool:
        """等待操作"""
        wait_time = action.parameters.get("duration", 2.0)
        wait_time = min(wait_time, self.config.max_wait_time)

        logger.debug(f"等待 {wait_time} 秒")
        time.sleep(wait_time)
        return True

    def _do_back(self, action: Action) -> bool:
        """返回上一页"""
        import pyautogui

        # 尝试浏览器后退快捷键
        pyautogui.hotkey("alt", "left")
        logger.debug("执行返回操作 (Alt+Left)")
        return True

    def _do_stop(self, action: Action) -> bool:
        """任务完成，停止执行"""
        logger.info("任务标记为完成")
        return True

    def _do_ask_human(self, action: Action) -> bool:
        """请求人工接管"""
        reason = action.reason or action.text or "遇到无法处理的情况"
        logger.warning(f"请求人工接管: {reason}")
        return True

    def _do_drag(self, action: Action) -> bool:
        """执行拖拽操作"""
        import pyautogui

        if action.x is None or action.y is None:
            logger.error("drag 操作缺少起始坐标")
            return False

        end_x = action.parameters.get("end_x")
        end_y = action.parameters.get("end_y")

        if end_x is None or end_y is None:
            logger.error("drag 操作缺少终点坐标")
            return False

        pyautogui.moveTo(action.x, action.y)
        time.sleep(0.1)
        pyautogui.drag(end_x - action.x, end_y - action.y, duration=0.5)
        logger.debug(f"拖拽: ({action.x},{action.y}) -> ({end_x},{end_y})")
        return True

    def _do_unknown(self, action: Action) -> bool:
        """未知动作类型"""
        logger.warning(f"未知动作类型: {action.action_type}")
        return False

    @property
    def execution_log(self) -> list[dict]:
        """获取执行日志"""
        return self._execution_log

    def clear_log(self):
        """清空执行日志"""
        self._execution_log = []
