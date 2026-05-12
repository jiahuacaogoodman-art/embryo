"""MCP Server - 暴露 Embryo 工具给外部 Agent

外部 Agent（如 Hermes/OpenClaw）通过 MCP 协议调用 Embryo 的 GUI 操作能力，
而不是通过 /api/chat 发自然语言。

暴露的工具：
- embryo.observe       截图+OCR 获取当前界面状态
- embryo.click         点击目标（语义化）
- embryo.type_text     输入文字
- embryo.hotkey        快捷键
- embryo.scroll        滚动
- embryo.find_text     在屏幕上查找文字
- embryo.verify        验证当前状态
- embryo.execute_plan  提交计划并执行
- embryo.get_trace     获取执行 trace

MCP 协议兼容：使用标准的 tool schema 格式。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..action import ComputerBackend, ActionResult, PyAutoGUIBackend
from ..logging import get_logger
from ..perception import Observation, Observer, ObserverConfig
from ..perception.target_resolver import TargetResolver, ResolvedTarget
from ..planning.schema import Target, TargetType
from ..verification import Verifier, VerifyResult
from ..planning.schema import VerificationRule, VerificationType
from .modes import ExecutionMode, ModeConfig

logger = get_logger(__name__)


class EmbryoMCPServer:
    """Embryo MCP Server

    暴露 GUI 操作能力为标准化工具接口。
    外部系统通过 JSON-RPC 风格调用。

    用法（外部 Agent 视角）：
        result = embryo.click(target={"type": "text", "value": "登录"})
        obs = embryo.observe()
        verified = embryo.verify(type="text_visible", target="欢迎")
    """

    def __init__(
        self,
        backend: Optional[ComputerBackend] = None,
        mode_config: Optional[ModeConfig] = None,
        llm_call: Optional[Callable[[str], str]] = None,
    ):
        self._backend = backend or PyAutoGUIBackend()
        self._mode_config = mode_config or ModeConfig()
        self._llm_call = llm_call
        self._observer = Observer(self._backend)
        self._target_resolver = TargetResolver(llm_call=llm_call)
        self._verifier = Verifier(llm_call=llm_call)

    @property
    def mode(self) -> ExecutionMode:
        return self._mode_config.mode

    def setup(self) -> None:
        """初始化后端"""
        self._backend.setup()

    def teardown(self) -> None:
        """清理后端"""
        self._backend.teardown()

    # --------------------------------------------------
    # MCP Tools
    # --------------------------------------------------

    def observe(self, include_ocr: bool = True, include_screenshot: bool = True) -> dict[str, Any]:
        """截图 + OCR 获取当前界面状态

        Returns:
            Observation 的字典表示
        """
        config = ObserverConfig(
            enable_screenshot=include_screenshot,
            enable_ocr=include_ocr,
        )
        self._observer.config = config
        obs = self._observer.observe()
        return {
            "screenshot_path": obs.screenshot_path,
            "screen_size": [obs.screen_width, obs.screen_height],
            "active_window": obs.active_window_title,
            "ocr_text": obs.ocr_text[:2000],
            "ocr_boxes_count": len(obs.ocr_boxes),
            "browser_url": obs.browser_url,
            "summary": obs.summary(),
        }

    def click(
        self,
        target: dict[str, Any],
        button: str = "left",
        clicks: int = 1,
    ) -> dict[str, Any]:
        """点击目标（支持语义化目标定位）

        Args:
            target: {"type": "text", "value": "登录"} 或 {"type": "coordinate", "x": 100, "y": 200}
            button: left/right/middle
            clicks: 点击次数

        Returns:
            操作结果
        """
        # 解析目标
        parsed_target = Target(**target)
        obs = self._observer.observe()
        resolved = self._target_resolver.resolve(parsed_target, obs)

        if not resolved.found:
            return {
                "success": False,
                "status": resolved.status.value,
                "message": resolved.message,
            }

        # 执行点击
        result = self._backend.click(
            x=resolved.cx,
            y=resolved.cy,
            button=button,
            clicks=clicks,
        )

        return {
            "success": result.success,
            "status": result.status.value,
            "message": result.message,
            "resolved_target": {
                "x": resolved.cx,
                "y": resolved.cy,
                "source": resolved.source,
                "confidence": resolved.confidence,
            },
        }

    def type_text(self, text: str, interval: float = 0.02) -> dict[str, Any]:
        """在当前焦点输入文字

        Args:
            text: 要输入的文字
            interval: 按键间隔

        Returns:
            操作结果
        """
        result = self._backend.type_text(text=text, interval=interval)
        return {
            "success": result.success,
            "status": result.status.value,
            "message": result.message,
        }

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        """执行快捷键

        Args:
            keys: 按键列表，如 ["ctrl", "c"]

        Returns:
            操作结果
        """
        result = self._backend.hotkey(keys=keys)
        return {
            "success": result.success,
            "status": result.status.value,
            "message": result.message,
        }

    def scroll(
        self,
        direction: str = "down",
        amount: int = 3,
        x: int = 0,
        y: int = 0,
    ) -> dict[str, Any]:
        """滚动页面

        Returns:
            操作结果
        """
        result = self._backend.scroll(direction=direction, amount=amount, x=x, y=y)
        return {
            "success": result.success,
            "status": result.status.value,
            "message": result.message,
        }

    def find_text(self, text: str) -> dict[str, Any]:
        """在屏幕上查找文字位置

        Args:
            text: 要查找的文字

        Returns:
            匹配结果
        """
        result = self._backend.find_text(target_text=text)
        return {
            "success": result.success,
            "status": result.status.value,
            "message": result.message,
            "matches": result.metadata.get("matches", []),
        }

    def verify(self, type: str, target: str = "", timeout_sec: float = 5.0) -> dict[str, Any]:
        """验证当前界面状态

        Args:
            type: 验证类型 (text_visible, text_absent, element_visible, url_contains, screenshot_changed)
            target: 验证目标
            timeout_sec: 超时时间

        Returns:
            验证结果
        """
        try:
            verif_type = VerificationType(type)
        except ValueError:
            return {
                "passed": False,
                "error": f"未知验证类型: {type}. 有效值: {[v.value for v in VerificationType]}",
            }

        rule = VerificationRule(type=verif_type, target=target, timeout_sec=timeout_sec)
        obs = self._observer.observe()
        result = self._verifier.verify_single(rule, obs)

        return {
            "passed": result.passed,
            "status": result.status.value,
            "message": result.message,
            "elapsed_sec": result.elapsed_sec,
        }

    def execute_plan(self, task: str, steps: Optional[list[dict]] = None) -> dict[str, Any]:
        """提交任务/计划并执行

        Args:
            task: 任务描述（如果 steps 为空，会自动生成 plan）
            steps: 预定义的步骤列表（可选）

        Returns:
            执行结果摘要
        """
        # Plan Mode 才允许
        if self._mode_config.mode == ExecutionMode.TOOL:
            return {
                "success": False,
                "error": "当前为 Tool Mode，不支持 execute_plan。请切换到 Plan 或 Supervised 模式。",
            }

        # TODO: 完整集成 TaskPlanner + PlanExecutor
        # 当前返回模式信息
        return {
            "success": False,
            "error": "execute_plan 需要完整集成 Planner + Executor（进行中）",
            "mode": self._mode_config.mode.value,
            "task": task,
        }

    def get_trace(self, task_id: str) -> dict[str, Any]:
        """获取任务执行 trace

        Args:
            task_id: 任务 ID

        Returns:
            trace 摘要
        """
        from ..core.trace import TaskTrace
        from pathlib import Path

        # 默认 traces 目录
        traces_dir = Path.home() / ".embryo" / "traces"
        trace_dir = traces_dir / task_id

        report = TaskTrace.load(trace_dir)
        if report:
            return report

        return {"error": f"Trace '{task_id}' 不存在"}

    # --------------------------------------------------
    # Schema 导出（供外部系统发现工具）
    # --------------------------------------------------

    def get_tools_schema(self) -> list[dict[str, Any]]:
        """导出所有 MCP 工具的 schema

        格式兼容 OpenAI function calling / MCP protocol。
        """
        return [
            {
                "name": "embryo.observe",
                "description": "截图+OCR 获取当前界面状态",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_ocr": {"type": "boolean", "default": True},
                        "include_screenshot": {"type": "boolean", "default": True},
                    },
                    "required": [],
                },
            },
            {
                "name": "embryo.click",
                "description": "点击目标。支持语义化目标定位（文字、角色、坐标等）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "object",
                            "description": "目标定义: {type: text|role|label|coordinate|description, value: ..., x: ..., y: ...}",
                            "properties": {
                                "type": {"type": "string", "enum": ["text", "role", "label", "coordinate", "description"]},
                                "value": {"type": "string"},
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                            },
                            "required": ["type"],
                        },
                        "button": {"type": "string", "default": "left"},
                        "clicks": {"type": "integer", "default": 1},
                    },
                    "required": ["target"],
                },
            },
            {
                "name": "embryo.type_text",
                "description": "在当前焦点位置输入文字",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要输入的文字"},
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "embryo.hotkey",
                "description": "执行键盘快捷键",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keys": {"type": "array", "items": {"type": "string"}, "description": "按键列表"},
                    },
                    "required": ["keys"],
                },
            },
            {
                "name": "embryo.scroll",
                "description": "滚动页面",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {"type": "string", "enum": ["up", "down"], "default": "down"},
                        "amount": {"type": "integer", "default": 3},
                    },
                    "required": [],
                },
            },
            {
                "name": "embryo.find_text",
                "description": "在屏幕上查找指定文字的位置",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要查找的文字"},
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "embryo.verify",
                "description": "验证当前界面状态是否满足条件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["text_visible", "text_absent", "element_visible", "url_contains", "screenshot_changed"],
                        },
                        "target": {"type": "string", "description": "验证目标（文字/URL片段等）"},
                        "timeout_sec": {"type": "number", "default": 5.0},
                    },
                    "required": ["type"],
                },
            },
            {
                "name": "embryo.execute_plan",
                "description": "提交任务描述，Embryo 自主规划并执行（Plan/Supervised模式）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "任务描述"},
                        "steps": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "可选的预定义步骤",
                        },
                    },
                    "required": ["task"],
                },
            },
            {
                "name": "embryo.get_trace",
                "description": "获取任务执行 trace",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "任务 ID"},
                    },
                    "required": ["task_id"],
                },
            },
        ]

    def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """分发工具调用

        Args:
            tool_name: 工具名称（如 "embryo.click"）
            arguments: 参数字典

        Returns:
            执行结果
        """
        # 去掉 "embryo." 前缀
        name = tool_name.removeprefix("embryo.")

        dispatch_map = {
            "observe": self.observe,
            "click": self.click,
            "type_text": self.type_text,
            "hotkey": self.hotkey,
            "scroll": self.scroll,
            "find_text": self.find_text,
            "verify": self.verify,
            "execute_plan": self.execute_plan,
            "get_trace": self.get_trace,
        }

        handler = dispatch_map.get(name)
        if not handler:
            return {"error": f"未知工具: {tool_name}. 可用: {list(dispatch_map.keys())}"}

        try:
            return handler(**arguments)
        except TypeError as e:
            return {"error": f"参数错误: {e}"}
        except Exception as e:
            logger.error("mcp_dispatch_error", tool=tool_name, error=str(e))
            return {"error": f"执行异常: {e}"}
