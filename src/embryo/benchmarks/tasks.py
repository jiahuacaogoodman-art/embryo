"""Benchmark Tasks - 标准测试任务定义

10 个覆盖核心 GUI 操作能力的测试任务。
每个任务定义：
- 描述
- 前置条件
- 验证规则（成功判定）
- 预期步骤数
- 难度等级
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass
class BenchmarkTask:
    """单个测试任务定义"""

    id: str
    name: str
    description: str  # 给 Agent 的自然语言任务描述
    difficulty: TaskDifficulty = TaskDifficulty.MEDIUM
    category: str = "general"  # click / form / navigation / error_handling / etc

    # 前置条件
    preconditions: list[str] = field(default_factory=list)
    setup_url: str = ""  # 需要先打开的 URL

    # 验证规则
    success_criteria: list[dict[str, str]] = field(default_factory=list)
    # 每项格式: {"type": "text_visible", "target": "..."}

    # 预期
    expected_steps: int = 5  # 预期步骤数（评估效率用）
    max_allowed_steps: int = 20  # 超过则算失败
    timeout_sec: float = 60.0

    # 元信息
    tags: list[str] = field(default_factory=list)
    required_backend: list[str] = field(default_factory=list)  # e.g. ["playwright"]
    notes: str = ""


# ============================================================
# 标准测试任务（10 个）
# ============================================================

BENCHMARK_TASKS: list[BenchmarkTask] = [
    # 1. 基础点击
    BenchmarkTask(
        id="bench_01_click_button",
        name="点击按钮",
        description="打开测试网页，找到并点击标记为 'Submit' 的按钮",
        difficulty=TaskDifficulty.EASY,
        category="click",
        preconditions=["测试网页已打开"],
        success_criteria=[
            {"type": "text_visible", "target": "Submitted"},
        ],
        expected_steps=3,
        max_allowed_steps=10,
        timeout_sec=30.0,
        tags=["click", "basic"],
    ),

    # 2. 表单填写 + 登录
    BenchmarkTask(
        id="bench_02_login_form",
        name="填写登录表单",
        description="在登录页面填写用户名 'testuser' 和密码 'password123'，然后点击登录按钮",
        difficulty=TaskDifficulty.MEDIUM,
        category="form",
        preconditions=["登录页面已打开"],
        success_criteria=[
            {"type": "text_visible", "target": "Welcome"},
            {"type": "text_absent", "target": "Login"},
        ],
        expected_steps=5,
        max_allowed_steps=15,
        timeout_sec=45.0,
        tags=["form", "login", "type"],
    ),

    # 3. 错误识别
    BenchmarkTask(
        id="bench_03_error_recognition",
        name="识别错误提示",
        description="在表单中输入无效邮箱 'invalid-email'，提交后识别并报告错误提示内容",
        difficulty=TaskDifficulty.MEDIUM,
        category="error_handling",
        preconditions=["表单页面已打开"],
        success_criteria=[
            {"type": "text_visible", "target": "invalid"},
        ],
        expected_steps=4,
        max_allowed_steps=12,
        timeout_sec=30.0,
        tags=["form", "error", "verify"],
    ),

    # 4. 分页导航
    BenchmarkTask(
        id="bench_04_pagination",
        name="分页翻页",
        description="在列表页面，点击 'Next' 或 '下一页' 按钮，导航到第 2 页",
        difficulty=TaskDifficulty.EASY,
        category="navigation",
        preconditions=["列表页面已打开", "当前在第 1 页"],
        success_criteria=[
            {"type": "text_visible", "target": "2"},
            {"type": "url_contains", "target": "page=2"},
        ],
        expected_steps=3,
        max_allowed_steps=8,
        timeout_sec=30.0,
        tags=["click", "navigation"],
    ),

    # 5. 滚动查找
    BenchmarkTask(
        id="bench_05_scroll_find",
        name="滚动找到目标",
        description="页面底部有一个 'Footer Link' 文字，滚动页面直到找到它",
        difficulty=TaskDifficulty.MEDIUM,
        category="scroll",
        preconditions=["长页面已打开", "目标在视口外"],
        success_criteria=[
            {"type": "text_visible", "target": "Footer Link"},
        ],
        expected_steps=5,
        max_allowed_steps=15,
        timeout_sec=45.0,
        tags=["scroll", "find"],
    ),

    # 6. 弹窗处理
    BenchmarkTask(
        id="bench_06_popup_dismiss",
        name="关闭弹窗",
        description="页面弹出了一个确认对话框，点击关闭或取消按钮关闭它",
        difficulty=TaskDifficulty.MEDIUM,
        category="popup",
        preconditions=["页面已打开", "弹窗已显示"],
        success_criteria=[
            {"type": "text_absent", "target": "确定"},
            {"type": "element_absent", "target": "dialog"},
        ],
        expected_steps=3,
        max_allowed_steps=8,
        timeout_sec=30.0,
        tags=["popup", "click"],
    ),

    # 7. 文件上传
    BenchmarkTask(
        id="bench_07_file_upload",
        name="上传文件",
        description="找到文件上传按钮，上传文件 '/tmp/test.txt'",
        difficulty=TaskDifficulty.HARD,
        category="file",
        preconditions=["上传页面已打开", "/tmp/test.txt 存在"],
        success_criteria=[
            {"type": "text_visible", "target": "uploaded"},
        ],
        expected_steps=4,
        max_allowed_steps=12,
        timeout_sec=45.0,
        tags=["file", "upload"],
        required_backend=["playwright"],
    ),

    # 8. 文件下载
    BenchmarkTask(
        id="bench_08_file_download",
        name="下载文件",
        description="找到下载链接并点击，确认文件开始下载",
        difficulty=TaskDifficulty.HARD,
        category="file",
        preconditions=["下载页面已打开"],
        success_criteria=[
            {"type": "text_visible", "target": "download"},
        ],
        expected_steps=3,
        max_allowed_steps=10,
        timeout_sec=45.0,
        tags=["file", "download", "click"],
    ),

    # 9. 多窗口切换
    BenchmarkTask(
        id="bench_09_window_switch",
        name="窗口切换",
        description="打开一个新标签页后，切换回原来的标签页",
        difficulty=TaskDifficulty.HARD,
        category="navigation",
        preconditions=["浏览器已打开", "至少有两个标签页"],
        success_criteria=[
            {"type": "url_contains", "target": "original"},
        ],
        expected_steps=4,
        max_allowed_steps=12,
        timeout_sec=45.0,
        tags=["window", "navigation", "hotkey"],
        required_backend=["playwright"],
    ),

    # 10. 慢加载等待
    BenchmarkTask(
        id="bench_10_wait_loading",
        name="等待加载",
        description="页面正在加载中（显示 loading），等待加载完成后点击出现的按钮",
        difficulty=TaskDifficulty.MEDIUM,
        category="wait",
        preconditions=["页面正在加载"],
        success_criteria=[
            {"type": "text_absent", "target": "loading"},
            {"type": "text_visible", "target": "Ready"},
        ],
        expected_steps=5,
        max_allowed_steps=15,
        timeout_sec=60.0,
        tags=["wait", "loading", "click"],
    ),
]
