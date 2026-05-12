"""VisionLLM - 截图 → Vision LLM → 结构化界面理解

核心能力：
1. analyze_screen(): 截图 → 识别所有可交互元素 + 界面状态
2. decide_action(): 截图 + 任务 → 选择下一步动作
3. verify_state(): 截图 + 条件 → 判断是否满足
4. describe_screen(): 截图 → 自然语言描述

关键设计：
- 截图编码为 base64 通过 OpenAI/Anthropic vision API 传入
- 输出严格 JSON，用 Pydantic 校验
- 支持 SoM 标注图（标注后的截图让 LLM 能精确指向元素）
- 支持多 provider（OpenAI GPT-4o / Anthropic Claude 3.5）
"""

from __future__ import annotations

import base64
import json
import re
import time
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 数据模型
# ============================================================


class VisionProvider(str, Enum):
    """Vision LLM 提供商"""
    OPENAI = "openai"  # GPT-4o / GPT-4-turbo
    ANTHROPIC = "anthropic"  # Claude 3.5 Sonnet


class VisionConfig(BaseModel):
    """Vision LLM 配置"""
    provider: VisionProvider = VisionProvider.OPENAI
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 4096
    temperature: float = 0.1
    # 图像处理
    max_image_size: int = 1920  # 最大边长（超过会缩放）
    image_detail: str = "high"  # OpenAI: auto/low/high


class UIElementInfo(BaseModel):
    """单个 UI 元素信息（Vision LLM 识别结果）"""
    id: int = 0  # SoM 编号
    type: str = ""  # button / input / link / text / icon / dropdown / checkbox / etc
    text: str = ""  # 可见文字
    description: str = ""  # LLM 对元素的描述
    bounds: list[int] = Field(default_factory=list)  # [x, y, w, h] 估计边界
    interactable: bool = True  # 是否可交互
    state: str = ""  # enabled / disabled / focused / checked / selected


class ScreenAnalysis(BaseModel):
    """屏幕分析结果"""
    # 界面基本信息
    app_name: str = ""  # 当前应用/网站名
    page_title: str = ""  # 页面标题
    url: str = ""  # 如果能看到地址栏
    description: str = ""  # 界面整体描述

    # 识别到的 UI 元素
    elements: list[UIElementInfo] = Field(default_factory=list)

    # 界面状态
    is_loading: bool = False
    has_popup: bool = False
    has_error: bool = False
    error_message: str = ""

    # 原始 LLM 响应（调试用）
    raw_response: str = ""
    latency_ms: int = 0


class ActionDecision(BaseModel):
    """LLM 决策的下一步动作"""
    action: str = ""  # click / type / scroll / hotkey / wait / done / fail
    target_id: int = -1  # SoM 元素编号（-1 表示无特定目标）
    target_text: str = ""  # 目标描述（备选定位）
    parameters: dict[str, Any] = Field(default_factory=dict)
    # type → parameters.text
    # scroll → parameters.direction, parameters.amount
    # hotkey → parameters.keys
    # click → (用 target_id 定位)
    reasoning: str = ""  # LLM 的推理过程
    confidence: float = 0.0  # 0-1 置信度
    is_done: bool = False  # 任务是否已完成
    is_failed: bool = False  # 任务是否已失败
    failure_reason: str = ""


class StateVerification(BaseModel):
    """状态验证结果"""
    satisfied: bool = False
    explanation: str = ""
    confidence: float = 0.0


# ============================================================
# Prompts
# ============================================================

ANALYZE_SCREEN_PROMPT = """\
你是一个 GUI 界面分析器。分析这张截图，识别所有可交互的 UI 元素。

输出严格 JSON 格式：
{
  "app_name": "应用名称",
  "page_title": "页面标题",
  "url": "如果能看到地址栏的URL",
  "description": "界面整体描述（一句话）",
  "is_loading": false,
  "has_popup": false,
  "has_error": false,
  "error_message": "",
  "elements": [
    {
      "id": 1,
      "type": "button|input|link|text|icon|dropdown|checkbox|radio|tab|menu",
      "text": "元素可见文字",
      "description": "简短描述这个元素的功能",
      "bounds": [x, y, width, height],
      "interactable": true,
      "state": "enabled|disabled|focused|checked"
    }
  ]
}

规则：
1. 只列出可见的元素
2. bounds 是像素坐标的估计值
3. 重点关注按钮、输入框、链接等可交互元素
4. 如果有 SoM 标注（元素上有彩色编号），直接用标注的编号作为 id
5. 只输出 JSON，不要解释"""

