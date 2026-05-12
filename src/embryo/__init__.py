"""
Embryo - 基于多模态界面感知与动态重规划的 GUI 智能体框架

核心架构：
- TaskPlanner: 动态任务规划（todo list 生成 + 反馈更新 + 重规划）
- Runtime: 双模式执行（通用 ReAct + GUI Plan Mode）
- Skills: Markdown 工作流文档，渐进式加载，自动生成
- Memory: 跨会话持久记忆（TF-IDF / 向量检索 / 时间衰减）
- Tools: 可扩展工具系统（Computer Use / Terminal / File / MCP）
- Security: 策略引擎 + Prompt 注入防护
- Learning: 内建学习循环，从操作经验中自动生成 Skills
"""

__version__ = "0.2.0"
