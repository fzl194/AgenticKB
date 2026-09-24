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
from knowledge_mining.mining.onenet.restore import (
    RESTORE_MODE_PRODUCT_DOCUMENT, RESTORE_MODES, split_path,
)

PARTS_DIR = "parts"
DATA_NAME = "slices.jsonl"
MANIFEST_NAME = "manifest.json"

_SEGMENT_RE = re.compile(r"part_(\d+)_(\d+)\.jsonl$")


class FetchVerifyError(RuntimeError):
    """完整性校验失败（缺片/重复/坏行——不产脏数据的硬闸）."""


@dataclass(frozen=True)
class Selection:
    """导入勾选范围（47 号：持久化于 onenet_imports.selection_json）."""

    subtrees: tuple[str, ...] = ()    # path 前缀（" > " 连接，原样含首段）；空 = 整包
    max_part_id: int | None = None    # 子集抽查
    path_hints: tuple[tuple[str, tuple[str, ...]], ...] = ()
    restore_mode: str = RESTORE_MODE_PRODUCT_DOCUMENT

    def __post_init__(self) -> None:
        if self.restore_mode not in RESTORE_MODES:
            raise ValueError(f"invalid restore_mode: {self.restore_mode!r}")

    @property
    def path_hints_map(self) -> dict[str, tuple[str, ...]]:
        return dict(self.path_hints)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "subtrees": list(self.subtrees),
            "max_part_id": self.max_part_id,
            "restore_mode": self.restore_mode,
        }
        if self.path_hints:
            out["path_hints"] = {
                key: list(segments) for key, segments in self.path_hints
            }
        return out

    def merge(self, other: "Selection") -> "Selection":
        """合并勾选范围（重同步前追加子树用，47 号 L1）：
        当前为空子树表示已是整包，不能被追加操作缩窄；空 incoming 表示无新增。
        有界 part 范围取更大值，任一明确扩到 None 时以整段优先。
        """
        if other.restore_mode != self.restore_mode:
            raise ValueError("restore_mode cannot change for an existing import")
        if not other.subtrees:
            return self
        if not self.subtrees:
            if self.max_part_id is None:
                return self
            widened_max = (
                None if other.max_part_id is None
                else max(self.max_part_id, other.max_part_id)
            )
            return Selection(
                max_part_id=widened_max, restore_mode=self.restore_mode,
            )
        merged = tuple(sorted(set(self.subtrees) | set(other.subtrees)))
        if self.max_part_id is None or other.max_part_id is None:
            merged_max = None
        else:
            merged_max = max(self.max_part_id, other.max_part_id)
        hints = self.path_hints_map
        hints.update(other.path_hints_map)
        return Selection(
            subtrees=merged,
            max_part_id=merged_max,
            path_hints=tuple(sorted(hints.items())),
            restore_mode=self.restore_mode,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Selection":
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("invalid selection: expected object")
        raw_subtrees = data.get("subtrees") or []
        if (not isinstance(raw_subtrees, (list, tuple))
                or any(not isinstance(value, str) for value in raw_subtrees)):
            raise ValueError("invalid selection: subtrees must be a string array")
        subtrees = tuple(value.strip() for value in raw_subtrees if value.strip())
        mpi = data.get("max_part_id")
        if (mpi is not None and
                (isinstance(mpi, bool) or not isinstance(mpi, int) or mpi <= 0)):
            raise ValueError("invalid max_part_id: expected a positive integer or null")
        restore_mode = str(
            data.get("restore_mode") or RESTORE_MODE_PRODUCT_DOCUMENT)
        try:
            raw_hints = data.get("path_hints") or {}
            if not isinstance(raw_hints, dict):
                raise ValueError("path_hints must be an object")
            hints: list[tuple[str, tuple[str, ...]]] = []
            for raw_path, raw_segments in raw_hints.items():
                key = str(raw_path)
                if key not in subtrees or not isinstance(raw_segments, (list, tuple)):
                    raise ValueError(f"invalid path_hint: {key!r}")
                segments = tuple(
                    segment.strip() for segment in raw_segments
                    if isinstance(segment, str) and segment.strip()
                )
                flattened = tuple(
                    raw for segment in segments for raw in split_path(segment)
                )
                if (len(segments) != len(raw_segments)
                        or flattened != tuple(split_path(key))):
                    raise ValueError(f"invalid path_hint: {key!r}")
                hints.append((key, segments))
            return cls(
                subtrees=subtrees,
                max_part_id=mpi,
                path_hints=tuple(sorted(hints)),
                restore_mode=restore_mode,
            )
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"invalid selection: max_part_id={mpi!r}; "
                f"restore_mode={restore_mode!r}; {e}") from e

    def matches(self, slice_row: dict[str, Any]) -> bool:
        """切片是否落在勾选子树内（前缀匹配按段比较；空 = 整包全收）.

        path 原样比较、不剔段（beta-2）：子树路径 = 章节树节点 path 的全量段。
        """
        if not self.subtrees:
            return True
        node_path = split_path(slice_row.get("path"))  # 前缀比较含叶子
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
    # 审查 H1：覆盖连续性只在整包模式有意义——子树勾选的 part 集合天然
    # 不从 1 连续，强行校验会让一切中部子树导入失败。子树只校验去重。
    """拉取勾选范围到 workspace（段幂等 + 子树过滤 + 校验 + manifest）."""
    workspace.mkdir(parents=True, exist_ok=True)
    parts_dir = workspace / PARTS_DIR
    parts_dir.mkdir(exist_ok=True)

    # part 文件已经按 selection 过滤；选择范围/上限/还原模式变化时不可复用。
    manifest_path = workspace / MANIFEST_NAME
    reset_parts = False
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if previous.get("selection") != selection.to_dict():
            reset_parts = True
    elif any(parts_dir.iterdir()):
        # 无 manifest 无法证明这些过滤后分段属于当前 selection，保守重拉。
        reset_parts = True
    if reset_parts:
        import shutil
        shutil.rmtree(parts_dir)
        parts_dir.mkdir()

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

    # 合并段 → 临时文件（审查 M1：校验通过前不覆写既有批次，失败不毒化基线）
    data_path = workspace / DATA_NAME
    tmp_path = workspace / (DATA_NAME + ".tmp")
    lines = 0
    with tmp_path.open("w", encoding="utf-8") as out:
        for seg_path in _select_segments(parts_dir):
            for row in _iter_slice_rows(seg_path):
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                lines += 1

    # 完整性校验（H1：整包模式含覆盖检查；子树模式只查去重）
    verify_result: dict[str, Any] = {"ok": None}
    if verify:
        full_mode = not selection.subtrees
        verify_result = verify_file(tmp_path, full_coverage=full_mode)
    tmp_path.replace(data_path)  # 校验通过才原子落位

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
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return FetchOutcome(manifest=manifest, slices_path=data_path, slice_count=lines)


def verify_file(data_path: Path, *, full_coverage: bool = True) -> dict[str, Any]:
    """完整性校验：nid/part 去重恒查；覆盖缺口仅整包模式查（审查 H1）。"""
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
    # part_id 语义分两类（kone_connector 实测）：HWICS/CHM 解包大文档全局唯一
    # 连续（可查覆盖缺口）；小文档（docx/xlsx 解包）按文件内编号、跨文件重复
    # ——dup_part 是合法数据形状（如 DOC1101700755 实测 dup_part=5183），
    # 此时覆盖检查无意义。nid 是唯一硬主键。
    per_file_numbering = bool(dup_part)
    if full_coverage and not per_file_numbering:
        missing = sum(1 for p in range(1, got_max + 1) if p not in seen_part)
    else:
        missing = 0
    ok = not dup_nid and missing == 0
    result = {
        "ok": ok, "dup_nid": len(dup_nid), "dup_part": len(dup_part),
        "missing_below_got_max": missing, "got_max_part_id": got_max,
        "full_coverage": full_coverage,
        "part_id_numbering": "per_file" if per_file_numbering else "global",
    }
    if dup_part:
        result["dup_part_note"] = "part_id 按文件内编号（小文档形态），跨文件重复属正常"
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