DECIDE_ACTION_PROMPT = """\
你是一个 GUI 操作 Agent。根据当前屏幕截图和任务要求，决定下一步操作。

## 任务
{task}

## 已完成的步骤
{history}

## 当前屏幕上的元素（SoM 标注编号）
{elements_summary}

## 要求
选择一个动作执行。输出严格 JSON：
{{
  "action": "click|type|scroll|hotkey|wait|done|fail",
  "target_id": 元素编号（SoM标注的数字，-1表示无目标）,
  "target_text": "目标描述（备用）",
  "parameters": {{}},
  "reasoning": "为什么选这个动作（简短）",
  "confidence": 0.9,
  "is_done": false,
  "is_failed": false,
  "failure_reason": ""
}}

动作说明：
- click: 点击 target_id 对应的元素
- type: 在当前焦点输入，parameters.text = "要输入的文字"
- scroll: 滚动，parameters.direction = "up|down", parameters.amount = 3
- hotkey: 快捷键，parameters.keys = ["ctrl", "a"]
- wait: 等待加载，parameters.seconds = 2
- done: 任务已完成
- fail: 任务无法完成，填写 failure_reason

规则：
1. 每次只做一个动作
2. 如果界面在加载，选 wait
3. 如果有弹窗挡住，先关掉弹窗
4. 如果任务已经完成（能看到成功标志），选 done
5. 只输出 JSON"""

VERIFY_STATE_PROMPT = """\
你是一个 GUI 状态验证器。根据截图判断以下条件是否满足。

## 条件
{condition}

## 要求
输出 JSON：
{{
  "satisfied": true/false,
  "explanation": "判断依据",
  "confidence": 0.95
}}

只看截图判断，只输出 JSON。"""


# ============================================================
# VisionLLM 主类
# ============================================================


