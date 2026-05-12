"""结构化日志系统

提供 JSON 格式结构化日志，每步操作可回溯审计。

用法：
    from embryo.logging import get_logger
    logger = get_logger(__name__)
    logger.info("tool_execute", tool="terminal", duration=1.23)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional


class StructuredLogger:
    """结构化日志器

    输出 JSON 格式日志到 stderr 和可选的文件。
    每条日志包含时间戳、级别、模块、事件名和结构化字段。
    """

    LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

    def __init__(self, name: str, level: str = "INFO", log_file: Optional[Path] = None):
        self.name = name
        self.level = self.LEVELS.get(level.upper(), 20)
        self.log_file = log_file
        self._file_handle = None

    def debug(self, event: str, **fields):
        self._log("DEBUG", event, fields)

    def info(self, event: str, **fields):
        self._log("INFO", event, fields)

    def warning(self, event: str, **fields):
        self._log("WARNING", event, fields)

    def error(self, event: str, **fields):
        self._log("ERROR", event, fields)

    def critical(self, event: str, **fields):
        self._log("CRITICAL", event, fields)

    def _log(self, level: str, event: str, fields: dict[str, Any]):
        if self.LEVELS.get(level, 0) < self.level:
            return

        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "level": level,
            "module": self.name,
            "event": event,
            **fields,
        }

        line = json.dumps(record, ensure_ascii=False, default=str)

        # 输出到 stderr（不干扰 stdout 的正常输出）
        if self.level <= self.LEVELS.get("DEBUG", 10) or level != "DEBUG":
            print(line, file=sys.stderr)

        # 输出到日志文件
        if self.log_file:
            try:
                if self._file_handle is None:
                    self.log_file.parent.mkdir(parents=True, exist_ok=True)
                    self._file_handle = open(self.log_file, "a", encoding="utf-8")
                self._file_handle.write(line + "\n")
                self._file_handle.flush()
            except Exception:
                pass


# 全局日志配置
_global_level = "INFO"
_global_log_file: Optional[Path] = None
_loggers: dict[str, StructuredLogger] = {}


def configure(level: str = "INFO", log_file: Optional[Path] = None):
    """全局配置日志"""
    global _global_level, _global_log_file
    _global_level = level.upper()
    _global_log_file = log_file
    # 更新已创建的 logger
    for logger in _loggers.values():
        logger.level = StructuredLogger.LEVELS.get(_global_level, 20)
        logger.log_file = _global_log_file


def get_logger(name: str) -> StructuredLogger:
    """获取指定模块的 logger"""
    if name not in _loggers:
        _loggers[name] = StructuredLogger(
            name=name,
            level=_global_level,
            log_file=_global_log_file,
        )
    return _loggers[name]
