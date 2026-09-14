# -*- coding: utf-8 -*-
"""批次拉取器（47 号 §四-6 步骤 4；自 kone_connector/src/fetch_batch.py 移植改造）.

- 段幂等：每段独立 ``parts/part_<lo>_<hi>.jsonl``，存在即跳过；
- 子树过滤：selection.subtrees（path 前缀集合）在落盘前本地过滤；
- 完整性校验：nid 去重 / part_id 去重 / 覆盖缺口；
- manifest 对齐 kone_connector/docs/02_batch_protocol.md（批次协议）。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from knowledge_mining.mining.onenet.client import OnenetClient
from knowledge_mining.mining.onenet.restore import split_path

PARTS_DIR = "parts"
DATA_NAME = "slices.jsonl"
MANIFEST_NAME = "manifest.json"

_SEGMENT_RE = re.compile(r"part_(\d+)_(\d+)\.jsonl$")


class FetchVerifyError(RuntimeError):
    """完整性校验失败（缺片/重复/坏行——不产脏数据的硬闸）."""


@dataclass(frozen=True)
class Selection:
    """导入勾选范围（47 号：持久化于 onenet_imports.selection_json）."""

    subtrees: tuple[str, ...] = ()    # path 前缀（" > " 连接，不含包名）；空 = 整包
    max_part_id: int | None = None    # 子集抽查

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtrees": list(self.subtrees),
            "max_part_id": self.max_part_id,
        }

    def merge(self, other: "Selection") -> "Selection":
        """合并勾选范围（重同步前追加子树用，47 号 L1）：
        subtrees 并集去重；max_part_id 取更宽者（None=整包优先）。"""
        merged = tuple(sorted(set(self.subtrees) | set(other.subtrees)))
        maxes = [m for m in (self.max_part_id, other.max_part_id) if m]
        return Selection(subtrees=merged, max_part_id=min(maxes) if maxes else None)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Selection":
        data = data or {}
        subtrees = tuple(str(s) for s in (data.get("subtrees") or []) if str(s).strip())
        mpi = data.get("max_part_id")
        try:
            return cls(subtrees=subtrees, max_part_id=int(mpi) if mpi else None)
        except (TypeError, ValueError) as e:
            raise ValueError(f"invalid max_part_id: {mpi!r}") from e

    def matches(self, slice_row: dict[str, Any]) -> bool:
        """切片是否落在勾选子树内（前缀匹配按段比较；空 = 整包全收）."""
        if not self.subtrees:
            return True
        segs = split_path(slice_row.get("path"))
        if len(segs) >= 2:
            segs = segs[1:]  # 剔包名
        node_path = segs  # 前缀比较含叶子
        for prefix in self.subtrees:
            psegs = split_path(prefix)
            if node_path[:len(psegs)] == psegs:
                return True
        return False


@dataclass
class FetchOutcome:
    manifest: dict[str, Any]
    slices_path: Path
    slice_count: int


def _segment_files(parts_dir: Path) -> list[tuple[int, int, Path]]:
    segs = []
    for fn in parts_dir.iterdir():
        m = _SEGMENT_RE.search(fn.name)
        if m:
            segs.append((int(m.group(1)), int(m.group(2)), fn))
    segs.sort(key=lambda t: (t[0], t[1]))
    return segs


def _select_segments(parts_dir: Path) -> list[Path]:
    """段选择（移植 demo）：完全包含去重（保留大段）+ 部分重叠报错."""
    segs = _segment_files(parts_dir)
    remove_ids: set[int] = set()
    for i, a in enumerate(segs):
        for j, b in enumerate(segs):
            if i != j and a[0] <= b[0] and b[1] <= a[1] and (a[0], a[1]) != (b[0], b[1]):
                remove_ids.add(j)
    kept = [s for k, s in enumerate(segs) if k not in remove_ids]
    for prev, cur in zip(kept, kept[1:]):
        if cur[0] <= prev[1]:
            raise FetchVerifyError(
                f"parts 段部分重叠: [{prev[0]},{prev[1]}] 与 [{cur[0]},{cur[1]}]，"
                f"请清理 {parts_dir}")
    return [s[2] for s in kept]


def _iter_slice_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise FetchVerifyError(f"坏行（{path.name}）: {e}") from e
    return rows


def fetch_selection(
    client: OnenetClient, source_id: str, selection: Selection,
    workspace: Path, *,
    chunk_width: int = 10000,
    page_size: int = 1000,
    throttle_seconds: float = 0.3,
    verify: bool = True,
) -> FetchOutcome:
    """拉取勾选范围到 workspace（段幂等 + 子树过滤 + 校验 + manifest）."""
    workspace.mkdir(parents=True, exist_ok=True)
    parts_dir = workspace / PARTS_DIR
    parts_dir.mkdir(exist_ok=True)

    total = client.count_source(source_id)
    if total <= 0:
        raise FetchVerifyError(f"source 无切片: {source_id}")
    pr = client.part_range(source_id)
    lo = int(pr.get("min") or 1)
    hi = int(pr.get("max") or total)
    if selection.max_part_id:
        hi = min(hi, selection.max_part_id)

    # 分段拉取（段文件存在即跳过 = 幂等）
    seg_lo = lo
    while seg_lo <= hi:
        seg_hi = min(seg_lo + chunk_width - 1, hi)
        part_path = parts_dir / f"part_{seg_lo}_{seg_hi}.jsonl"
        if not part_path.exists():
            rows = [r for r in client.fetch_source_chunk(
                source_id, seg_lo, seg_hi, page_size=page_size)
                if selection.matches(r)]
            with part_path.open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            if throttle_seconds > 0:
                time.sleep(throttle_seconds)  # 温和限速
        seg_lo = seg_hi + 1

    # 合并段 → slices.jsonl
    data_path = workspace / DATA_NAME
    lines = 0
    with data_path.open("w", encoding="utf-8") as out:
        for seg_path in _select_segments(parts_dir):
            for row in _iter_slice_rows(seg_path):
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                lines += 1

    # 完整性校验
    verify_result: dict[str, Any] = {"ok": None}
    if verify:
        verify_result = verify_file(data_path)

    manifest = {
        "batch_id": f"{source_id}-{int(time.time())}",
        "source_system": "知识一张网 ITSM-source_type-0",
        "source_id": source_id,
        "doc_total_slices": total,
        "part_id_range": {"min": pr.get("min"), "max": pr.get("max")},
        "fetched_max_part_id": hi,
        "fetch_mode": "full",
        "selection": selection.to_dict(),
        "segments": [p.name for p in _select_segments(parts_dir)],
        "lines_in_jsonl": lines,
        "verify": verify_result,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (workspace / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return FetchOutcome(manifest=manifest, slices_path=data_path, slice_count=lines)


def verify_file(data_path: Path) -> dict[str, Any]:
    """完整性校验：nid 去重 / part_id 去重 / 覆盖缺口（不通过 → FetchVerifyError）."""
    dup_nid: set[str] = set()
    dup_part: set[int] = set()
    seen_nid: set[str] = set()
    seen_part: set[int] = set()
    for row in _iter_slice_rows(data_path):
        nid = str(row.get("nid") or "")
        pid = int(row.get("part_id") or -1)
        if nid in seen_nid:
            dup_nid.add(nid)
        seen_nid.add(nid)
        if pid in seen_part:
            dup_part.add(pid)
        seen_part.add(pid)
    got_max = max(seen_part) if seen_part else 0
    missing = sum(1 for p in range(1, got_max + 1) if p not in seen_part)
    ok = not dup_nid and not dup_part and missing == 0
    result = {
        "ok": ok, "dup_nid": len(dup_nid), "dup_part": len(dup_part),
        "missing_below_got_max": missing, "got_max_part_id": got_max,
    }
    if not ok:
        result["detail"] = (
            f"dup_nid样例={sorted(dup_nid)[:3]} "
            f"dup_part样例={sorted(dup_part)[:3]} missing={missing}")
        raise FetchVerifyError("批次校验未通过: " + result["detail"])
    return result


def load_slices(data_path: Path) -> list[dict[str, Any]]:
    """读回批次切片（resync diff 用）."""
    return _iter_slice_rows(data_path)


__all__ = [
    "DATA_NAME", "FetchOutcome", "FetchVerifyError", "MANIFEST_NAME",
    "PARTS_DIR", "Selection", "fetch_selection", "load_slices", "verify_file",
]
