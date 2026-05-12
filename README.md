# Embryo

**多后端 GUI Computer-Use 执行层**

Embryo 不是单一 pyautogui 脚本，而是一个多后端 GUI Agent 执行器。外部 Agent（或人类）通过 MCP 协议或 REST API 调用 Embryo 的 GUI 操作能力，Embryo 负责：**感知可靠、动作可靠、验证可靠、安全可靠**。

---

## 架构概览

```
┌─────────────────────────────────────────────────────┐
│                  External Agent / User               │
│            (Hermes / OpenClaw / Claude / Human)       │
└────────────────────────┬────────────────────────────┘
                         │ MCP Tools / REST API
┌────────────────────────▼────────────────────────────┐
│                    Embryo Server                      │
│  ┌─────────┐  ┌─────────┐  ┌──────────────────┐    │
│  │  Tool   │  │  Plan   │  │   Supervised     │    │
│  │  Mode   │  │  Mode   │  │   Mode           │    │
│  └─────────┘  └─────────┘  └──────────────────┘    │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│                   Planning Layer                      │
│  Pydantic Schema → JSON Repair → Validation → Plan   │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│                  Perception Layer                     │
│  Observation = Screenshot + OCR + Accessibility + DOM │
│  TargetResolver: semantic target → coordinates       │
└──────────┬─────────────────────────────┬────────────┘
           │                             │
┌──────────▼──────────┐   ┌─────────────▼────────────┐
│    Action Layer      │   │   Verification Layer     │
│  ComputerBackend ABC │   │  Verifier + Rules        │
│  ├ PyAutoGUI         │   │  FailureClassifier       │
│  ├ Playwright (WIP)  │   │  (12 failure types)      │
│  ├ Accessibility     │   │                          │
│  └ VNC/Remote        │   │                          │
└──────────────────────┘   └──────────────────────────┘
```

---

## 核心模块

| 模块 | 职责 |
|------|------|
| `action/` | 多后端 GUI 执行（ComputerBackend ABC） |
| `perception/` | 统一感知：Observation + Observer + TargetResolver |
| `planning/` | Pydantic plan schema + JSON repair + validation |
| `verification/` | 规则验证器 + 失败分类（不再"默认成功"） |
| `security/` | 能力授权模型 + API Token + Rate Limit + Audit |
| `memory/` | 三层记忆（User/Task/Skill）+ 自主回忆决策 |
| `skills/` | 可复用工作流 + 质量门控（draft→testing→verified） |
| `core/` | 任务 Trace 记录（plan.json + steps.jsonl） |
| `server/` | MCP Server + 三种执行模式 |
| `benchmarks/` | 10 个标准 GUI 测试任务 + Runner |

---

## 安装

```bash
pip install -e .            # 基础（仅 openai + pydantic）
pip install -e ".[gui]"     # + pyautogui / pytesseract / pillow
pip install -e ".[browser]" # + playwright
pip install -e ".[full]"    # 全部依赖
pip install -e ".[dev]"     # + pytest / ruff
```

---

## 快速开始

### Tool Mode（外部 Agent 直接调用）

```python
from embryo.server import EmbryoMCPServer

server = EmbryoMCPServer()
server.setup()

# 观察当前界面
obs = server.observe()
print(obs["ocr_text"])

# 语义化点击（不需要坐标）
result = server.click(target={"type": "text", "value": "登录"})

# 验证结果
check = server.verify(type="text_visible", target="欢迎")
print(check["passed"])

server.teardown()
```

### Plan Mode（自主规划执行）

```python
result = server.execute_plan(task="打开浏览器，登录 demo.example.com")
```

### Supervised Mode（高风险操作需确认）

```yaml
# config
runtime:
  mode: supervised
  require_confirmation_for:
    - submit
    - payment
    - delete
```

---

## MCP 工具列表

| 工具 | 说明 |
|------|------|
| `embryo.observe` | 截图 + OCR 获取界面状态 |
| `embryo.click` | 语义化点击（text/role/label/coordinate） |
| `embryo.type_text` | 输入文字 |
| `embryo.hotkey` | 快捷键 |
| `embryo.scroll` | 滚动 |
| `embryo.find_text` | 查找文字位置 |
| `embryo.verify` | 验证界面状态 |
| `embryo.execute_plan` | 提交任务自主执行 |
| `embryo.get_trace` | 获取执行 trace |

---

## 关键设计决策

### 1. 多后端而非绑定 pyautogui

```python
class ComputerBackend(ABC):
    def screenshot(...) -> ActionResult: ...
    def click(...) -> ActionResult: ...
    def type_text(...) -> ActionResult: ...
    def hotkey(...) -> ActionResult: ...
```

PyAutoGUI 是 fallback，浏览器走 Playwright DOM，桌面走 Accessibility API。

### 2. 语义目标而非坐标

Planner 输出 `{"type": "text", "value": "登录"}`，TargetResolver 负责解析为坐标。

### 3. 不默认成功

每步必须有 VerificationRule。没有规则 = SKIPPED（不是 PASSED）。

### 4. 精确失败分类

12 种 FailureType（target_not_found / popup_blocking / page_loading / ...），
replan prompt 不再空猜。

### 5. Skill 要过质量门

`draft → testing → verified`，至少 N 次通过才能正式使用。

---

## 目录结构

```
src/embryo/
  action/           # ComputerBackend ABC + PyAutoGUIBackend
  perception/       # Observation + Observer + TargetResolver
  planning/         # Pydantic schema + repair pipeline
  verification/     # Verifier + FailureClassifier
  security/         # CapabilityChecker + Auth + Audit
  memory/           # 三层记忆 + MemoryRetriever
  skills/           # SkillManager + SkillValidator
  core/             # TaskTrace
  server/           # MCP Server + ExecutionMode
  benchmarks/       # BenchmarkRunner + 10 tasks
  planner/          # TaskPlanner + PlanExecutor (legacy integration)
  runtime/          # AgentLoop + Session
  gateway/          # Web API + Telegram channel
  tools/            # ToolRegistry + adapters
```

---

## Benchmark

```python
from embryo.benchmarks import BenchmarkRunner, BENCHMARK_TASKS

runner = BenchmarkRunner(execute_fn=my_executor)
result = runner.run()
print(result.report())
```

指标：success_rate / avg_steps / avg_retries / avg_replans / avg_duration / human_interventions

---

## 下一步

- [ ] 实现 PlaywrightBackend（浏览器 DOM 操作）
- [ ] 实现 AccessibilityBackend（Windows UIAutomation / macOS AX）
- [ ] 实现 RemoteVNCBackend（Docker / 远程桌面）
- [ ] 完整集成 Planner + Executor + Trace
- [ ] 完善 Benchmark 并建立 CI 回归
- [ ] Web UI 支持 Supervised Mode 确认

---

## License

MIT
