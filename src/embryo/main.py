"""命令行入口

用法:
    embryo                        # REPL 交互模式
    embryo "帮我看下当前目录"       # 单次执行
    embryo serve                  # 启动 Web API 服务
    embryo serve --telegram       # 同时启动 Telegram Bot
    embryo --model gpt-4o         # 指定模型
"""

from __future__ import annotations

import argparse
import sys

from .config import Config


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("task", nargs="?", default="", help="要执行的任务（空=进入 REPL，'serve'=启动服务）")
    parser.add_argument("--model", "-m", help="LLM 模型名称")
    parser.add_argument("--no-gui", action="store_true", help="禁用 Computer Use")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    parser.add_argument("--port", type=int, default=8642, help="Web API 端口")
    parser.add_argument("--telegram", action="store_true", help="启用 Telegram Bot")
    parser.add_argument("--config", "-c", help="配置文件路径")

    args = parser.parse_args()

    # 加载配置
    from pathlib import Path
    if args.config:
        config = Config.from_yaml(Path(args.config))
    else:
        config_path = Path.home() / ".embryo" / "config.yaml"
        if config_path.exists():
            config = Config.from_yaml(config_path)
        else:
            config = Config()

    config.log_level = args.log_level
    if args.model:
        config.llm.model = args.model
    if args.no_gui:
        config.computer_use.enabled = False

    # 创建 Agent
    from .agent import EmbryoAgent
    agent = EmbryoAgent(config)

    if args.task == "serve":
        # Gateway 服务模式
        _run_server(agent, port=args.port, telegram=args.telegram)
    elif args.task:
        # 单次执行模式
        response = agent.chat(args.task)
        print(response)
    else:
        # REPL 模式
        agent.run_repl()


def _run_server(agent, port: int = 8642, telegram: bool = False):
    """启动 Gateway 服务（Web API + 可选 Telegram）"""
    import asyncio
    from .gateway import GatewayRouter
    from .gateway.channels.web import WebChannel

    router = GatewayRouter(agent)

    # Web API
    web = WebChannel(agent, host="0.0.0.0", port=port)
    router.add_channel(web)

    # Telegram（如果配置了 token）
    if telegram:
        import os
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if token:
            from .gateway.channels.telegram import TelegramChannel
            tg = TelegramChannel(agent, token=token)
            router.add_channel(tg)
            print(f"Telegram Bot 已启用")
        else:
            print("Warning: --telegram 已指定但 TELEGRAM_BOT_TOKEN 环境变量未设置")

    print(f"Embryo Agent 服务启动中...")
    print(f"  Web API: http://0.0.0.0:{port}")
    print(f"  API 文档: http://0.0.0.0:{port}/docs")
    print(f"  通道: {list(router._channels.keys())}")

    # 如果只有 Web 通道，直接用 uvicorn 同步启动（更稳定）
    if len(router._channels) == 1 and "web" in router._channels:
        web.run_sync()
    else:
        router.run()


if __name__ == "__main__":
    main()
