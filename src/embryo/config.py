"""全局配置 - YAML 文件 + 环境变量覆盖 + 运行时校验

配置优先级：代码默认值 < YAML 文件 < 环境变量 < 运行时参数

配置文件位置: ~/.embryo/config.yaml
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class LLMConfig:
    """LLM 提供商配置"""
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: Optional[str] = None
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout: float = 60.0  # API 调用超时

    def __post_init__(self):
        # 环境变量覆盖
        if not self.api_key:
            self.api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self.base_url:
            self.base_url = os.environ.get("OPENAI_BASE_URL")
        env_model = os.environ.get("EMBRYO_MODEL")
        if env_model:
            self.model = env_model


@dataclass
class MemoryConfig:
    """持久记忆配置"""
    backend: str = "json"  # json / sqlite
    storage_path: Path = field(default_factory=lambda: Path.home() / ".embryo" / "memory")
    max_entries: int = 1000
    decay_half_life_days: float = 30.0  # 记忆半衰期


@dataclass
class SkillsConfig:
    """Skills 系统配置"""
    skills_dir: Path = field(default_factory=lambda: Path.home() / ".embryo" / "skills")
    bundled_skills_dir: Optional[Path] = None
    auto_create: bool = True
    max_skill_tokens: int = 2000  # 渐进式加载 token 预算
    max_loaded_skills: int = 3  # 单次最多加载 Skill 数


@dataclass
class ComputerUseConfig:
    """Computer Use (GUI 操作) 配置"""
    enabled: bool = True
    backend: str = "pyautogui"
    screenshot_dir: Path = field(default_factory=lambda: Path.home() / ".embryo" / "screenshots")
    action_delay: float = 0.3
    max_retries: int = 3
    verify_after_action: bool = True  # 操作后截图验证
    ocr_language: str = "chi_sim+eng"


@dataclass
class RuntimeConfig:
    """Agent Runtime 配置"""
    max_iterations: int = 30
    max_consecutive_errors: int = 3
    tool_timeout: int = 120
    max_tool_output: int = 8000  # 工具输出截断阈值
    sessions_dir: Path = field(default_factory=lambda: Path.home() / ".embryo" / "sessions")
    auto_save_session: bool = True


@dataclass
class Config:
    """总配置"""
    llm: LLMConfig = field(default_factory=LLMConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    computer_use: ComputerUseConfig = field(default_factory=ComputerUseConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    data_dir: Path = field(default_factory=lambda: Path.home() / ".embryo")
    log_level: str = "INFO"
    log_file: Optional[Path] = None

    def __post_init__(self):
        # 环境变量覆盖
        env_level = os.environ.get("EMBRYO_LOG_LEVEL")
        if env_level:
            self.log_level = env_level.upper()

    def ensure_dirs(self):
        """确保所有必要目录存在"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.memory.storage_path.mkdir(parents=True, exist_ok=True)
        self.skills.skills_dir.mkdir(parents=True, exist_ok=True)
        self.computer_use.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.runtime.sessions_dir.mkdir(parents=True, exist_ok=True)

    def validate(self) -> list[str]:
        """校验配置，返回问题列表"""
        issues = []
        if not self.llm.api_key:
            issues.append("LLM API key 未设置 (设置 OPENAI_API_KEY 环境变量)")
        if self.runtime.max_iterations < 1:
            issues.append("runtime.max_iterations 必须 >= 1")
        if self.memory.max_entries < 10:
            issues.append("memory.max_entries 必须 >= 10")
        return issues

    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        """从 YAML 文件加载配置"""
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls._from_dict(data or {})
        except ImportError:
            # 没有 pyyaml，尝试 JSON
            import json
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls._from_dict(data or {})
        except FileNotFoundError:
            return cls()

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> "Config":
        """从字典构建配置"""
        config = cls()

        # LLM
        llm_data = data.get("llm", {})
        for key, val in llm_data.items():
            if hasattr(config.llm, key):
                setattr(config.llm, key, val)

        # Memory
        mem_data = data.get("memory", {})
        for key, val in mem_data.items():
            if hasattr(config.memory, key):
                if key == "storage_path":
                    val = Path(val)
                setattr(config.memory, key, val)

        # Skills
        skills_data = data.get("skills", {})
        for key, val in skills_data.items():
            if hasattr(config.skills, key):
                if "dir" in key and val:
                    val = Path(val)
                setattr(config.skills, key, val)

        # Computer Use
        cu_data = data.get("computer_use", {})
        for key, val in cu_data.items():
            if hasattr(config.computer_use, key):
                if "dir" in key and val:
                    val = Path(val)
                setattr(config.computer_use, key, val)

        # Runtime
        rt_data = data.get("runtime", {})
        for key, val in rt_data.items():
            if hasattr(config.runtime, key):
                if "dir" in key and val:
                    val = Path(val)
                setattr(config.runtime, key, val)

        # Top-level
        if "data_dir" in data:
            config.data_dir = Path(data["data_dir"])
        if "log_level" in data:
            config.log_level = data["log_level"]
        if "log_file" in data:
            config.log_file = Path(data["log_file"])

        config.__post_init__()
        return config

    def to_yaml_template(self) -> str:
        """生成 YAML 配置模板"""
        return f"""# Embryo Agent 配置文件
# 位置: ~/.embryo/config.yaml

llm:
  provider: openai
  model: {self.llm.model}
  # api_key: "..."  # 建议用环境变量 OPENAI_API_KEY
  # base_url: "https://..."
  temperature: {self.llm.temperature}
  max_tokens: {self.llm.max_tokens}

memory:
  backend: json
  max_entries: {self.memory.max_entries}
  decay_half_life_days: {self.memory.decay_half_life_days}

skills:
  auto_create: {str(self.skills.auto_create).lower()}
  max_skill_tokens: {self.skills.max_skill_tokens}
  max_loaded_skills: {self.skills.max_loaded_skills}

computer_use:
  enabled: {str(self.computer_use.enabled).lower()}
  backend: {self.computer_use.backend}
  max_retries: {self.computer_use.max_retries}
  verify_after_action: {str(self.computer_use.verify_after_action).lower()}

runtime:
  max_iterations: {self.runtime.max_iterations}
  tool_timeout: {self.runtime.tool_timeout}
  auto_save_session: {str(self.runtime.auto_save_session).lower()}

log_level: {self.log_level}
"""
