"""Benchmark Runner - 批量执行和评估

执行流程：
1. 加载任务列表
2. 对每个任务：
   a. 检查前置条件
   b. 调用 MCP server 执行
   c. 验证结果
   d. 记录 trace
3. 汇总指标

评估指标：
- success_rate: 成功率
- avg_steps: 平均步骤数
- avg_retries: 平均重试次数
- avg_replans: 平均重规划次数
- avg_duration_sec: 平均耗时
- human_interventions: 人工介入次数
- false_positive_rate: 验证假阳性率
- false_negative_rate: 验证假阴性率
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..core.trace import TaskTrace
from ..logging import get_logger
from .tasks import BenchmarkTask, BENCHMARK_TASKS

logger = get_logger(__name__)


@dataclass
class TaskResult:
    """单个任务的执行结果"""

    task_id: str
    task_name: str
    success: bool = False
    steps_executed: int = 0
    retries: int = 0
    replans: int = 0
    duration_sec: float = 0.0
    human_interventions: int = 0
    error: str = ""
    verification_results: list[dict[str, Any]] = field(default_factory=list)
    trace_id: str = ""

    @property
    def efficiency(self) -> float:
        """效率分（步骤数 / 预期步骤数，越接近 1 越好）"""
        # 需要外部提供 expected_steps
        return 0.0


@dataclass
class BenchmarkResult:
    """完整 benchmark 结果"""

    run_id: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    task_results: list[TaskResult] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    # 汇总指标
    @property
    def total_tasks(self) -> int:
        return len(self.task_results)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.task_results if r.success)

    @property
    def success_rate(self) -> float:
        if not self.task_results:
            return 0.0
        return self.success_count / self.total_tasks

    @property
    def avg_steps(self) -> float:
        if not self.task_results:
            return 0.0
        return sum(r.steps_executed for r in self.task_results) / self.total_tasks

    @property
    def avg_retries(self) -> float:
        if not self.task_results:
            return 0.0
        return sum(r.retries for r in self.task_results) / self.total_tasks

    @property
    def avg_replans(self) -> float:
        if not self.task_results:
            return 0.0
        return sum(r.replans for r in self.task_results) / self.total_tasks

    @property
    def avg_duration_sec(self) -> float:
        if not self.task_results:
            return 0.0
        return sum(r.duration_sec for r in self.task_results) / self.total_tasks

    @property
    def total_human_interventions(self) -> int:
        return sum(r.human_interventions for r in self.task_results)

    def summary(self) -> dict[str, Any]:
        """生成结果摘要"""
        return {
            "run_id": self.run_id,
            "total_tasks": self.total_tasks,
            "success_count": self.success_count,
            "success_rate": round(self.success_rate, 3),
            "avg_steps": round(self.avg_steps, 1),
            "avg_retries": round(self.avg_retries, 1),
            "avg_replans": round(self.avg_replans, 1),
            "avg_duration_sec": round(self.avg_duration_sec, 1),
            "total_human_interventions": self.total_human_interventions,
            "duration_total_sec": round(self.finished_at - self.started_at, 1) if self.finished_at else 0,
        }

    def report(self) -> str:
        """生成人类可读报告"""
        lines = [
            "=" * 60,
            "EMBRYO GUI BENCHMARK REPORT",
            "=" * 60,
            f"Run ID: {self.run_id}",
            f"Tasks: {self.success_count}/{self.total_tasks} passed ({self.success_rate:.1%})",
            f"Avg steps: {self.avg_steps:.1f}",
            f"Avg duration: {self.avg_duration_sec:.1f}s",
            f"Avg retries: {self.avg_retries:.1f}",
            f"Avg replans: {self.avg_replans:.1f}",
            f"Human interventions: {self.total_human_interventions}",
            "",
            "TASK DETAILS:",
            "-" * 40,
        ]

        for r in self.task_results:
            icon = "PASS" if r.success else "FAIL"
            lines.append(
                f"  [{icon}] {r.task_name} "
                f"(steps={r.steps_executed}, time={r.duration_sec:.1f}s)"
            )
            if r.error:
                lines.append(f"         Error: {r.error[:80]}")

        lines.append("=" * 60)
        return "\n".join(lines)


class BenchmarkRunner:
    """Benchmark 执行器

    用法：
        runner = BenchmarkRunner(execute_fn=my_execute_function)
        result = runner.run(tasks=BENCHMARK_TASKS[:5])
        print(result.report())
    """

    def __init__(
        self,
        execute_fn: Optional[Callable[[BenchmarkTask], TaskResult]] = None,
        traces_dir: Optional[Path] = None,
        run_id: Optional[str] = None,
    ):
        """
        Args:
            execute_fn: 任务执行函数（接收 BenchmarkTask，返回 TaskResult）
                       如果不提供，使用内置的 dry-run 执行器
            traces_dir: trace 保存目录
            run_id: 运行 ID
        """
        self._execute_fn = execute_fn or self._dry_run_execute
        self._traces_dir = traces_dir or Path.home() / ".embryo" / "benchmarks"
        self._run_id = run_id or f"bench_{int(time.time())}"

    def run(
        self,
        tasks: Optional[list[BenchmarkTask]] = None,
        filter_tags: Optional[list[str]] = None,
        filter_difficulty: Optional[str] = None,
    ) -> BenchmarkResult:
        """执行 benchmark

        Args:
            tasks: 要执行的任务列表（None = 全部）
            filter_tags: 按标签过滤
            filter_difficulty: 按难度过滤

        Returns:
            BenchmarkResult
        """
        if tasks is None:
            tasks = BENCHMARK_TASKS

        # 过滤
        if filter_tags:
            tasks = [t for t in tasks if any(tag in t.tags for tag in filter_tags)]
        if filter_difficulty:
            tasks = [t for t in tasks if t.difficulty.value == filter_difficulty]

        result = BenchmarkResult(
            run_id=self._run_id,
            started_at=time.time(),
            config={
                "total_tasks": len(tasks),
                "filter_tags": filter_tags,
                "filter_difficulty": filter_difficulty,
            },
        )

        logger.info("benchmark_start", run_id=self._run_id, tasks=len(tasks))

        for task in tasks:
            logger.info("benchmark_task_start", task_id=task.id, name=task.name)
            start = time.time()

            try:
                task_result = self._execute_fn(task)
                task_result.duration_sec = time.time() - start
                task_result.task_id = task.id
                task_result.task_name = task.name
            except Exception as e:
                task_result = TaskResult(
                    task_id=task.id,
                    task_name=task.name,
                    success=False,
                    duration_sec=time.time() - start,
                    error=f"执行异常: {e}",
                )

            result.task_results.append(task_result)

            status = "PASS" if task_result.success else "FAIL"
            logger.info(
                "benchmark_task_done",
                task_id=task.id,
                status=status,
                steps=task_result.steps_executed,
                duration=f"{task_result.duration_sec:.1f}s",
            )

        result.finished_at = time.time()

        logger.info(
            "benchmark_complete",
            run_id=self._run_id,
            success_rate=f"{result.success_rate:.1%}",
            total_time=f"{result.finished_at - result.started_at:.1f}s",
        )

        # 保存结果
        self._save_result(result)

        return result

    def _dry_run_execute(self, task: BenchmarkTask) -> TaskResult:
        """Dry-run 执行器（不实际操作 GUI，用于测试 runner 逻辑）"""
        return TaskResult(
            task_id=task.id,
            task_name=task.name,
            success=False,
            steps_executed=0,
            error="dry-run mode: 无实际执行。请提供 execute_fn。",
        )

    def _save_result(self, result: BenchmarkResult) -> None:
        """保存 benchmark 结果"""
        import json

        save_dir = self._traces_dir / self._run_id
        save_dir.mkdir(parents=True, exist_ok=True)

        # 保存摘要
        summary_path = save_dir / "summary.json"
        summary_path.write_text(
            json.dumps(result.summary(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 保存详细结果
        details_path = save_dir / "results.json"
        details = {
            "run_id": result.run_id,
            "summary": result.summary(),
            "tasks": [
                {
                    "task_id": r.task_id,
                    "task_name": r.task_name,
                    "success": r.success,
                    "steps_executed": r.steps_executed,
                    "retries": r.retries,
                    "replans": r.replans,
                    "duration_sec": round(r.duration_sec, 2),
                    "human_interventions": r.human_interventions,
                    "error": r.error,
                    "trace_id": r.trace_id,
                }
                for r in result.task_results
            ],
        }
        details_path.write_text(
            json.dumps(details, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 保存报告
        report_path = save_dir / "report.txt"
        report_path.write_text(result.report(), encoding="utf-8")

        logger.info("benchmark_results_saved", path=str(save_dir))