class VisionLLM:
    """Vision LLM 客户端

    将截图作为 image message 发给多模态 LLM，获取结构化的界面理解。

    用法：
        vision = VisionLLM(VisionConfig(api_key="..."))
        analysis = vision.analyze_screen("screenshot.png")
        decision = vision.decide_action("screenshot.png", task="登录系统", history=[])
        check = vision.verify_state("screenshot.png", "页面显示'欢迎'")
    """

    def __init__(self, config: VisionConfig):
        self._config = config
        self._client = None

    @property
    def config(self) -> VisionConfig:
        return self._config

    def _get_client(self):
        """延迟初始化 LLM 客户端"""
        if self._client is not None:
            return self._client

        if self._config.provider == VisionProvider.OPENAI:
            from openai import OpenAI
            kwargs: dict[str, Any] = {}
            if self._config.api_key:
                kwargs["api_key"] = self._config.api_key
            if self._config.base_url:
                kwargs["base_url"] = self._config.base_url
            self._client = OpenAI(**kwargs)
        elif self._config.provider == VisionProvider.ANTHROPIC:
            try:
                import anthropic
                kwargs = {}
                if self._config.api_key:
                    kwargs["api_key"] = self._config.api_key
                self._client = anthropic.Anthropic(**kwargs)
            except ImportError:
                raise ImportError("anthropic 未安装: pip install anthropic")

        return self._client

    def _encode_image(self, image_path: str | Path) -> str:
        """将图片编码为 base64"""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"截图文件不存在: {path}")

        # 如果图片太大，缩放
        try:
            from PIL import Image
            img = Image.open(path)
            max_size = self._config.max_image_size
            if max(img.size) > max_size:
                ratio = max_size / max(img.size)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, Image.LANCZOS)
                # 保存到临时路径
                resized_path = path.parent / f"_resized_{path.name}"
                img.save(resized_path)
                path = resized_path
        except ImportError:
            pass  # 没有 PIL 就不缩放

        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _call_openai_vision(
        self,
        system_prompt: str,
        user_text: str,
        image_path: str | Path,
    ) -> str:
        """调用 OpenAI Vision API"""
        client = self._get_client()
        image_b64 = self._encode_image(image_path)

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_b64}",
                            "detail": self._config.image_detail,
                        },
                    },
                ],
            },
        ]

        response = client.chat.completions.create(
            model=self._config.model,
            messages=messages,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
        )

        return response.choices[0].message.content.strip()

    def _call_anthropic_vision(
        self,
        system_prompt: str,
        user_text: str,
        image_path: str | Path,
    ) -> str:
        """调用 Anthropic Vision API"""
        client = self._get_client()
        image_b64 = self._encode_image(image_path)

        # Anthropic 格式
        response = client.messages.create(
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        )

        return response.content[0].text.strip()

    def _call_vision(
        self,
        system_prompt: str,
        user_text: str,
        image_path: str | Path,
    ) -> str:
        """统一调用 Vision LLM"""
        if self._config.provider == VisionProvider.OPENAI:
            return self._call_openai_vision(system_prompt, user_text, image_path)
        elif self._config.provider == VisionProvider.ANTHROPIC:
            return self._call_anthropic_vision(system_prompt, user_text, image_path)
        else:
            raise ValueError(f"不支持的 provider: {self._config.provider}")

    def _extract_json(self, text: str) -> dict[str, Any]:
        """从 LLM 输出中提取 JSON"""
        text = text.strip()
        # 去 markdown 代码块
        code_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if code_match:
            text = code_match.group(1).strip()

        # 找 JSON 对象
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        # 尝试整段解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("vision_json_parse_failed", text_length=len(text))
            return {}

    # ============================================================
    # 公开 API
    # ============================================================

    def analyze_screen(self, image_path: str | Path) -> ScreenAnalysis:
        """分析屏幕截图，识别 UI 元素和界面状态

        Args:
            image_path: 截图文件路径

        Returns:
            ScreenAnalysis 包含元素列表和状态信息
        """
        start = time.time()

        try:
            raw = self._call_vision(
                system_prompt="你是 GUI 界面分析器。只输出 JSON。",
                user_text=ANALYZE_SCREEN_PROMPT,
                image_path=image_path,
            )

            data = self._extract_json(raw)
            latency = int((time.time() - start) * 1000)

            # 解析元素
            elements = []
            for el_data in data.get("elements", []):
                try:
                    elements.append(UIElementInfo(**el_data))
                except Exception:
                    pass

            analysis = ScreenAnalysis(
                app_name=data.get("app_name", ""),
                page_title=data.get("page_title", ""),
                url=data.get("url", ""),
                description=data.get("description", ""),
                elements=elements,
                is_loading=data.get("is_loading", False),
                has_popup=data.get("has_popup", False),
                has_error=data.get("has_error", False),
                error_message=data.get("error_message", ""),
                raw_response=raw,
                latency_ms=latency,
            )

            logger.info(
                "vision_analyze_done",
                elements=len(elements),
                latency_ms=latency,
                app=analysis.app_name,
            )
            return analysis

        except Exception as e:
            logger.error("vision_analyze_failed", error=str(e))
            return ScreenAnalysis(
                description=f"分析失败: {e}",
                raw_response=str(e),
                latency_ms=int((time.time() - start) * 1000),
            )

    def decide_action(
        self,
        image_path: str | Path,
        task: str,
        history: list[str] | None = None,
        elements_summary: str = "",
    ) -> ActionDecision:
        """看截图，决定下一步动作

        这是 ReAct loop 的核心——每步调用一次，LLM 看图决策。

        Args:
            image_path: 当前截图（可以是 SoM 标注后的图）
            task: 用户任务描述
            history: 已执行步骤列表（简短描述）
            elements_summary: 当前屏幕元素摘要（来自 SoM 或 analyze）

        Returns:
            ActionDecision 包含动作和参数
        """
        start = time.time()

        history_text = ""
        if history:
            for i, h in enumerate(history, 1):
                history_text += f"  {i}. {h}\n"
        else:
            history_text = "  (无)"

        prompt = DECIDE_ACTION_PROMPT.format(
            task=task,
            history=history_text,
            elements_summary=elements_summary or "(请从截图中识别元素)",
        )

        try:
            raw = self._call_vision(
                system_prompt="你是 GUI 操作 Agent。每次只决定一个动作。只输出 JSON。",
                user_text=prompt,
                image_path=image_path,
            )

            data = self._extract_json(raw)
            latency = int((time.time() - start) * 1000)

            decision = ActionDecision(
                action=data.get("action", "wait"),
                target_id=data.get("target_id", -1),
                target_text=data.get("target_text", ""),
                parameters=data.get("parameters", {}),
                reasoning=data.get("reasoning", ""),
                confidence=data.get("confidence", 0.5),
                is_done=data.get("is_done", False),
                is_failed=data.get("is_failed", False),
                failure_reason=data.get("failure_reason", ""),
            )

            logger.info(
                "vision_decide_done",
                action=decision.action,
                target_id=decision.target_id,
                reasoning=decision.reasoning[:50],
                latency_ms=latency,
            )
            return decision

        except Exception as e:
            logger.error("vision_decide_failed", error=str(e))
            return ActionDecision(
                action="fail",
                is_failed=True,
                failure_reason=f"Vision LLM 调用失败: {e}",
            )

    def verify_state(
        self,
        image_path: str | Path,
        condition: str,
    ) -> StateVerification:
        """看截图，验证条件是否满足

        Args:
            image_path: 当前截图
            condition: 要验证的条件（自然语言）

        Returns:
            StateVerification
        """
        start = time.time()

        prompt = VERIFY_STATE_PROMPT.format(condition=condition)

        try:
            raw = self._call_vision(
                system_prompt="你是 GUI 状态验证器。只输出 JSON。",
                user_text=prompt,
                image_path=image_path,
            )

            data = self._extract_json(raw)

            result = StateVerification(
                satisfied=data.get("satisfied", False),
                explanation=data.get("explanation", ""),
                confidence=data.get("confidence", 0.5),
            )

            logger.info(
                "vision_verify_done",
                condition=condition[:50],
                satisfied=result.satisfied,
                latency_ms=int((time.time() - start) * 1000),
            )
            return result

        except Exception as e:
            logger.error("vision_verify_failed", error=str(e))
            return StateVerification(
                satisfied=False,
                explanation=f"验证失败: {e}",
                confidence=0.0,
            )

    def describe_screen(self, image_path: str | Path) -> str:
        """简单描述当前屏幕内容（一段自然语言）

        Args:
            image_path: 截图路径

        Returns:
            自然语言描述
        """
        try:
            return self._call_vision(
                system_prompt="你是截图描述器。用中文简洁描述这张 GUI 截图的内容和状态。",
                user_text="描述这张截图中的界面内容、布局和可见的交互元素。2-3 句话即可。",
                image_path=image_path,
            )
        except Exception as e:
            return f"(描述失败: {e})"
