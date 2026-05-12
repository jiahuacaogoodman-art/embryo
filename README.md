# Embryo

**自主 AI Agent 框架 — 参考 OpenClaw + Hermes Agent 架构**

一个能持久运行、跨会话记忆、自我改进、具备 GUI 操作能力的个人 AI 智能体。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                      EmbryoAgent                            │
├──────────┬───────────┬────────────┬─────────────────────────┤
│ Runtime  │  Skills   │  Memory    │  Learning               │
│          │           │            │                         │
│ AgentLoop│ SKILL.md  │ 持久存储    │ 失败教训提取            │
│ (ReAct)  │ 渐进加载   │ 关键词检索  │ 用户纠正识别            │
│ Session  │ 自动创建   │ 淘汰策略    │ Skill 自动生成          │
├──────────┴───────────┴────────────┴─────────────────────────┤
│                         Tools                               │
│                                                             │
│  terminal    file_ops    computer_use    memory    mcp      │
│  (shell)     (R/W/Edit)  (GUI操作)       (存/取)   (扩展)    │
└─────────────────────────────────────────────────────────────┘
```

## 核心能力

| 能力 | 说明 | 来源 |
|------|------|------|
| ReAct Loop | LLM → tool call → execute → inject result → loop | OpenClaw |
| Skills | Markdown 工作流文档，渐进式加载 | OpenClaw + Hermes |
| 持久记忆 | 跨会话记忆偏好/教训/环境/项目信息 | Hermes |
| Computer Use | 截图+OCR+点击+输入，操作 GUI 界面 | Hermes |
| 自我改进 | 从失败学习、自动生成 Skill | Hermes |
| MCP 扩展 | 连接外部 MCP Server 扩展工具 | OpenClaw |

## 快速开始

```bash
# 安装（最小依赖，只需 openai）
pip install -e .

# 如需 GUI 操作能力
pip install -e ".[gui]"

# 设置 API Key
export OPENAI_API_KEY="your-key"

# REPL 模式
embryo

# 单次执行
embryo "帮我看下当前目录结构"
```

## 代码调用

```python
from embryo.agent import EmbryoAgent
from embryo.config import Config

# 默认配置
agent = EmbryoAgent()
response = agent.chat("列出当前目录的文件")
print(response)

# 自定义配置
config = Config()
config.llm.model = "gpt-4o"
config.computer_use.enabled = True

agent = EmbryoAgent(config)
agent.run_repl()  # 交互模式
```

## 项目结构

```
src/embryo/
├── __init__.py         # 包入口
├── agent.py            # 主入口 - 组装所有子系统
├── config.py           # 全局配置（dataclass）
├── main.py             # CLI 入口
├── runtime/            # Agent Runtime
│   ├── agent_loop.py   # ReAct 循环（LLM ↔ Tool 交替执行）
│   └── session.py      # 会话管理（消息历史、状态）
├── skills/             # Skills 系统
│   └── manager.py      # Skill 索引、匹配、加载、创建
├── memory/             # 持久记忆
│   └── store.py        # 存储/检索/淘汰（JSON 后端）
├── learning/           # 学习循环
│   └── learner.py      # 从经验中提取教训和 Skill
└── tools/              # 工具集
    ├── registry.py     # 工具注册表 + OpenAI schema 导出
    ├── terminal.py     # Shell 命令执行
    ├── file_ops.py     # 文件 读/写/编辑/列目录
    ├── computer_use.py # GUI 操作（截图/点击/输入/OCR）
    ├── memory_tools.py # Agent 主动管理记忆
    └── mcp_client.py   # MCP Server 连接

skills/                 # 内置 Skill 文档
├── gui-login/SKILL.md
└── form-filling/SKILL.md
```

## Skills 系统

Skills 是 Markdown 文件，不是代码。它告诉 Agent "如何完成某类任务"：

```markdown
---
name: "GUI 系统登录"
description: "通过 GUI 操作完成系统登录"
tags: [gui, login, computer_use]
triggers: [登录, login]
---

# GUI 系统登录

## 执行步骤
1. 截图观察当前界面
2. OCR 识别文字，确认是登录页
3. 找到用户名输入框并输入
4. 找到密码输入框并输入
5. 点击登录按钮
6. 截图验证是否成功
```

Agent 会在收到相关任务时自动加载匹配的 Skill 到上下文中。

## 记忆系统

记忆有 6 个类别：

| 类别 | 说明 | 示例 |
|------|------|------|
| preference | 用户偏好 | "用户喜欢简洁的代码风格" |
| environment | 环境信息 | "系统是 Ubuntu 22.04" |
| lesson | 经验教训 | "登录前需要等待页面加载" |
| project | 项目知识 | "项目用 FastAPI + PostgreSQL" |
| fact | 一般事实 | "用户名是 admin" |
| correction | 用户纠正 | "不要用 print，用 logger" |

Agent 可以通过 `remember` 工具主动存储记忆，也会在会话结束后自动提取教训。

## 工具系统

内置 11 个工具：

| 工具 | 功能 |
|------|------|
| `terminal` | 执行 shell 命令 |
| `read_file` | 读取文件 |
| `write_file` | 创建/写入文件 |
| `edit_file` | 精确编辑文件 |
| `list_directory` | 列目录 |
| `remember` | 存储记忆 |
| `recall` | 检索记忆 |
| `screenshot` | 截屏 |
| `click` | 鼠标点击 |
| `type_text` | 键盘输入 |
| `find_text_on_screen` | OCR 文字定位 |

通过 MCP 配置可扩展更多工具。

## 环境要求

- Python >= 3.10
- OpenAI 兼容 API（必需）
- Tesseract OCR + pyautogui（GUI 操作时需要）
