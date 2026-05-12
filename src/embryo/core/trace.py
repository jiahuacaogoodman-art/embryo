"""Task Trace - 完整任务执行记录

每次任务执行保存完整 trace：

    traces/{task_id}/
        plan.json           # 初始计划
        steps.jsonl         # 每步执行记录（追加写入）
        screenshots/        # 截图（before/after）
        observations/       # Observation JSON 快照
        policy.jsonl        # 安全策略决策日志
        final_report.json   # 最终结果报告

用途：
- 事后复盘和调试
- Benchmark 评估
- Skill 生成的素材
- 证明系统有效性
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..logging import get_logger

logger = get_logger(__name__)


@dataclass
class StepRecord:
    """单步执行记录

    对应 steps.jsonl 中的一行。
    """

    step_id: str = ""
    step_index: int = 0
    timestamp: float = field(default_factory=time.time)
    action: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    resolved_target: dict[str, Any] = field(default_factory=dict)  # {x, y, source, confidence}
    parameters: dict[str, Any] = field(default_factory=dict)
    result: str = ""  # success / failed / no_effect / timeout
    result_message: str = ""
    verification: dict[str, Any] = field(default_factory=dict)  # {type, target, passed}
    failure_type: str = ""  # FailureType value
    failure_message: str = ""
    duration_ms: int = 0
    screenshot_before: str = ""  # 相对路径
    screenshot_after: str = ""  # 相对路径
    observation_before: str = ""  # 相对路径
    observation_after: str = ""  # 相对路径
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_index": self.step_index,
            "timestamp": self.timestamp,
            "action": self.action,
            "target": self.target,
            "resolved_target": self.resolved_target,
            "parameters": self.parameters,
            "result": self.result,
            "result_message": self.result_message,
            "verification": self.verification,
            "failure_type": self.failure_type,
            "failure_message": self.failure_message,
            "duration_ms": self.duration_ms,
            "screenshot_before": self.screenshot_before,
            "screenshot_after": self.screenshot_after,
            "observation_before": self.observation_before,
            "observation_after": self.observation_after,
            "metadata": self.metadata,
        }

    def to_json_line(self) -> str:
        """序列化为单行 JSON（JSONL 格式）"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


