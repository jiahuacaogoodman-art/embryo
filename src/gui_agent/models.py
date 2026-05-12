"""核心数据模型定义"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ============================================================
# 界面感知相关模型
# ============================================================


class ElementType(str, Enum):
    """UI 元素类型"""
    BUTTON = "button"
    INPUT = "input"
    LINK = "link"
    MENU = "menu"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    DROPDOWN = "dropdown"
    TEXT = "text"
    IMAGE = "image"
    DIALOG = "dialog"
    TAB = "tab"
    UNKNOWN = "unknown"


class BoundingBox(BaseModel):
    """元素边界框 (x1, y1, x2, y2)"""
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1


class UIElement(BaseModel):
    """识别到的 UI 元素"""
    element_type: ElementType
    label: str = ""
    bbox: BoundingBox
    confidence: float = 1.0
    is_enabled: bool = True
    is_visible: bool = True
    attributes: dict = Field(default_factory=dict)


class ScreenState(BaseModel):
    """当前屏幕状态（界面感知的输出）"""
    window_title: str = ""
    mouse_position: tuple[int, int] = (0, 0)
    screen_size: tuple[int, int] = (1920, 1080)
    detected_text: list[str] = Field(default_factory=list)
    elements: list[UIElement] = Field(default_factory=list)
    screenshot_path: Optional[str] = None
    timestamp: float = 0.0


# ============================================================
# 动作规划相关模型
# ============================================================


class ActionType(str, Enum):
    """动作类型"""
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE = "type"
    HOTKEY = "hotkey"
    SCROLL = "scroll"
    WAIT = "wait"
    BACK = "back"
    STOP = "stop"
    ASK_HUMAN = "ask_human"
    DRAG = "drag"


class RiskLevel(str, Enum):
    """操作风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Action(BaseModel):
    """AI 生成的结构化动作指令"""
    action_type: ActionType
    target: str = ""  # 目标描述
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None  # 输入文本或快捷键
    expected_result: str = ""
    reason: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    parameters: dict = Field(default_factory=dict)


# ============================================================
# 结果验证相关模型
# ============================================================


class VerificationStatus(str, Enum):
    """验证状态"""
    SUCCESS = "success"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    TIMEOUT = "timeout"


class VerificationResult(BaseModel):
    """操作验证结果"""
    status: VerificationStatus
    message: str = ""
    changes_detected: list[str] = Field(default_factory=list)
    screenshot_before: Optional[str] = None
    screenshot_after: Optional[str] = None
    similarity_score: float = 0.0


# ============================================================
# 错误诊断相关模型
# ============================================================


class FailureReason(str, Enum):
    """失败原因分类"""
    COORDINATE_OFFSET = "coordinate_offset"
    ELEMENT_NOT_LOADED = "element_not_loaded"
    FOCUS_ERROR = "focus_error"
    POPUP_BLOCKING = "popup_blocking"
    NETWORK_DELAY = "network_delay"
    OCR_FAILURE = "ocr_failure"
    STATE_MISMATCH = "state_mismatch"
    ELEMENT_DISABLED = "element_disabled"
    UNEXPECTED_DIALOG = "unexpected_dialog"
    UNKNOWN = "unknown"


class Diagnosis(BaseModel):
    """错误诊断结果"""
    failure_reason: FailureReason
    confidence: float = 0.0
    description: str = ""
    suggested_fix: str = ""


class ReplanStrategy(BaseModel):
    """重规划策略"""
    strategy_type: str  # retry / adjust_coord / wait_longer / alternative_path / escalate
    description: str = ""
    new_action: Optional[Action] = None
    adjustments: dict = Field(default_factory=dict)


# ============================================================
# 任务相关模型
# ============================================================


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class TaskStep(BaseModel):
    """任务步骤记录"""
    step_index: int
    action: Action
    screen_before: Optional[ScreenState] = None
    screen_after: Optional[ScreenState] = None
    verification: Optional[VerificationResult] = None
    diagnosis: Optional[Diagnosis] = None
    retry_count: int = 0
    duration: float = 0.0


class Task(BaseModel):
    """完整任务"""
    task_id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    steps: list[TaskStep] = Field(default_factory=list)
    total_retries: int = 0
    error_log: list[str] = Field(default_factory=list)
