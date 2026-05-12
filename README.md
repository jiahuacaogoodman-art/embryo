# Embryo

**基于多模态界面感知与动态重规划的 GUI 智能体框架**

> AI 不是每步盲问一次模型，而是先分析出完整执行计划，逐步执行，每步验证，失败时动态修改计划。

## 核心能力

- **动态任务规划** — AI 先生成 todo list，执行中根据反馈实时更新（插入步骤/修改策略/重新规划）
- **GUI 操作闭环** — 截图感知 → 目标定位 → 执行操作 → 验证结果 → 失败诊断 → 调整策略
- **跨会话记忆** — 操作经验持久化，同类任务不重复踩坑
- **自动生成 Skill** — 成功的操作序列自动提炼为可复用的工作流文档
- **安全策略引擎** — 危险命令拦截、路径限制、prompt 注入防护

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                          EmbryoAgent                             │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│   TaskPlanner (动态 Todo List)                                    │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │ 1. 截图观察当前界面          [✓]                          │   │
│   │ 2. 输入用户名 admin         [✓]                          │   │
│   │ 2.5 关闭弹窗 (自动插入)     [✓]                          │   │
│   │ 3. 输入密码                 [→ 执行中]                    │   │
│   │ 4. 点击登录                 [ ]                          │   │
│   │ 5. 验证是否成功             [ ]                          │   │
│   └──────────────────────────────────────────────────────────┘   │
│                           ↕ 反馈更新                              │
│   ┌────────────┬────────────┬────────────┬────────────────────┐  │
│   │  Runtime   │  Memory    │  Skills    │  Security          │  │
│   │  AgentLoop │  TF-IDF   │  渐进加载   │  PolicyEngine      │  │
│   │  Compaction│  向量检索   │  自动创建   │  PromptGuard       │  │
│   │  Guardrail │  时间衰减   │  版本管理   │  工具白/黑名单      │  │
│   └────────────┴────────────┴────────────┴────────────────────┘  │
│                                                                  │
│   ┌──────────────────── Tools ────────────────────────────────┐  │
│   │ terminal │ file_ops │ computer_use │ memory │ mcp │ ...   │  │
│   └───────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│   Gateway: CLI │ Web API (FastAPI) │ Telegram Bot │ WebSocket    │
└──────────────────────────────────────────────────────────────────┘
```

## 快速开始

```bash
pip install -e .
export OPENAI_API_KEY="your-key"

# 交互模式
embryo

# 单次执行
embryo "帮我在浏览器中登录XX系统"

# 启动 Web API
embryo serve

# Docker 部署
docker-compose up
```

## 项目结构

```
src/embryo/
├── agent.py              # 主入口，装配所有子系统
├── config.py             # YAML + 环境变量 + 运行时校验
├── main.py               # CLI (REPL / serve / 单次)
├── planner/              # 动态任务规划（核心）
│   ├── planner.py        # 初始规划 + 动态重规划
│   ├── executor.py       # 按 plan 逐步执行 GUI 操作
│   └── models.py         # TaskPlan / PlanStep / StepStatus
├── runtime/
│   ├── agent_loop.py     # ReAct Loop + Plan Mode 双模式
│   ├── session.py        # 会话持久化 (save/load/resume)
│   ├── compaction.py     # 上下文压缩（长对话不爆 token）
│   ├── tool_guardrails.py # 工具循环检测（防死循环）
│   └── credential_pool.py # 多 API Key 轮转
├── memory/
│   ├── store.py          # TF-IDF + 时间衰减 + 去重
│   └── embeddings.py     # 向量嵌入语义检索
├── retrieval/            # 混合检索管线
│   ├── sparse.py         # BM25
│   ├── dense.py          # 向量（3级降级）
│   ├── fusion.py         # RRF 融合
│   └── reranker.py       # LLM 重排序
├── skills/
│   ├── manager.py        # 渐进式加载 + token 预算
│   └── lifecycle.py      # LLM 生成/优化/版本/导入
├── tools/
│   ├── computer_use.py   # GUI 操作（截图验证闭环）
│   ├── terminal.py       # Shell 执行
│   ├── file_ops.py       # 文件 CRUD
│   ├── mcp_client.py     # MCP 协议完整实现
│   └── memory_tools.py   # Agent 主动记忆
├── security/
│   ├── policy.py         # 命令黑名单 + 路径限制
│   └── prompt_guard.py   # 注入检测（10+ 模式）
├── gateway/              # 多通道接入
│   └── channels/         # Web / Telegram / CLI
├── scheduler/            # 定时任务
│   └── cron.py           # once / interval / cron
├── learning/             # 学习循环
│   └── learner.py        # 失败教训 + 用户纠正 + Skill 生成
└── logging.py            # JSON 结构化日志
```

## 核心设计：动态任务规划

普通 Agent（如大多数 ReAct 实现）是**响应式**的——每步问一次模型"下一步干啥"。

Embryo 是**规划式**的：

```
用户: "登录系统并导出张三的记录"
          ↓
TaskPlanner → LLM 生成完整 todo list (10步)
          ↓
PlanExecutor 逐步执行:
  步骤3 失败（弹窗遮挡）
    → Planner 自动插入 "关闭弹窗" 步骤
    → 重新执行
  步骤5 失败（验证不通过）
    → 重试 2 次仍失败
    → Planner 调 LLM 重规划后续步骤
    → 生成新的 5 步替代方案
          ↓
任务完成 → Memory 存经验 → 自动生成 Skill
```

下次遇到同类系统，Agent 加载 Skill 直接跳过踩坑阶段。

## GUI 操作验证闭环

每次点击/输入后不是"点完就算"，而是：

1. 操作前截图 hash
2. 执行操作
3. 操作后截图 hash 对比
4. 如果无变化 → 自动重试一次
5. 仍无变化 → 返回诊断信息（坐标偏移/元素未加载/焦点错误）
6. Planner 根据诊断修改计划

## 记忆系统

| 类别 | 说明 |
|------|------|
| preference | 用户偏好 |
| environment | 环境信息 |
| lesson | 操作经验教训 |
| project | 项目知识 |
| fact | 事实 |
| correction | 用户纠正 |

检索方式：TF-IDF (中文 bigram) + 时间指数衰减 + 可选向量嵌入。

## 安全机制

- 命令黑名单：`rm -rf /`、`mkfs`、fork bomb 等绝对禁止
- 危险命令确认：`sudo`、`git push --force`、`drop database` 需要确认
- 路径限制：可配置 allowed_paths
- Prompt 注入防护：10+ 种注入模式检测 + 隐藏 Unicode 检测

## 环境要求

- Python >= 3.10
- OpenAI 兼容 API（必需）
- Tesseract OCR + pyautogui（GUI 操作时需要）

## License

MIT