class TaskTrace:
    """任务执行 Trace 记录器

    每个任务实例对应一个 TaskTrace，负责：
    - 创建 trace 目录结构
    - 保存初始计划
    - 逐步记录执行过程
    - 保存截图和 Observation
    - 生成最终报告

    用法：
        trace = TaskTrace(traces_dir, task_description="登录淘宝")
        trace.save_plan(plan_dict)
        trace.record_step(step_record)
        trace.save_screenshot(img_path, "001_before")
        trace.save_observation(obs_dict, "001_before")
        trace.finalize(success=True, summary="任务完成")
    """

    def __init__(
        self,
        traces_dir: Path,
        task_description: str = "",
        task_id: Optional[str] = None,
    ):
        """
        Args:
            traces_dir: traces 根目录
            task_description: 任务描述
            task_id: 任务 ID（None=自动生成）
        """
        self._task_id = task_id or f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
        self._task_description = task_description
        self._trace_dir = traces_dir / self._task_id
        self._screenshots_dir = self._trace_dir / "screenshots"
        self._observations_dir = self._trace_dir / "observations"
        self._step_count = 0
        self._start_time = time.time()
        self._finalized = False

        # 创建目录
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        self._screenshots_dir.mkdir(exist_ok=True)
        self._observations_dir.mkdir(exist_ok=True)

        # 写入基本信息
        self._write_meta()

        logger.info("trace_started", task_id=self._task_id, task=task_description[:50])

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def trace_dir(self) -> Path:
        return self._trace_dir

    @property
    def step_count(self) -> int:
        return self._step_count

    def save_plan(self, plan: dict[str, Any]) -> None:
        """保存初始计划"""
        plan_path = self._trace_dir / "plan.json"
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def record_step(self, record: StepRecord) -> None:
        """记录一步执行结果（追加到 steps.jsonl）"""
        self._step_count += 1
        record.step_index = self._step_count

        steps_path = self._trace_dir / "steps.jsonl"
        with open(steps_path, "a", encoding="utf-8") as f:
            f.write(record.to_json_line() + "\n")

    def record_policy_decision(self, decision: dict[str, Any]) -> None:
        """记录安全策略决策"""
        policy_path = self._trace_dir / "policy.jsonl"
        decision["timestamp"] = time.time()
        with open(policy_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(decision, ensure_ascii=False) + "\n")

    def save_screenshot(self, source_path: str | Path, name: str) -> str:
        """保存截图到 trace 目录

        Args:
            source_path: 源截图文件路径
            name: 保存名称（如 "001_before"）

        Returns:
            相对路径（相对于 trace_dir）
        """
        source = Path(source_path)
        if not source.exists():
            return ""

        ext = source.suffix or ".png"
        dest = self._screenshots_dir / f"{name}{ext}"

        try:
            shutil.copy2(source, dest)
            return f"screenshots/{name}{ext}"
        except Exception as e:
            logger.warning("trace_screenshot_save_failed", error=str(e))
            return ""

    def save_observation(self, observation: dict[str, Any], name: str) -> str:
        """保存 Observation JSON 快照

        Args:
            observation: Observation 字典
            name: 保存名称（如 "001_before"）

        Returns:
            相对路径
        """
        dest = self._observations_dir / f"{name}.json"
        try:
            dest.write_text(
                json.dumps(observation, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            return f"observations/{name}.json"
        except Exception as e:
            logger.warning("trace_observation_save_failed", error=str(e))
            return ""

    def finalize(
        self,
        success: bool,
        summary: str = "",
        metrics: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """生成最终报告

        Args:
            success: 任务是否成功
            summary: 结果摘要
            metrics: 额外指标

        Returns:
            最终报告字典
        """
        if self._finalized:
            return {}

        self._finalized = True
        elapsed = time.time() - self._start_time

        report = {
            "task_id": self._task_id,
            "task_description": self._task_description,
            "success": success,
            "summary": summary,
            "total_steps": self._step_count,
            "duration_sec": round(elapsed, 2),
            "started_at": self._start_time,
            "finished_at": time.time(),
            "metrics": metrics or {},
        }

        # 读取 steps 统计
        steps_path = self._trace_dir / "steps.jsonl"
        if steps_path.exists():
            steps = []
            with open(steps_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            steps.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

            report["metrics"]["step_results"] = {
                "success": sum(1 for s in steps if s.get("result") == "success"),
                "failed": sum(1 for s in steps if s.get("result") == "failed"),
                "no_effect": sum(1 for s in steps if s.get("result") == "no_effect"),
                "total": len(steps),
            }
            report["metrics"]["avg_step_duration_ms"] = (
                sum(s.get("duration_ms", 0) for s in steps) // max(len(steps), 1)
            )

        # 保存报告
        report_path = self._trace_dir / "final_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info(
            "trace_finalized",
            task_id=self._task_id,
            success=success,
            steps=self._step_count,
            duration=f"{elapsed:.1f}s",
        )

        return report

    def _write_meta(self) -> None:
        """写入 trace 元信息"""
        meta = {
            "task_id": self._task_id,
            "task_description": self._task_description,
            "started_at": self._start_time,
            "version": "1.0",
        }
        meta_path = self._trace_dir / "meta.json"
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # --------------------------------------------------
    # 便捷方法
    # --------------------------------------------------

    def record_step_simple(
        self,
        action: str,
        target: dict[str, Any],
        result: str,
        duration_ms: int = 0,
        **kwargs,
    ) -> StepRecord:
        """简化的步骤记录方法"""
        record = StepRecord(
            step_id=f"s{self._step_count + 1}",
            action=action,
            target=target,
            result=result,
            duration_ms=duration_ms,
            **kwargs,
        )
        self.record_step(record)
        return record

    @classmethod
    def load(cls, trace_dir: Path) -> Optional[dict[str, Any]]:
        """加载已有 trace 的最终报告

        Returns:
            报告字典，不存在则 None
        """
        report_path = trace_dir / "final_report.json"
        if report_path.exists():
            try:
                return json.loads(report_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    @classmethod
    def list_traces(cls, traces_dir: Path, limit: int = 50) -> list[dict[str, Any]]:
        """列出所有 traces 的摘要

        Returns:
            按时间倒序的 trace 摘要列表
        """
        if not traces_dir.exists():
            return []

        traces = []
        for item in sorted(traces_dir.iterdir(), reverse=True):
            if item.is_dir():
                meta_path = item / "meta.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        # 尝试加载报告
                        report_path = item / "final_report.json"
                        if report_path.exists():
                            report = json.loads(report_path.read_text(encoding="utf-8"))
                            meta["success"] = report.get("success")
                            meta["total_steps"] = report.get("total_steps")
                            meta["duration_sec"] = report.get("duration_sec")
                        traces.append(meta)
                    except json.JSONDecodeError:
                        pass

            if len(traces) >= limit:
                break

        return traces
