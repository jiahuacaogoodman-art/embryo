"""全局配置"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class LLMConfig:
    """LLM 提供商配置"""
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    base_url: Optional[str] = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL"))
    temperature: float = 0.1
    max_tokens: int = 4096


@dataclass
class MemoryConfig:
    """持久记忆配置"""
    backend: str = "json"  # json / sqlite / redis
    storage_path: Path = field(default_factory=lambda: Path.home() / ".embryo" / "memory")
    max_entries: int = 1000
    auto_persist: bool = True


@dataclass
class SkillsConfig:
    """Skills 系统配置"""
    skills_dir: Path = field(default_factory=lambda: Path.home() / ".embryo" / "skills")
    bundled_skills_dir: Optional[Path] = None
    auto_create: bool = True  # 是否自动从经验创建 Skill
    max_skill_tokens: int = 2000  # 渐进式加载时单个 Skill 的 token 上限


@dataclass
class ComputerUseConfig:
    """Computer Use (GUI 操作) 配置"""
    enabled: bool = True
    backend: str = "pyautogui"  # pyautogui / playwright / xdotool
    screenshot_dir: Path = field(default_factory=lambda: Path.home() / ".embryo" / "screenshots")
    action_delay: float = 0.3
    max_retries: int = 3
    ocr_language: str = "chi_sim+eng"


@dataclass
class GatewayConfig:
    """Gateway 层配置"""
    host: str = "127.0.0.1"
    port: int = 8642
    channels: list[str] = field(default_factory=lambda: ["cli"])  # cli / telegram / discord / web


@dataclass
class Config:
    """总配置"""
    llm: LLMConfig = field(default_factory=LLMConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    computer_use: ComputerUseConfig = field(default_factory=ComputerUseConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    data_dir: Path = field(default_factory=lambda: Path.home() / ".embryo")
    log_level: str = "INFO"

    def ensure_dirs(self):
        """确保所有必要目录存在"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.memory.storage_path.mkdir(parents=True, exist_ok=True)
        self.skills.skills_dir.mkdir(parents=True, exist_ok=True)
        self.computer_use.screenshot_dir.mkdir(parents=True, exist_ok=True)
