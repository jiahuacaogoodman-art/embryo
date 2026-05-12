"""
Embryo - 多后端 GUI Computer-Use 执行层

定位：不是单一 pyautogui 脚本，而是多后端 GUI Agent 执行器。

核心架构：
- action/       多后端 GUI 执行（ComputerBackend ABC + PyAutoGUI/Playwright/VNC）
- perception/   统一感知层（Observation + OCR + Accessibility + DOM + TargetResolver）
- planning/     结构化规划（Pydantic schema + JSON repair + validation pipeline）
- verification/ 规则化验证 + 失败分类（Verifier + FailureClassifier）
- security/     能力授权模型 + API 鉴权 + 审计（CapabilityChecker + TokenValidator）
- memory/       三层记忆（User/Task/Skill scope + 自主回忆决策）
- skills/       可复用工作流 + 质量门控（SkillValidator lifecycle）
- core/         Trace 记录（plan.json + steps.jsonl + screenshots）
- server/       MCP Server + 三种执行模式（tool/plan/supervised）
- benchmarks/   标准化 GUI benchmark（10 tasks + runner + metrics）
- planner/      旧版执行器集成（TaskPlanner + PlanExecutor）
- runtime/      Agent Loop + Session + Compaction
- gateway/      多通道（Web API + Telegram）
- tools/        工具注册表 + 适配器
"""

__version__ = "0.3.0"
