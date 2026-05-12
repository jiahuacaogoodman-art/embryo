"""Benchmarks 模块 - GUI 自动化测试基准

提供标准化的测试任务和评估指标，让系统从"看起来能用"变成"有工程评估"。

组件：
- tasks/: 测试任务定义
- runner: 批量执行和结果收集
- metrics: 评估指标计算
"""

from .runner import BenchmarkRunner, BenchmarkResult
from .tasks import BENCHMARK_TASKS, BenchmarkTask

__all__ = ["BenchmarkRunner", "BenchmarkResult", "BENCHMARK_TASKS", "BenchmarkTask"]
