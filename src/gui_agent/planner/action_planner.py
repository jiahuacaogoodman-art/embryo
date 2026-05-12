"""动作规划模块

由大语言模型根据当前界面状态、用户目标和历史操作步骤，
生成下一步最合理的标准化 JSON 动作指令。
"""

from __future__ import annotations

import json
from typing import Optional

from loguru import logger

from ..config import LLMConfig
from ..models import (
    Action,
    ActionType,
    RiskLevel,
    ScreenState,
    TaskStep,
)


# 系统提示词模板
SYSTEM_PROMPT = """你是一个 GUI 自动化操作助手。你的任务是根据当前屏幕状态和用户目标，生成下一步操作指令。

## 你的能力
你可以生成以下类型的操作指令：
- click: 单击目标位置
- double_click: 双击目标
- right_click: 右键点击
- type: 在当前焦点位置输入文字
- hotkey: 执行键盘快捷键（如 ctrl+c, enter, tab）
- scroll: 滚动页面（up/down/left/right）
- wait: 等待页面加载
- back: 返回上一页
- stop: 任务已完成
- ask_human: 遇到无法处理的情况，请求人工接管

## 输出格式
你必须输出严格的 JSON 格式，包含以下字段：
{
    "action_type": "操作类型",
    "target": "目标元素描述",
    "x": 点击的x坐标（仅click类操作需要）,
    "y": 点击的y坐标（仅click类操作需要）,
    "text": "输入文字或快捷键（type和hotkey操作需要）",
    "expected_result": "执行后预期结果",
    "reason": "为什么选择这个操作",
    "risk_level": "low/medium/high/critical"
}

## 规则
1. 每次只输出一个操作指令
2. 操作前先确认目标元素存在于当前界面
3. 如果目标不在当前界面，考虑滚动或导航
4. 如果连续操作失败，考虑使用 ask_human
5. 输入操作前确保目标输入框已获得焦点
6. 注意弹窗和对话框，优先处理它们
7. 只输出 JSON，不要输出任何其他文字
"""

USER_PROMPT_TEMPLATE = """## 当前任务
{task_description}

## 当前界面状态
- 窗口标题: {window_title}
- 鼠标位置: {mouse_position}
- 屏幕尺寸: {screen_size}

## 界面可见文字
{detected_text}

## 可操作元素
{elements}

## 已执行步骤
{history}

## 请生成下一步操作指令（JSON格式）
"""


class ActionPlanner:
    """动作规划器

    集成大语言模型，根据界面状态生成标准化操作指令。
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._history: list[TaskStep] = []

    def plan_next_action(
        self,
        task_description: str,
        screen_state: ScreenState,
        history: Optional[list[TaskStep]] = None,
    ) -> Action:
        """规划下一步操作

        Args:
            task_description: 用户任务描述
            screen_state: 当前界面状态
            history: 已执行的历史步骤

        Returns:
            下一步动作指令
        """
        logger.info("开始规划下一步操作...")

        if history is not None:
            self._history = history

        # 构建提示词
        user_prompt = self._build_user_prompt(task_description, screen_state)

        # 调用 LLM
        response_text = self._call_llm(user_prompt)

        # 解析 LLM 输出为 Action
        action = self._parse_action(response_text)

        logger.info(
            f"规划结果: {action.action_type.value} -> '{action.target}' "
            f"({action.x}, {action.y}) | 原因: {action.reason}"
        )
        return action

    def plan_task_steps(self, task_description: str, screen_state: ScreenState) -> list[str]:
        """将复杂任务拆解为步骤列表

        Args:
            task_description: 任务描述
            screen_state: 当前界面状态

        Returns:
            步骤描述列表
        """
        decompose_prompt = f"""请将以下任务拆解为具体操作步骤：

任务：{task_description}

当前界面：{screen_state.window_title}
可见元素：{', '.join(screen_state.detected_text[:20])}

