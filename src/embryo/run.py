"""Embryo 一体化入口 - 完整 pipeline 集成

把 Vision LLM + SoM + ReAct Loop + Sandbox/Local Backend 串联起来。
一行代码启动任务执行。

用法：

    # 最简单：本地桌面 + GPT-4o
    from embryo.run import run_task
    result = run_task("打开浏览器，搜索 Python 官网")

    # Docker 隔离 + Claude
    result = run_task(
        "登录 example.com",
        backend="sandbox",
        vision_model="claude-3-5-sonnet-20241022",
        vision_provider="anthropic",
    )

    # 完全自定义
    from embryo.run import EmbryoEngine, EngineConfig
    engine = EmbryoEngine(EngineConfig(
        backend="sandbox",
        sandbox_image="kasmweb/chrome:1.15.0",
        vision_model="gpt-4o",
        max_steps=50,
    ))
    with engine:
        result = engine.run("在淘宝搜索 iPhone 16")
        print(result.summary())
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from .action import ComputerBackend, PyAutoGUIBackend
from .logging import get_logger
from .perception.som import SoMAnnotator
from .runtime.react_loop import LoopResult, ReactConfig, ReactLoop
from .sandbox import DockerSandbox, SandboxBackend, SandboxConfig
from .vision import VisionConfig, VisionLLM

logger = get_logger(__name__)


# ============================================================
# Engine 配置
# ============================================================


class EngineConfig(BaseModel):
    """Embryo 引擎配置

    一个配置对象控制整个 pipeline。
    """

    # --- Backend ---
    backend: str = "local"  # "local" (pyautogui) | "sandbox" (docker)

    # --- Sandbox (backend="sandbox" 时生效) ---
    sandbox_image: str = "kasmweb/chrome:1.15.0"
    sandbox_screen_width: int = 1920
    sandbox_screen_height: int = 1080
    sandbox_memory: str = "2g"

    # --- Vision LLM ---
    vision_provider: str = "openai"  # "openai" | "anthropic"
    vision_model: str = "gpt-4o"
    vision_api_key: str = ""  # 空 = 从环境变量读取
    vision_base_url: str = ""
    vision_temperature: float = 0.1
    vision_max_tokens: int = 4096

    # --- ReAct Loop ---
    max_steps: int = 30
    action_delay_sec: float = 0.5
    screenshot_delay_sec: float = 0.3
    enable_som: bool = True
    enable_trace: bool = True
    traces_dir: str = ""  # 空 = ~/.embryo/traces

    # --- Supervised Mode ---
    supervised: bool = False
    confirm_keywords: list[str] = Field(default_factory=lambda: [
        "submit", "delete", "payment", "send", "purchase",
    ])

    def resolve_api_key(self) -> str:
        """解析 API key（配置优先，否则环境变量）"""
        if self.vision_api_key:
            return self.vision_api_key
        if self.vision_provider == "openai":
            return os.environ.get("OPENAI_API_KEY", "")
        elif self.vision_provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY", "")
        return ""

    def resolve_traces_dir(self) -> Path:
        if self.traces_dir:
            return Path(self.traces_dir)
        return Path.home() / ".embryo" / "traces"


# ============================================================
# Embryo Engine
# ============================================================


class EmbryoEngine:
    """Embryo 引擎 - 完整 pipeline 集成

    组装所有组件：
    - Backend: PyAutoGUI (local) 或 SandboxBackend (docker)
    - Vision: VisionLLM (GPT-4o / Claude)
    - SoM: SoMAnnotator
    - Loop: ReactLoop

    用法：
        engine = EmbryoEngine(EngineConfig(backend="sandbox"))
        with engine:
            result = engine.run("登录 example.com")
    """

    def __init__(self, config: Optional[EngineConfig] = None):
        self._config = config or EngineConfig()
        self._backend: Optional[ComputerBackend] = None
        self._vision: Optional[VisionLLM] = None
        self._annotator: Optional[SoMAnnotator] = None
        self._loop: Optional[ReactLoop] = None
        self._sandbox: Optional[DockerSandbox] = None
        self._started = False

    @property
    def config(self) -> EngineConfig:
        return self._config

    @property
    def backend(self) -> Optional[ComputerBackend]:
        return self._backend

    @property
    def vision(self) -> Optional[VisionLLM]:
        return self._vision

    def start(self) -> None:
        """初始化所有组件"""
        if self._started:
            return

        logger.info(
            "engine_starting",
            backend=self._config.backend,
            model=self._config.vision_model,
        )

        # 1. Backend
        self._backend = self._create_backend()
        self._backend.setup()

        # 2. Vision LLM
        self._vision = self._create_vision()

        # 3. SoM Annotator
        self._annotator = SoMAnnotator()

        # 4. ReactLoop
        react_config = ReactConfig(
            max_steps=self._config.max_steps,
            action_delay_sec=self._config.action_delay_sec,
            screenshot_delay_sec=self._config.screenshot_delay_sec,
            enable_som=self._config.enable_som,
            enable_trace=self._config.enable_trace,
            traces_dir=self._config.resolve_traces_dir(),
            confirm_keywords=self._config.confirm_keywords,
        )

        # Supervised mode 确认回调
        if self._config.supervised:
            react_config.confirm_fn = self._default_confirm

        self._loop = ReactLoop(
            backend=self._backend,
            vision=self._vision,
            config=react_config,
            annotator=self._annotator,
        )

        self._started = True
        logger.info("engine_started")

    def stop(self) -> None:
        """清理所有组件"""
        if not self._started:
            return

        if self._backend:
            self._backend.teardown()
            self._backend = None

        self._vision = None
        self._annotator = None
        self._loop = None
        self._started = False
        logger.info("engine_stopped")

    def run(self, task: str) -> LoopResult:
        """执行任务

        Args:
            task: 自然语言任务描述

        Returns:
            LoopResult 包含执行状态和所有步骤
        """
        if not self._started:
            self.start()

        logger.info("engine_run", task=task[:80])
        return self._loop.run(task)

    # ============================================================
    # 组件工厂
    # ============================================================

    def _create_backend(self) -> ComputerBackend:
        """创建 GUI 操作后端"""
        if self._config.backend == "sandbox":
            sandbox_config = SandboxConfig(
                image=self._config.sandbox_image,
                screen_width=self._config.sandbox_screen_width,
                screen_height=self._config.sandbox_screen_height,
                memory_limit=self._config.sandbox_memory,
            )
            self._sandbox = DockerSandbox(sandbox_config)
            return SandboxBackend(config=sandbox_config, sandbox=self._sandbox)
        else:
            # 本地 PyAutoGUI
            return PyAutoGUIBackend()

    def _create_vision(self) -> VisionLLM:
        """创建 Vision LLM 客户端"""
        from .vision.llm import VisionProvider

        provider = VisionProvider(self._config.vision_provider)

        vision_config = VisionConfig(
            provider=provider,
            model=self._config.vision_model,
            api_key=self._config.resolve_api_key(),
            base_url=self._config.vision_base_url,
            max_tokens=self._config.vision_max_tokens,
            temperature=self._config.vision_temperature,
        )
        return VisionLLM(vision_config)

    @staticmethod
    def _default_confirm(decision) -> bool:
        """默认确认回调（CLI 交互）"""
        print(f"\n{'='*50}")
        print(f"[CONFIRM] 即将执行高风险操作:")
        print(f"  动作: {decision.action}")
        print(f"  目标: {decision.target_text}")
        print(f"  原因: {decision.reasoning}")
        print(f"{'='*50}")
        answer = input("允许执行? (y/n): ").strip().lower()
        return answer in ("y", "yes", "是")

    # ============================================================
    # Context Manager
    # ============================================================

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


# ============================================================
# 便捷函数
# ============================================================


def run_task(
    task: str,
    backend: str = "local",
    vision_model: str = "gpt-4o",
    vision_provider: str = "openai",
    api_key: str = "",
    max_steps: int = 30,
    supervised: bool = False,
    sandbox_image: str = "kasmweb/chrome:1.15.0",
    **kwargs,
) -> LoopResult:
    """一行代码执行 GUI 任务

    Args:
        task: 任务描述
        backend: "local" (pyautogui) 或 "sandbox" (docker)
        vision_model: Vision LLM 模型名
        vision_provider: "openai" 或 "anthropic"
        api_key: API key（空=环境变量）
        max_steps: 最大步数
        supervised: 是否需要确认高风险操作
        sandbox_image: Docker 镜像（backend=sandbox 时）

    Returns:
        LoopResult

    Example:
        result = run_task("打开 Chrome 搜索天气")
        if result.success:
            print("完成！")
    """
    config = EngineConfig(
        backend=backend,
        vision_model=vision_model,
        vision_provider=vision_provider,
        vision_api_key=api_key,
        max_steps=max_steps,
        supervised=supervised,
        sandbox_image=sandbox_image,
        **kwargs,
    )

    with EmbryoEngine(config) as engine:
        return engine.run(task)
