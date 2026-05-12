"""Observer - 统一感知采集器

调用 ComputerBackend 的各种感知方法，组装成 Observation 对象。
支持配置哪些感知源启用（screenshot / ocr / accessibility / dom）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..action.backend import ComputerBackend, ActionStatus
from ..logging import get_logger
from .observation import Observation, ObservationSource, OCRBox, UIElement

logger = get_logger(__name__)


@dataclass
class ObserverConfig:
    """观测器配置：控制采集哪些感知数据"""

    enable_screenshot: bool = True
    enable_ocr: bool = True
    enable_accessibility: bool = False  # 默认关闭，需要平台支持
    enable_dom: bool = False  # 默认关闭，仅浏览器场景
    ocr_language: str = "chi_sim+eng"
    screenshot_region: tuple[int, int, int, int] | None = None  # None=全屏


class Observer:
    """统一感知采集器

    通过 ComputerBackend 获取当前屏幕状态，
    组装成统一的 Observation 对象。

    用法：
        observer = Observer(backend, config)
        obs = observer.observe()
    """

    def __init__(self, backend: ComputerBackend, config: ObserverConfig | None = None):
        self._backend = backend
        self._config = config or ObserverConfig()

    @property
    def config(self) -> ObserverConfig:
        return self._config

    @config.setter
    def config(self, value: ObserverConfig) -> None:
        self._config = value

    def observe(self) -> Observation:
        """执行一次完整观测，返回 Observation 快照

        根据配置依次采集：截图、OCR、accessibility tree、DOM。
        任何一个环节失败不影响其他环节。
        """
        obs = Observation()

        # 屏幕基本信息
        try:
            screen_info = self._backend.get_screen_info()
            obs.screen_width = screen_info.width
            obs.screen_height = screen_info.height
        except Exception as e:
            logger.warning("observer_screen_info_failed", error=str(e))

        # 截图
        if self._config.enable_screenshot:
            self._collect_screenshot(obs)

        # OCR
        if self._config.enable_ocr:
            self._collect_ocr(obs)

        # Accessibility
        if self._config.enable_accessibility:
            self._collect_accessibility(obs)

        # DOM (浏览器)
        if self._config.enable_dom:
            self._collect_dom(obs)

        return obs

    def _collect_screenshot(self, obs: Observation) -> None:
        """采集截图"""
        try:
            result = self._backend.screenshot(region=self._config.screenshot_region)
            if result.status == ActionStatus.SUCCESS:
                obs.screenshot_path = result.metadata.get("path")
                obs.sources.append(ObservationSource.SCREENSHOT)
                # 更新屏幕尺寸（以实际截图为准）
                if "width" in result.metadata:
                    obs.screen_width = result.metadata["width"]
                if "height" in result.metadata:
                    obs.screen_height = result.metadata["height"]
            else:
                logger.warning("observer_screenshot_failed", message=result.message)
        except Exception as e:
            logger.warning("observer_screenshot_error", error=str(e))

    def _collect_ocr(self, obs: Observation) -> None:
        """采集 OCR 文字识别"""
        try:
            result = self._backend.ocr(
                region=self._config.screenshot_region,
                language=self._config.ocr_language,
            )
            if result.status == ActionStatus.SUCCESS:
                obs.ocr_text = result.metadata.get("text", "")
                # 解析 OCR boxes
                raw_boxes = result.metadata.get("boxes", [])
                for box_data in raw_boxes:
                    if isinstance(box_data, dict):
                        obs.ocr_boxes.append(OCRBox(**box_data))
                obs.sources.append(ObservationSource.OCR)
            else:
                logger.debug("observer_ocr_no_result", message=result.message)
        except Exception as e:
            logger.warning("observer_ocr_error", error=str(e))

    def _collect_accessibility(self, obs: Observation) -> None:
        """采集 Accessibility Tree（需要后端支持）"""
        # 当前 PyAutoGUIBackend 不支持 accessibility
        # 未来 AccessibilityBackend / PlaywrightBackend 会实现
        logger.debug("observer_accessibility_skipped", reason="backend not supported")

    def _collect_dom(self, obs: Observation) -> None:
        """采集 DOM 快照（需要浏览器后端支持）"""
        # 当前 PyAutoGUIBackend 不支持 DOM
        # 未来 PlaywrightBackend 会实现
        logger.debug("observer_dom_skipped", reason="backend not supported")
