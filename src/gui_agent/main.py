"""GUI Agent 命令行入口

使用示例：
    gui-agent "登录系统并打开护理记录页面"
    gui-agent --config config.yaml "提交表单"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loguru import logger

from .agent import GUIAgent
from .config import AgentConfig


def setup_logging(level: str = "INFO", log_file: str = None):
    """配置日志"""
    logger.remove()  # 移除默认处理器

    # 控制台输出
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{module}</cyan> - {message}",
    )

    # 文件日志
    if log_file:
        logger.add(
            log_file,
            level="DEBUG",
            rotation="10 MB",
            retention="7 days",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {module}:{function}:{line} - {message}",
        )


def load_config(config_path: str = None) -> AgentConfig:
    """加载配置

    优先级: 命令行参数 > 配置文件 > 环境变量 > 默认值
    """
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            data = json.load(f)
            return AgentConfig(**data)

    # 尝试从环境变量加载关键配置
    import os
    config = AgentConfig()

    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        config.llm.api_key = api_key

    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        config.llm.base_url = base_url

    model = os.environ.get("GUI_AGENT_MODEL")
    if model:
        config.llm.model = model

    return config


def print_task_report(task):
    """打印任务执行报告"""
    print("\n" + "=" * 60)
    print(f"  任务报告")
    print("=" * 60)
    print(f"  任务ID: {task.task_id}")
    print(f"  描  述: {task.description}")
    print(f"  状  态: {task.status.value}")
    print(f"  步骤数: {len(task.steps)}")
    print(f"  重试数: {task.total_retries}")

    if task.error_log:
        print(f"  错误日志:")
        for err in task.error_log:
            print(f"    - {err}")

    print("-" * 60)
    print("  执行步骤:")
    for step in task.steps:
        status_icon = "✓" if (
            step.verification and step.verification.status.value == "success"
        ) else "○"
        print(
            f"    {status_icon} [{step.step_index}] "
            f"{step.action.action_type.value} '{step.action.target}' "
            f"({step.duration:.1f}s)"
        )
        if step.action.reason:
            print(f"        原因: {step.action.reason}")

    print("=" * 60)


def main():
    """命令行主入口"""
    parser = argparse.ArgumentParser(
        description="GUI Agent - 基于多模态界面感知与动态重规划的GUI智能体",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  gui-agent "登录系统"
  gui-agent --model gpt-4o "填写并提交表单"
  gui-agent --env vnc --max-steps 30 "打开浏览器访问百度"
        """,
    )

    parser.add_argument("task", help="任务描述（自然语言）")
    parser.add_argument("--config", "-c", help="配置文件路径 (JSON)")
    parser.add_argument("--model", "-m", help="LLM模型名称")
    parser.add_argument("--env", choices=["vnc", "xvfb", "rdp", "local"], default="local",
                        help="运行环境类型 (默认: local)")
    parser.add_argument("--max-steps", type=int, default=50, help="最大步骤数 (默认: 50)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        default="INFO", help="日志级别")
    parser.add_argument("--log-file", help="日志文件路径")
    parser.add_argument("--no-screenshots", action="store_true", help="不保存截图")

    args = parser.parse_args()

    # 配置日志
    setup_logging(level=args.log_level, log_file=args.log_file)

    # 加载配置
    config = load_config(args.config)

    # 命令行参数覆盖
    if args.model:
        config.llm.model = args.model
    config.environment.type = args.env
    config.save_screenshots = not args.no_screenshots

    # 创建并运行 Agent
    logger.info(f"GUI Agent v0.1.0")
    logger.info(f"任务: {args.task}")
    logger.info(f"模型: {config.llm.model}")
    logger.info(f"环境: {config.environment.type}")

    agent = GUIAgent(config)
    task = agent.run_task(args.task, max_steps=args.max_steps)

    # 打印报告
    print_task_report(task)

    # 返回状态码
    if task.status.value == "completed":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
