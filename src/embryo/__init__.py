"""
Embryo - 自主 AI Agent 框架

参考 OpenClaw (Gateway + Agent Runtime + Skills) 和 Hermes Agent (学习循环 + 持久记忆 + Computer Use)
实现一个能自我改进、具备 GUI 操作能力的个人 AI 智能体。

核心架构：
- Gateway: 消息路由、会话管理、策略引擎
- Runtime: ReAct Agent Loop (observe → plan → act → reflect)
- Skills: Markdown 定义的可复用工作流，渐进式加载
- Memory: 跨会话持久记忆（偏好、环境、教训）
- Tools: 可扩展工具系统 (Computer Use / Terminal / Web / File / MCP)
- Learning: 内建学习循环，从经验中自动生成和优化 Skills
"""

__version__ = "0.2.0"
