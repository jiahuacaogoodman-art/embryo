# GUI Agent - 基于多模态界面感知与动态重规划的GUI智能体

> **通过"执行—验证—诊断—重规划"闭环，使 AI 能够在无 API 的传统 GUI 系统中完成可控、可纠错、可审计的自动化操作。**

## 项目简介

本项目面向缺乏 API 接口、难以通过命令行自动化的传统桌面软件或网页系统，构建一套基于 GUI 界面的智能操作代理。系统通过屏幕截图、OCR 文字识别、鼠标坐标、窗口状态和控件结构等信息感知当前界面，由大语言模型生成标准化操作指令，并在每一步操作后进行结果验证和错误诊断，实现"感知—规划—执行—验证—纠错"的闭环式自动操作。

## 技术架构

```
┌─────────────────────────────────────────────────────────┐
│                    GUI Agent 主控调度器                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐        │
│   │ 界面感知  │───▶│ 动作规划  │───▶│ 动作执行  │        │
│   │ 模块     │    │ 模块(LLM)│    │ 模块     │        │
│   └──────────┘    └──────────┘    └──────────┘        │
│        ▲                                │              │
│        │          ┌──────────┐          ▼              │
│        │          │ 错误诊断  │    ┌──────────┐        │
│        │◀─────────│ 重规划   │◀───│ 结果验证  │        │
│        │          │ 模块     │    │ 模块     │        │
│        │          └──────────┘    └──────────┘        │
│                                                         │
├─────────────────────────────────────────────────────────┤
│   ┌──────────┐    ┌──────────┐                         │
│   │ 目标定位  │    │ 隔离环境  │                         │
│   │ 模块     │    │ 管理     │                         │
│   └──────────┘    └──────────┘                         │
└─────────────────────────────────────────────────────────┘
```

## 核心特性

- **多模态感知**：截图 + OCR + 鼠标坐标 + 窗口状态 + 控件树，全面理解界面
- **LLM 驱动规划**：大语言模型生成结构化 JSON 动作指令，每步可审计
- **闭环验证**：每步操作后验证结果，确保操作真的达到预期
- **智能纠错**：失败后自动诊断原因（坐标偏移/加载延迟/焦点错误/弹窗遮挡等），动态调整策略
- **隔离运行**：支持 VNC/Xvfb/RDP 隔离环境，不干扰用户正常操作
- **安全兜底**：风险升高时自动停止并请求人工接管

## 快速开始

### 安装

```bash
# 安装基础依赖
pip install -e .

# 如需网页自动化支持
pip install -e ".[web]"

# 开发环境
pip install -e ".[dev]"
```

### 系统依赖

```bash
# Ubuntu/Debian
sudo apt install tesseract-ocr tesseract-ocr-chi-sim xvfb x11vnc

# macOS
brew install tesseract
```

### 配置

```bash
# 设置 LLM API Key
export OPENAI_API_KEY="your-api-key"

# 可选：自定义模型端点（兼容 OpenAI 接口的服务）
export OPENAI_BASE_URL="https://your-api-endpoint/v1"

# 可选：指定模型
export GUI_AGENT_MODEL="gpt-4o"
```

### 运行

```bash
# 基础用法
gui-agent "登录系统并打开护理记录页面"

# 指定环境和模型
gui-agent --env vnc --model gpt-4o "填写并提交表单"

# 调试模式
gui-agent --log-level DEBUG --max-steps 20 "点击搜索按钮"
```

### 代码调用

```python
from gui_agent.agent import GUIAgent
from gui_agent.config import AgentConfig

# 使用默认配置
agent = GUIAgent()
task = agent.run_task("登录系统并打开老人护理记录页面")

# 自定义配置
config = AgentConfig()
config.llm.model = "gpt-4o"
config.llm.api_key = "your-key"
config.environment.type = "vnc"

agent = GUIAgent(config)
task = agent.run_task("填写表单并提交", max_steps=30)

print(f"任务状态: {task.status.value}")
print(f"执行步骤: {len(task.steps)}")
```

## 项目结构

```
src/gui_agent/
├── __init__.py          # 包入口
├── agent.py             # 主控调度器（闭环流程）
├── config.py            # 全局配置
├── models.py            # 数据模型定义
├── main.py              # 命令行入口
├── perception/          # 界面感知模块
│   ├── screen_capture.py    # 屏幕截图
│   ├── ocr_engine.py        # OCR文字识别
│   ├── element_detector.py  # UI元素检测
│   └── perception_engine.py # 感知引擎总调度
├── locator/             # 目标定位模块
│   └── target_locator.py    # 多策略目标定位
├── planner/             # 动作规划模块
│   └── action_planner.py    # LLM动作规划
├── executor/            # 动作执行模块
│   └── action_executor.py   # GUI操作执行
├── verifier/            # 结果验证模块
│   └── result_verifier.py   # 多维度验证
├── replanner/           # 错误诊断与重规划
│   ├── error_diagnoser.py   # 错误诊断
│   └── replanner.py         # 动态重规划
└── environment/         # 隔离运行环境
    └── sandbox_manager.py   # 沙箱管理
```

## 技术路线

详见 [docs/technical_route.md](docs/technical_route.md)

### 核心流程

```
用户任务输入 → 界面感知 → 目标定位 → 动作规划(LLM)
    → 动作执行 → 结果验证 → [成功] → 继续下一步
                          → [失败] → 错误诊断 → 动态重规划 → 重新执行
```

### 错误处理机制

| 失败现象 | 诊断原因 | 重规划策略 |
|---------|---------|-----------|
| 点击后无变化 | 坐标偏移/按钮未加载 | 重新定位，调整坐标或等待 |
| 输入无效 | 输入框未获得焦点 | 先点击输入框，再重新输入 |
| 页面未跳转 | 网络延迟/登录失败 | 延长等待，读取错误提示 |
| 出现弹窗 | 弹窗遮挡操作路径 | 先处理弹窗 |
| OCR未识别目标 | 字体小/背景复杂 | 改用控件树或模板匹配 |
| 连续失败 | 风险升高 | 停止任务并请求人工接管 |

## 支持的操作类型

| 操作 | 说明 |
|------|------|
| `click` | 单击目标位置 |
| `double_click` | 双击目标 |
| `right_click` | 右键点击 |
| `type` | 输入文字（支持中文） |
| `hotkey` | 键盘快捷键 |
| `scroll` | 滚动页面 |
| `wait` | 等待加载 |
| `back` | 返回上一页 |
| `drag` | 拖拽操作 |
| `stop` | 任务完成 |
| `ask_human` | 请求人工接管 |

## 环境要求

- Python >= 3.10
- Tesseract OCR（含中文语言包）
- 显示环境（Xvfb/VNC/本地桌面）

## 许可证

MIT License
