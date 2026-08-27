"""进程内归档解压任务注册表（批次2c：大包异步解压）。

单实例部署下够用：重启丢任务（用户重传即可，报告已注明）。若未来多实例
或需要跨重启持久，迁到 asset_upload_sessions 表即可——状态字段同构。
"""
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from typing import Any


class ArchiveTaskRegistry:
    """线程安全的任务状态表，容量有界（FIFO 驱逐最旧）。"""

    def __init__(self, *, max_entries: int = 100) -> None:
        self._lock = threading.Lock()
        self._tasks: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._max_entries = max_entries

    def create(self, *, kb_id: str, archive_name: str) -> str:
        task_id = f"arch_{uuid.uuid4().hex[:16]}"
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "kb_id": kb_id,
                "archive_name": archive_name,
                "status": "processing",
                "progress": {"done": 0, "total": None},
                "document_count": None,
                "failed": None,
                "error": None,
            }
            self._evict_locked()
        return task_id

    def update(self, task_id: str, *, done: int | None = None,
               total: int | None = None) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task["status"] != "processing":
                return
            if done is not None:
                task["progress"]["done"] = done
            if total is not None:
                task["progress"]["total"] = total

    def complete(self, task_id: str, *, document_count: int, failed: int) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task["status"] != "processing":
                return
            task["status"] = "completed"
            task["document_count"] = document_count
            task["failed"] = failed

    def fail(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task["status"] != "processing":
                return
            task["status"] = "failed"
            task["error"] = error[:500]

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(t) for t in self._tasks.values()]

    def _evict_locked(self) -> None:
        while len(self._tasks) > self._max_entries:
            self._tasks.popitem(last=False)


#: 模块级单例（路由与后台任务共享）
registry = ArchiveTaskRegistry()
