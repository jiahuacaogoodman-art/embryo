"""Cron 调度器实现

支持：
- 一次性延迟任务（after N seconds/minutes/hours）
- 周期性任务（every N seconds/minutes/hours）
- Cron 表达式任务（0 9 * * * = 每天9点）
- 任务持久化（重启后恢复）
- 执行结果记录
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, TYPE_CHECKING

from ..logging import get_logger

if TYPE_CHECKING:
    from ..agent import EmbryoAgent

logger = get_logger("scheduler")


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    ONCE = "once"          # 一次性
    INTERVAL = "interval"  # 周期性
    CRON = "cron"          # Cron 表达式


@dataclass
class CronJob:
    """定时任务"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    task_description: str = ""  # Agent 要执行的自然语言任务
    job_type: JobType = JobType.ONCE
    interval_seconds: float = 0  # 周期间隔（interval 类型用）
    cron_expression: str = ""  # cron 表达式（cron 类型用）
    next_run_at: float = 0.0  # 下次执行时间（Unix timestamp）
    status: JobStatus = JobStatus.PENDING
    last_run_at: float = 0.0
    last_result: str = ""
    run_count: int = 0
    max_runs: int = 0  # 0 = 无限
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "task_description": self.task_description,
            "job_type": self.job_type.value,
            "interval_seconds": self.interval_seconds,
            "cron_expression": self.cron_expression,
            "next_run_at": self.next_run_at,
            "status": self.status.value,
            "last_run_at": self.last_run_at,
            "last_result": self.last_result[:500],
            "run_count": self.run_count,
            "max_runs": self.max_runs,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CronJob":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            task_description=data.get("task_description", ""),
            job_type=JobType(data.get("job_type", "once")),
            interval_seconds=data.get("interval_seconds", 0),
            cron_expression=data.get("cron_expression", ""),
            next_run_at=data.get("next_run_at", 0),
            status=JobStatus(data.get("status", "pending")),
            last_run_at=data.get("last_run_at", 0),
            last_result=data.get("last_result", ""),
            run_count=data.get("run_count", 0),
            max_runs=data.get("max_runs", 0),
            created_at=data.get("created_at", 0),
        )


class CronScheduler:
    """Cron 调度器

    后台线程定期检查任务，到期则执行。
    """

    def __init__(self, agent: "EmbryoAgent", storage_dir: Optional[Path] = None):
        self.agent = agent
        self.storage_dir = storage_dir or (agent.config.data_dir / "scheduler")
        self._jobs: list[CronJob] = []
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._check_interval = 10.0  # 每 10 秒检查一次
        self._load_jobs()

    def start(self):
        """启动调度器后台线程"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="cron-scheduler")
        self._thread.start()
        logger.info("scheduler_started", jobs_count=len(self._jobs))

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("scheduler_stopped")

    def add_job(
        self,
        task_description: str,
        name: str = "",
        delay_seconds: float = 0,
        interval_seconds: float = 0,
        cron_expression: str = "",
        max_runs: int = 0,
    ) -> CronJob:
        """添加定时任务

        Args:
            task_description: Agent 要执行的自然语言任务
            name: 任务名称（可选）
            delay_seconds: 延迟执行秒数（一次性任务）
            interval_seconds: 周期间隔秒数（周期任务）
            cron_expression: Cron 表达式（暂不支持完整解析）
            max_runs: 最大执行次数（0=无限）

        Returns:
            创建的任务
        """
        now = time.time()

        if interval_seconds > 0:
            job_type = JobType.INTERVAL
            next_run = now + (delay_seconds or interval_seconds)
        elif cron_expression:
            job_type = JobType.CRON
            next_run = now + 60  # 简化：1分钟后首次执行
        else:
            job_type = JobType.ONCE
            next_run = now + delay_seconds

        job = CronJob(
            name=name or task_description[:30],
            task_description=task_description,
            job_type=job_type,
            interval_seconds=interval_seconds,
            cron_expression=cron_expression,
            next_run_at=next_run,
            max_runs=max_runs,
        )

        self._jobs.append(job)
        self._save_jobs()

        logger.info("job_added", job_id=job.id, name=job.name, next_run_at=next_run)
        return job

    def cancel_job(self, job_id: str) -> bool:
        """取消任务"""
        for job in self._jobs:
            if job.id == job_id and job.status == JobStatus.PENDING:
                job.status = JobStatus.CANCELLED
                self._save_jobs()
                return True
        return False

    def list_jobs(self) -> list[CronJob]:
        """列出所有任务"""
        return list(self._jobs)

    def _run_loop(self):
        """后台循环：定期检查并执行到期任务"""
        while self._running:
            now = time.time()

            for job in self._jobs:
                if job.status != JobStatus.PENDING:
                    continue
                if job.next_run_at > now:
                    continue

                # 到期执行
                self._execute_job(job)

            time.sleep(self._check_interval)

    def _execute_job(self, job: CronJob):
        """执行单个任务"""
        logger.info("job_executing", job_id=job.id, task=job.task_description[:50])
        job.status = JobStatus.RUNNING
        job.last_run_at = time.time()
        job.run_count += 1

        try:
            # 使用 Agent 执行任务
            result = self.agent.chat(job.task_description)
            job.last_result = result
            job.status = JobStatus.COMPLETED if job.job_type == JobType.ONCE else JobStatus.PENDING

            # 周期任务：计算下次执行时间
            if job.job_type == JobType.INTERVAL:
                job.next_run_at = time.time() + job.interval_seconds
                job.status = JobStatus.PENDING

                # 检查 max_runs
                if job.max_runs > 0 and job.run_count >= job.max_runs:
                    job.status = JobStatus.COMPLETED

            logger.info("job_completed", job_id=job.id, result_length=len(result))

        except Exception as e:
            job.last_result = f"执行失败: {e}"
            job.status = JobStatus.FAILED
            logger.error("job_failed", job_id=job.id, error=str(e))

        self._save_jobs()

    def _load_jobs(self):
        """从文件加载任务"""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        jobs_file = self.storage_dir / "jobs.json"
        if jobs_file.exists():
            try:
                data = json.loads(jobs_file.read_text(encoding="utf-8"))
                self._jobs = [CronJob.from_dict(j) for j in data.get("jobs", [])]
                # 恢复 pending 任务
                for job in self._jobs:
                    if job.status == JobStatus.RUNNING:
                        job.status = JobStatus.PENDING  # 重启后重试
            except Exception:
                self._jobs = []

    def _save_jobs(self):
        """持久化任务"""
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        jobs_file = self.storage_dir / "jobs.json"
        data = {"jobs": [j.to_dict() for j in self._jobs]}
        jobs_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
