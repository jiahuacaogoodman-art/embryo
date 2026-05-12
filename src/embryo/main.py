"""命令行入口

用法:
    embryo                     # REPL 交互模式
    embryo "帮我看下当前目录"    # 单次执行
    embryo --model gpt-4o      # 指定模型
"""

from __future__ import annotations

import argparse
import sys

from .config import Config


def main():
    parser = argparse.ArgumentParser(
        description="Embryo - 自主 AI Agent（参考 OpenClaw + Hermes）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("task", nargs="?", default="", help="要执行的任务（空=进入 REPL）")
    parser.add_argument("--model", "-m", help="LLM 模型名称")
    parser.add_argument("--no-gui", action="store_true", help="禁用 Computer Use")
    parser.add_argument("--log-level", default="INFO", help="日志级别")

    args = parser.parse_args()

    # 配置
    config = Config()
    config.log_level = args.log_level
    if args.model:
        config.llm.model = args.model
    if args.no_gui:
        config.computer_use.enabled = False

    # 创建 Agent
    from .agent import EmbryoAgent
    agent = EmbryoAgent(config)

    if args.task:
        # 单次执行模式
        response = agent.chat(args.task)
        print(response)
    else:
        # REPL 模式
        agent.run_repl()


if __name__ == "__main__":
    main()
