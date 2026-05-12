"""全局配置模块"""

from pydantic import BaseModel, Field
from typing import Optional


class LLMConfig(BaseModel):
    """大语言模型配置"""
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.1
    max_tokens: int = 4096


class PerceptionConfig(BaseModel):
    """界面感知配置"""
    screenshot_interval: float = 0.5  # 截图间隔（秒）
    ocr_language: str = "chi_sim+eng"  # OCR语言
    ocr_confidence_threshold: float = 0.6  # OCR置信度阈值
    template_match_threshold: float = 0.8  # 模板匹配阈值


class ExecutionConfig(BaseModel):
    """动作执行配置"""
    click_delay: float = 0.3  # 点击后等待时间
    type_interval: float = 0.05  # 输入字符间隔
    scroll_amount: int = 3  # 滚动量
    max_wait_time: float = 30.0  # 最大等待时间
    action_timeout: float = 10.0  # 单步操作超时


class VerificationConfig(BaseModel):
    """结果验证配置"""
    diff_threshold: float = 0.05  # 截图差分阈值（变化比例）
    verify_delay: float = 1.0  # 验证前等待时间
    max_verify_retries: int = 3  # 最大验证重试次数


class ReplanningConfig(BaseModel):
    """重规划配置"""
    max_retries: int = 3  # 单步最大重试次数
    max_consecutive_failures: int = 5  # 连续失败上限
    coordinate_adjustment: int = 10  # 坐标微调像素值
    wait_multiplier: float = 1.5  # 等待时间倍增因子


class EnvironmentConfig(BaseModel):
    """隔离环境配置"""
    type: str = "vnc"  # vnc / xvfb / rdp / local
    host: str = "localhost"
    port: int = 5900
    password: Optional[str] = None
    screen_width: int = 1920
    screen_height: int = 1080


class AgentConfig(BaseModel):
    """GUI Agent 总配置"""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    perception: PerceptionConfig = Field(default_factory=PerceptionConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    replanning: ReplanningConfig = Field(default_factory=ReplanningConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    log_level: str = "INFO"
    save_screenshots: bool = True
    screenshot_dir: str = "./screenshots"
