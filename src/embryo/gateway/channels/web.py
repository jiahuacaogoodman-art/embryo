"""Web HTTP API 通道 — FastAPI 实现

提供 RESTful API 供前端或第三方调用：
- POST /api/chat      发送消息并获取回复
- GET  /api/sessions  获取会话列表
- GET  /api/memory    获取记忆
- GET  /api/skills    获取 Skills 列表
- GET  /api/status    Agent 状态
- WS   /ws/chat       WebSocket 实时对话（流式响应）
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional, TYPE_CHECKING

from .base import Channel, IncomingMessage, OutgoingMessage

if TYPE_CHECKING:
    from ...agent import EmbryoAgent


class WebChannel(Channel):
    """Web HTTP API 通道

    使用 FastAPI 实现，支持同步 REST 和 WebSocket 流式。
    """

    def __init__(self, agent: "EmbryoAgent", host: str = "0.0.0.0", port: int = 8642):
        super().__init__("web")
        self.agent = agent
        self.host = host
        self.port = port
        self._app = None
        self._server = None
        self._running = False

    def create_app(self):
        """创建 FastAPI 应用"""
        try:
            from fastapi import FastAPI, WebSocket, WebSocketDisconnect
            from fastapi.middleware.cors import CORSMiddleware
            from pydantic import BaseModel
        except ImportError:
            raise ImportError("Web 通道需要 fastapi 和 uvicorn: pip install fastapi uvicorn")

        app = FastAPI(
            title="Embryo Agent API",
            version="0.2.0",
            description="自主 AI Agent HTTP 接口",
        )

        # CORS
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # === 数据模型 ===

        class ChatRequest(BaseModel):
            message: str
            session_id: Optional[str] = None
            user_id: str = "web_user"

        class ChatResponse(BaseModel):
            response: str
            session_id: str
            steps: int
            tokens: int

        # === 路由 ===

        @app.post("/api/chat", response_model=ChatResponse)
        async def chat(req: ChatRequest):
            """发送消息并获取回复"""
            # 如果指定了 session_id，尝试恢复
            if req.session_id:
                filepath = self.agent.config.runtime.sessions_dir / f"{req.session_id}.json"
                if filepath.exists():
                    from ...runtime.session import Session, SessionStatus
                    self.agent._session = Session.load(filepath)
                    self.agent._session.status = SessionStatus.ACTIVE

            response_text = self.agent.chat(req.message)
            session = self.agent._session

            return ChatResponse(
                response=response_text,
                session_id=session.id if session else "",
                steps=session.total_steps if session else 0,
                tokens=session.total_tokens if session else 0,
            )

        @app.post("/api/new_session")
        async def new_session():
            """创建新会话"""
            self.agent.new_session()
            return {"status": "ok", "message": "新会话已创建"}

        @app.get("/api/sessions")
        async def list_sessions():
            """获取会话历史"""
            from ...runtime.session import Session
            sessions = Session.list_sessions(self.agent.config.runtime.sessions_dir)
            return {"sessions": sessions[:50]}

        @app.get("/api/memory")
        async def get_memory():
            """获取记忆列表"""
            entries = self.agent.memory.recall_all()
            return {
                "count": len(entries),
                "entries": [
                    {
                        "id": e.id,
                        "category": e.category,
                        "content": e.content,
                        "importance": e.importance,
                        "access_count": e.access_count,
                    }
                    for e in entries[-50:]
                ],
            }

        @app.get("/api/skills")
        async def get_skills():
            """获取 Skills 列表"""
            skills = self.agent.skills.list_skills()
            return {
                "count": len(skills),
                "skills": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "tags": s.tags,
                        "token_estimate": s.token_estimate,
                        "use_count": s.use_count,
                    }
                    for s in skills
                ],
            }

        @app.get("/api/tools")
        async def get_tools():
            """获取工具列表"""
            tools = self.agent.tools.list_tools()
            return {
                "count": len(tools),
                "tools": [
                    {
                        "name": name,
                        "description": self.agent.tools.get_tool(name).description[:100],
                        "category": self.agent.tools.get_tool(name).category,
                    }
                    for name in tools
                ],
            }

        @app.get("/api/status")
        async def get_status():
            """Agent 状态"""
            return {
                "version": "0.2.0",
                "model": self.agent.config.llm.model,
                "tools_count": self.agent.tools.count,
                "memory_count": self.agent.memory.count,
                "skills_count": len(self.agent.skills.list_skills()),
                "current_session": self.agent._session.id if self.agent._session else None,
            }

        @app.websocket("/ws/chat")
        async def websocket_chat(ws: WebSocket):
            """WebSocket 实时对话（支持流式响应）"""
            await ws.accept()
            try:
                while True:
                    data = await ws.receive_json()
                    message = data.get("message", "")
                    if not message:
                        continue

                    # 流式回调
                    async def stream_callback(chunk: str):
                        await ws.send_json({"type": "stream", "content": chunk})

                    # 执行（同步，在线程中运行）
                    response = await asyncio.to_thread(self.agent.chat, message)

                    await ws.send_json({
                        "type": "complete",
                        "content": response,
                        "session_id": self.agent._session.id if self.agent._session else "",
                    })

            except WebSocketDisconnect:
                pass
            except Exception as e:
                await ws.close(code=1011, reason=str(e)[:100])

        self._app = app
        return app

    async def start(self):
        """启动 Web 服务"""
        import uvicorn

        app = self.create_app()
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="info")
        self._server = uvicorn.Server(config)
        self._running = True
        await self._server.serve()

    async def stop(self):
        """停止 Web 服务"""
        if self._server:
            self._server.should_exit = True
        self._running = False

    async def send(self, message: OutgoingMessage):
        """Web 通道不主动推送，通过 API 返回"""
        pass

    @property
    def is_running(self) -> bool:
        return self._running

    def run_sync(self):
        """同步方式启动（阻塞）"""
        import uvicorn
        app = self.create_app()
        uvicorn.run(app, host=self.host, port=self.port)
