# -*- coding: utf-8 -*-
"""β 规则文件还原（47 号 §四-2）.

实测结论（32 条真实样例 + kone_connector 摸底）：
- path 树 = HWICS/CHM 包的 TOC 文件树，每个节点可能是页面；节点可既有
  直属切片又有子节点；绝对层级不可靠（分支高度不齐）；
- ``title`` 恒等于 ``path[-1]``；``url`` 为包级不能区分文件；
- ``part_id`` 全局阅读序：文件内切片升序拼接、文件间按 min(part_id) 排序。

规则 β（rule_version=beta-2，相对规则，免疫层级不齐）：
- **path 原样参与建树/落位，不剔任何段**（beta-2 修订，内网实测：path[0]
  语义随 source 而异——HWICS 包根是技术包名，产品文档附件/普通文档的
  path[0] 是附件名或章节名（即导入单位本身）。猜「什么算包名」必引新 bug，
  统一不剔最保险；HWICS 包仅在顶层文档目录下多一层包名目录，无害）；
- 文件 = 切片 path[-2] 节点（完整路径），path[-1] 为文件内直属标题；
- 规则 α 兜底：path 仅一段（path=X）→ X 自身即文件（根级孤页）；
- 文件内容 = 归属该文件的切片按 part_id 升序；
- 文件排序 = min(part_id) 升序；文件之上层级 = 目录（"/" 连接）。
- beta-3：title 校准切分——``title`` 恒等于 ``path[-1]``（32 样例实证），
  标题自身含 ``>`` 时尾部多切出假段致文件错分；path 尾部各段与 title
  切段严格相等时合并为一个标题段，其余回退 raw 切分（53 号 §四）。

边界归并（更深层标题向上归并）留作后续版本（beta-3 已用于 title 校准切分）：
beta 系在真实 HWICS 数据上每个 path 节点本身即页面，纯 β 分组即成立
（样例回归见 test_restore.py）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

#: 还原规则版本（47 号：rule_version 版本化，规则不对调参数不改正代码）。
RULE_VERSION = "beta-3"

#: 表格预测标记（content 内管道表格块边界）。
TBL_RE = re.compile(r"\[tbl_predict_(?:start|end)\]")

#: 目录段危险字符（与 sanitize_filename 同类：路径分隔符/Windows 非法/控制符）。
#: 章节名混入这些字符会让 ensure_folder_path 拒绝（\、换行）或长出假层级（/）。
_FOLDER_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean_content(content: str | None) -> str:
    """剥离 [tbl_predict_*] 标记（保留管道表格文本）."""
    return TBL_RE.sub("", content or "").strip()


def split_path(path: str | None) -> list[str]:
    """path → 段列表（剔空段）；返回 [包名, L1, ..., 叶子]."""
    return [p.strip() for p in (path or "").split(">") if p.strip()]


def split_path_calibrated(path: str | None, title: str | None) -> list[str]:
    """path + title → 校准段列表（beta-3：标题跨段时尾部合并为一段）.

    仅当 title 切出多于一段且 path 尾部与 title 段严格相等（strip 后逐段
    比）才合并；普通数据与 raw 切分完全一致。合并段用 `` > `` 规范连接
    （与树节点路径的既有规范化一致）。
    """
    segs = split_path(path)
    tsegs = split_path(title)
    if (len(tsegs) > 1 and len(segs) >= len(tsegs)
            and segs[-len(tsegs):] == tsegs):
        return segs[:-len(tsegs)] + [" > ".join(tsegs)]
    return segs


def build_calibration(
    slices: Iterable[dict[str, Any]],
) -> dict[str, list[str]]:
    """path 字符串 → 校准段（同 path 单一切法，part_id 升序首个 title 决定）.

    同一 path 不同 title 的罕见数据取其一——否则建树会产生重复节点键
    （el-tree node-key 崩坏）且分组分裂（53 号 §四-2）。
    """
    calib: dict[str, list[str]] = {}
    for s in sorted(slices, key=lambda x: int(x.get("part_id") or 0)):
        key = str(s.get("path") or "")
        if key and key not in calib:
            calib[key] = split_path_calibrated(key, s.get("title"))
    return calib


def sanitize_folder_segment(name: str, *, max_len: int = 120) -> str:
    """目录段清洗（V1.3）：危险字符替换为 ``_``，防炸导入/假层级。

    仅用于 KB 落位目录链；file_path/树路径（selection 匹配键）保持上游原样。
    极端撞名（上游 ``A/B`` 与 ``A_B`` 清洗后同段）可接受——落位显示层语义。
    """
    cleaned = _FOLDER_UNSAFE_RE.sub("_", (name or "").strip()).strip(". ")
    return (cleaned or "_")[:max_len]


def top_folder_segment(doc_name: str | None, source_id: str) -> str:
    """KB 顶层目录段（V1.3 落位规则）：``文档名 [source_id]``.

    包名首段剔除后目录树缺文档级分区，跨产品文档同名章节会混层；
    顶层补文档名 + 唯一标识，人能看懂、机器永不混。doc_name 缺失时
    退化为纯 source_id。
    """
    name = sanitize_folder_segment(doc_name or "")
    if not name or name == "_":
        return source_id
    return f"{name} [{source_id}]"


# ---------------------------------------------------------------- 路径树


@dataclass
class TreeNode:
    """path 树节点（toc_scan 与 restore 共用）."""

    title: str
    path: str                      # 原样 " > " 连接路径（不剔段）
    depth: int                     # 1 起
    children: dict[str, "TreeNode"] = field(default_factory=dict)
    slices: list[dict[str, Any]] = field(default_factory=list)  # path 恰好终止于此的切片

    @property
    def slice_count_total(self) -> int:
        """本节点及全部后代切片数."""
        return len(self.slices) + sum(c.slice_count_total for c in self.children.values())

    @property
    def direct_slice_count(self) -> int:
        """本节点直属切片数（path 恰好终止于此）."""
        return len(self.slices)

    @property
    def part_min(self) -> int | None:
        values = [int(s.get("part_id") or 0) for s in self.iter_slices()]
        return min(values) if values else None

    @property
    def part_max(self) -> int | None:
        values = [int(s.get("part_id") or 0) for s in self.iter_slices()]
        return max(values) if values else None

    def iter_slices(self) -> Iterable[dict[str, Any]]:
        yield from self.slices
        for child in self.children.values():
            yield from child.iter_slices()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "path": self.path,
            "depth": self.depth,
            "slice_count": self.slice_count_total,
            "direct_slice_count": len(self.slices),
            "part_min": self.part_min,
            "part_max": self.part_max,
            "children": [c.to_dict() for c in self.children.values()],
        }


def build_path_tree(slices: Iterable[dict[str, Any]]) -> TreeNode:
    """按校准段建树（beta-3；切片挂在其完整 path 的叶子节点）."""
    root = TreeNode(title="__ROOT__", path="", depth=0)
    slices = list(slices)               # calib 与建树两次遍历，防生成器耗尽
    calib = build_calibration(slices)
    for s in slices:
        key = str(s.get("path") or "")
        segs = calib.get(key) or split_path(key)
        if not segs:
            continue
        node = root
        path_so_far = ""
        for i, seg in enumerate(segs, start=1):
            path_so_far = f"{path_so_far} > {seg}" if path_so_far else seg
            child = node.children.get(seg)
            if child is None:
                child = TreeNode(title=seg, path=path_so_far, depth=i)
                node.children[seg] = child
            node = child
        node.slices.append(s)
    return root


# ---------------------------------------------------------------- β 还原


@dataclass(frozen=True)
class RestoredFile:
    """还原出的一个「HTML 文件」（逻辑文档单元）."""

    file_path: str            # 文件节点的完整 path（" > " 连接，原样不剔段）
    file_title: str           # 文件节点末段名
    heading_title: str        # 首切片直属标题（path[-1]）
    folder_path: str          # 文件之上层级 "/" 连接（"" = 根目录）
    slices: tuple[dict[str, Any], ...]  # part_id 升序
    part_min: int
    part_max: int

    @property
    def slice_nids(self) -> tuple[str, ...]:
        return tuple(str(s.get("nid")) for s in self.slices)


@dataclass(frozen=True)
class RestoreResult:
    rule_version: str
    files: tuple[RestoredFile, ...]     # min(part_id) 升序
    folders: tuple[str, ...]            # 去重目录（含中间层）
    slice_count: int
    unassigned: int                     # path 为空/无法归属的切片数


def restore_files(slices: Iterable[dict[str, Any]]) -> RestoreResult:
    """β 规则还原：切片集合 → 文件集合 + 目录集合."""
    materialized = list(slices)
    calib = build_calibration(materialized)
    by_file: dict[str, list[dict[str, Any]]] = {}
    headings: dict[str, str] = {}       # file_path -> 首切片直属标题
    segs_map: dict[str, list[str]] = {}  # file_path -> 校准段（folder 推导用）
    unassigned = 0

    for s in sorted(materialized, key=lambda x: int(x.get("part_id") or 0)):
        key = str(s.get("path") or "")
        segs = calib.get(key) or split_path(key)
        if not segs:
            unassigned += 1
            continue
        if len(segs) >= 2:
            file_path = " > ".join(segs[:-1])
            heading = segs[-1]
        else:
            file_path = segs[0]         # α 兜底：自身即文件（根级孤页）
            heading = segs[0]
        by_file.setdefault(file_path, []).append(s)
        headings.setdefault(file_path, heading)
        segs_map.setdefault(file_path, segs)

    folders: set[str] = set()
    files: list[RestoredFile] = []
    for file_path, file_slices in by_file.items():
        # folder 链按校准段推导（合并段不可再按 " > " 拆）
        segs = segs_map[file_path]
        for i in range(1, max(len(segs) - 1, 0)):
            folders.add(sanitize_folder_path(segs[:i]))
        parts = [int(s.get("part_id") or 0) for s in file_slices]
        files.append(RestoredFile(
            file_path=file_path,
            file_title=segs[-2] if len(segs) >= 2 else segs[0],
            heading_title=headings[file_path],
            folder_path=sanitize_folder_path(segs[:-2]),
            slices=tuple(file_slices),  # 已按 part_id 升序（外层排序）
            part_min=min(parts), part_max=max(parts),
        ))
    files.sort(key=lambda f: f.part_min)
    return RestoreResult(
        rule_version=RULE_VERSION,
        files=tuple(files),
        folders=tuple(sorted(folders)),
        slice_count=len(materialized) - unassigned,
        unassigned=unassigned,
    )


def render_markdown(result: RestoreResult, *, with_source_markers: bool = True) -> str:
    """还原结果 → markdown 文本（预览渲染用；标明逻辑文档）."""
    lines = ["<!-- 由一张网切片重建的逻辑文档（非原始文件） -->"]
    for f in result.files:
        lines.append("")
        lines.append("# " + f.file_title)
        for s in f.slices:
            if with_source_markers:
                lines.append(f"<!-- nid={s.get('nid')} part_id={s.get('part_id')} -->")
            lines.append(clean_content(s.get("content")))
    return "\n".join(lines) + "\n"


def render_file_markdown(slices: list[dict[str, Any]], *, title: str = "") -> str:
    """单文件切片 → markdown（onenet 预览渲染；标注逻辑文档）."""
    lines = ["<!-- 由一张网切片重建的逻辑文档（非原始文件） -->", ""]
    heading = title or (slices[0].get("title") if slices else "") or ""
    if heading:
        lines.append(f"# {heading}")
    for s in sorted(slices, key=lambda x: int(x.get("part_id") or 0)):
        lines.append(f"<!-- nid={s.get('nid')} part_id={s.get('part_id')} -->")
        lines.append(clean_content(s.get("content")))
        lines.append("")
    return "\n".join(lines) + "\n"


def sanitize_folder_path(segs: list[str]) -> str:
    """段列表 → 清洗后的 "/" 目录链（空列表 → ""，即 KB 根）."""
    return "/".join(sanitize_folder_segment(s) for s in segs)


__all__ = [
    "RULE_VERSION", "RestoredFile", "RestoreResult", "TreeNode",
    "build_calibration", "build_path_tree", "clean_content",
    "render_file_markdown",
    "render_markdown", "restore_files", "sanitize_folder_path",
    "sanitize_folder_segment", "split_path", "top_folder_segment",
]
