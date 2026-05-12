"""Embryo Agent - 主入口

将所有子系统装配在一起：
- Runtime (Agent Loop)
- Tools (Terminal / File / Computer Use / Memory / MCP)
- Skills (Markdown 工作流)
- Memory (持久记忆)
- Learning (自我改进)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import Config
from .learning.learner import LearningEngine
from .logging import configure as configure_logging, get_logger
from .memory.store import MemoryStore
from .runtime.agent_loop import AgentLoop
from .runtime.session import Session, SessionStatus
from .skills.manager import SkillManager
from .tools import ToolRegistry
from .tools.computer_use import (
    CLICK_TOOL, FIND_TEXT_TOOL, HOTKEY_TOOL, OCR_SCREEN_TOOL,
    PRESS_KEY_TOOL, SCREENSHOT_TOOL, SCROLL_TOOL, TYPE_TEXT_TOOL,
)
from .tools.file_ops import EDIT_FILE_TOOL, LIST_DIR_TOOL, READ_FILE_TOOL, WRITE_FILE_TOOL
from .tools.memory_tools import FORGET_TOOL, RECALL_TOOL, REMEMBER_TOOL, bind_memory_store
from .tools.terminal import TERMINAL_TOOL

logger = get_logger(__name__)


class EmbryoAgent:
    """Embryo 智能体

    生命周期：
    1. __init__: 加载配置、初始化各子系统
    2. chat(): 单轮对话（用户输入 → Agent 执行 → 返回结果）
    3. run(): 持续运行模式（REPL 或 Gateway）

    使用示例:
        agent = EmbryoAgent()
        result = agent.chat("帮我看一下当前目录有什么文件")
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.config.ensure_dirs()

        # 配置日志
        configure_logging(
            level=self.config.log_level,
            log_file=self.config.log_file,
        )

        # 配置校验
        issues = self.config.validate()
        if issues:
            logger.warning("config_issues", issues=issues)

        # 初始化子系统
        self.memory = MemoryStore(
            storage_path=self.config.memory.storage_path,
            max_entries=self.config.memory.max_entries,
        )

        self.skills = SkillManager(
            skills_dir=self.config.skills.skills_dir,
            bundled_dir=self.config.skills.bundled_skills_dir,
        )

        self.tools = ToolRegistry()
        self._register_tools()

        self.loop = AgentLoop(
            config=self.config,
            tool_registry=self.tools,
            skill_manager=self.skills,
            memory_store=self.memory,
        )

        self.learner = LearningEngine(
            memory=self.memory,
            skills=self.skills,
        )

        # 绑定记忆工具的存储实例
        bind_memory_store(self.memory)

        # 当前会话
        self._session: Optional[Session] = None

        logger.info(
            "agent_initialized",
            model=self.config.llm.model,
            tools=self.tools.count,
            memories=self.memory.count,
            skills=len(self.skills.list_skills()),
        )

    def chat(self, user_input: str) -> str:
        """单轮对话

        Args:
            user_input: 用户输入

        Returns:
            Agent 的最终回复
        """
        # 延续当前会话或新建
        if self._session is None or self._session.status != SessionStatus.ACTIVE:
            self._session = Session()
            self._session.context["task"] = user_input

        session = self.loop.run(user_input, self._session)

        # 学习
        self.learner.learn_from_session(session)

        # 持久化会话
        if self.config.runtime.auto_save_session:
            session.save(self.config.runtime.sessions_dir)

        # 提取最终回复
        assistant_msgs = [m for m in session.messages if m.role == "assistant"]
        if assistant_msgs:
            return assistant_msgs[-1].content
        return "(无回复)"

    def new_session(self):
        """开始新会话"""
        self._session = None

    def run_repl(self):
        """REPL 模式 - 交互式命令行"""
        print("Embryo Agent v0.2.0")
        print(f"模型: {self.config.llm.model}")
        print(f"记忆: {self.memory.count} 条")
        print(f"Skills: {len(self.skills.list_skills())} 个")
        print(f"工具: {self.tools.count} 个")
        print("输入 /quit 退出, /new 新会话, /memory 查看记忆, /skills 查看技能")
        print("     /history 查看会话历史, /resume <id> 恢复会话")
        print("-" * 60)

        while True:
            try:
                user_input = input("\n[You] ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nBye!")
                break

            if not user_input:
                continue

            # 命令处理
            if user_input.startswith("/"):
                self._handle_command(user_input)
                continue

            # 正常对话
            response = self.chat(user_input)
            print(f"\n[Embryo] {response}")

    def _handle_command(self, cmd: str):
        """处理 REPL 命令"""
        if cmd == "/quit":
            raise SystemExit
        elif cmd == "/new":
            self.new_session()
            print("(新会话)")
        elif cmd == "/memory":
            entries = self.memory.recall_all()
            if not entries:
                print("(记忆为空)")
            else:
                print(f"共 {len(entries)} 条记忆:")
                for e in entries[-10:]:
                    print(f"  [{e.category}] {e.content[:80]}")
        elif cmd == "/skills":
            skills = self.skills.list_skills()
            if not skills:
                print("(无 Skill)")
            else:
                print(f"共 {len(skills)} 个 Skill:")
                for s in skills:
                    print(f"  [{s.name}] {s.description}")
        elif cmd == "/tools":
            tools = self.tools.list_tools()
            print(f"共 {len(tools)} 个工具:")
            for name in tools:
                t = self.tools.get_tool(name)
                print(f"  {name}: {t.description[:60] if t else ''}")
        elif cmd == "/history":
            sessions = Session.list_sessions(self.config.runtime.sessions_dir)
            if not sessions:
                print("(无历史会话)")
            else:
                print(f"最近 {min(len(sessions), 10)} 个会话:")
                for s in sessions[:10]:
                    import time as _time
                    ts = _time.strftime("%m-%d %H:%M", _time.localtime(s["created_at"]))
                    print(f"  [{s['id']}] {ts} | {s['status']} | {s['title'][:50]}")
        elif cmd.startswith("/resume "):
            session_id = cmd.split(" ", 1)[1].strip()
            filepath = self.config.runtime.sessions_dir / f"{session_id}.json"
            if filepath.exists():
                self._session = Session.load(filepath)
                self._session.status = SessionStatus.ACTIVE
                print(f"已恢复会话 {session_id}")
            else:
                print(f"会话 {session_id} 不存在")
        elif cmd.startswith("/forget "):
            entry_id = cmd.split(" ", 1)[1]
            self.memory.forget(entry_id)
            print(f"已删除记忆: {entry_id}")
        else:
            print(f"未知命令: {cmd}")
            print("可用: /quit /new /memory /skills /tools /history /resume <id> /forget <id>")

    def _register_tools(self):
        """注册所有内置工具"""
        from .tools.registry import Tool

        # 终端
        self.tools.register(TERMINAL_TOOL)

        # 文件操作
        self.tools.register(READ_FILE_TOOL)
        self.tools.register(WRITE_FILE_TOOL)
        self.tools.register(EDIT_FILE_TOOL)
        self.tools.register(LIST_DIR_TOOL)

        # 记忆
        self.tools.register(REMEMBER_TOOL)
        self.tools.register(RECALL_TOOL)
        self.tools.register(FORGET_TOOL)

        # Skill 按需加载工具（渐进式 disclosure）
        self.tools.register(Tool(
            name="load_skill",
            description="加载指定 Skill 的完整内容。当系统提示某 Skill 已截断时使用。",
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "Skill 名称"},
                },
                "required": ["skill_name"],
            },
            handler=lambda skill_name: self.skills.load_skill_full(skill_name),
            category="skills",
        ))

        # Computer Use (GUI)
        if self.config.computer_use.enabled:
            self.tools.register(SCREENSHOT_TOOL)
            self.tools.register(CLICK_TOOL)
            self.tools.register(TYPE_TEXT_TOOL)
            self.tools.register(HOTKEY_TOOL)
            self.tools.register(PRESS_KEY_TOOL)
            self.tools.register(SCROLL_TOOL)
            self.tools.register(OCR_SCREEN_TOOL)
            self.tools.register(FIND_TEXT_TOOL)

        # MCP (如果配置了)
        mcp_config = self.config.data_dir / "mcp.json"
        if mcp_config.exists():
            from .tools.mcp_client import MCPClient
            self._mcp = MCPClient(self.tools)
            self._mcp.load_config_file(mcp_config)
            self._mcp.connect_all()