请用编号列出每个步骤，每步一行，只描述具体操作。"""

        response = self._call_llm(decompose_prompt)

        # 解析步骤列表
        steps = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if line and any(line.startswith(f"{i}") for i in range(1, 20)):
                # 去除编号
                step = line.lstrip("0123456789.、) ").strip()
                if step:
                    steps.append(step)

        logger.info(f"任务拆解为 {len(steps)} 个步骤")
        return steps

    def _build_user_prompt(self, task_description: str, state: ScreenState) -> str:
        """构建发送给LLM的用户提示词"""

        # 格式化可见文字
        detected_text = "\n".join(f"  - {t}" for t in state.detected_text[:30])
        if not detected_text:
            detected_text = "  （无可见文字）"

        # 格式化元素列表
        elements_str = ""
        for i, elem in enumerate(state.elements[:20]):
            center = elem.bbox.center
            elements_str += (
                f"  [{i}] 类型={elem.element_type.value}, "
                f"标签='{elem.label}', "
                f"位置=({center[0]}, {center[1]}), "
                f"区域=({elem.bbox.x1},{elem.bbox.y1})-({elem.bbox.x2},{elem.bbox.y2})\n"
            )
        if not elements_str:
            elements_str = "  （未检测到可操作元素）"

        # 格式化历史步骤
        history_str = ""
        for step in self._history[-5:]:  # 只保留最近5步
            status = "✓" if step.verification and step.verification.status.value == "success" else "✗"
            history_str += (
                f"  {status} 步骤{step.step_index}: "
                f"{step.action.action_type.value} '{step.action.target}' "
                f"-> {step.action.expected_result}\n"
            )
        if not history_str:
            history_str = "  （尚无历史操作）"

        return USER_PROMPT_TEMPLATE.format(
            task_description=task_description,
            window_title=state.window_title,
            mouse_position=f"({state.mouse_position[0]}, {state.mouse_position[1]})",
            screen_size=f"{state.screen_size[0]}x{state.screen_size[1]}",
            detected_text=detected_text,
            elements=elements_str,
            history=history_str,
        )

    def _call_llm(self, user_prompt: str) -> str:
        """调用大语言模型

        支持 OpenAI 兼容接口。

        Args:
            user_prompt: 用户提示词

        Returns:
            模型输出文本
        """
        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            # 返回安全默认动作
            return json.dumps({
                "action_type": "ask_human",
                "target": "",
                "text": f"LLM调用失败: {str(e)}",
                "expected_result": "人工接管",
                "reason": "模型服务不可用",
                "risk_level": "low",
            })

    def _parse_action(self, response_text: str) -> Action:
        """解析LLM输出为结构化Action

        Args:
            response_text: LLM输出的JSON文本

        Returns:
            解析后的Action对象
        """
        try:
            # 尝试提取JSON（处理可能的markdown代码块包裹）
            json_text = self._extract_json(response_text)
            data = json.loads(json_text)

            action = Action(
                action_type=ActionType(data.get("action_type", "ask_human")),
                target=data.get("target", ""),
                x=data.get("x"),
                y=data.get("y"),
                text=data.get("text"),
                expected_result=data.get("expected_result", ""),
                reason=data.get("reason", ""),
                risk_level=RiskLevel(data.get("risk_level", "low")),
                parameters=data.get("parameters", {}),
            )
            return action

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"解析LLM输出失败: {e}, 原文: {response_text[:200]}")
            # 返回安全默认动作
            return Action(
                action_type=ActionType.ASK_HUMAN,
                target="",
                reason=f"LLM输出解析失败: {str(e)}",
                risk_level=RiskLevel.LOW,
            )

    def _extract_json(self, text: str) -> str:
        """从文本中提取JSON内容

        处理可能的 ```json ... ``` 包裹。
        """
        text = text.strip()

        # 去除markdown代码块标记
        if text.startswith("```"):
            lines = text.split("\n")
            # 去除首行 ```json 和末行 ```
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # 尝试找到 { 和 } 的配对
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start: end + 1]

        return text

    def _get_client(self):
        """获取或创建 OpenAI 客户端"""
        if self._client is None:
            from openai import OpenAI

            kwargs = {"api_key": self.config.api_key}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url

            self._client = OpenAI(**kwargs)

        return self._client

    def reset_history(self):
        """重置历史步骤"""
        self._history = []
