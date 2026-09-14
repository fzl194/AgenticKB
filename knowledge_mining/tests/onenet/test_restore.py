# -*- coding: utf-8 -*-
"""β 规则还原（restore.py）——含真实样例回归基线（32 条 UDG 切片）.

预期分组由独立脚本按「完整 path[-2]」推演固化（见 47 号 §四-2）：
7 个文件、8 个目录、文件按 min(part_id) 升序。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_mining.mining.onenet.restore import (
    RULE_VERSION, build_path_tree, clean_content, render_markdown,
    restore_files, split_path,
)

FIXTURE = Path(__file__).parent / "fixtures" / "udg_sample_32.json"

PKG = "UDG 20.18.0 产品文档 01（虚机容器）.hwics"
NAV = f"{PKG} > 特性部署 > 特性指南 > UDG特性指南 > IP基本特性 > IPFD-010000 接口与链路"


def _load_sample() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sample() -> list[dict]:
    return _load_sample()


# ---------------------------------------------------------------- 基础工具


def test_split_path_strips_empty_segments():
    assert split_path(" A > B >  C ") == ["A", "B", "C"]
    assert split_path(None) == []
    assert split_path("") == []


def test_clean_content_strips_table_markers():
    assert clean_content("[tbl_predict_start]| a | b |[tbl_predict_end]") == "| a | b |"
    assert clean_content(None) == ""


# ---------------------------------------------------------------- 路径树


def test_build_tree_nested_counts_and_parts():
    slices = [
        {"nid": "a", "part_id": 2, "path": f"{PKG} > L1 > L2 > 叶A"},
        {"nid": "b", "part_id": 3, "path": f"{PKG} > L1 > L2 > 叶A"},   # 同叶双切片
        {"nid": "c", "part_id": 1, "path": f"{PKG} > L1"},
    ]
    root = build_path_tree(slices)
    l1 = root.children["L1"]
    assert l1.slice_count_total == 3          # 聚合后代
    assert l1.direct_slice_count == 1         # 自身直属
    assert l1.part_min == 1 and l1.part_max == 3
    leaf = l1.children["L2"].children["叶A"]
    assert leaf.slice_count_total == 2
    assert [c.title for c in root.children.values()] == ["L1"]  # 保序


def test_build_tree_package_segment_dropped():
    root = build_path_tree([{"nid": "x", "part_id": 1, "path": f"{PKG} > A > B"}])
    assert list(root.children) == ["A"]
    assert root.children["A"].children["B"].depth == 2


# ---------------------------------------------------------------- β 还原（真实样例回归）


def test_real_sample_grouping_pinned(sample):
    result = restore_files(sample)
    assert result.rule_version == RULE_VERSION == "beta-1"
    assert result.unassigned == 0
    assert result.slice_count == 32
    assert len(result.files) == 7

    # 文件顺序 = min(part_id) 升序；标题/切片数/范围逐一钉死
    expect = [
        ("IPFD-010001 接口管理", 1, 12294, 12294),
        ("IPFD-010001 接口管理特性概述", 12, 12295, 12306),
        ("实现原理", 2, 12307, 12311),
        ("控制接口震荡特性", 3, 12308, 12310),
        ("IPFD-010002 支持VLAN子接口", 1, 12312, 12312),
        ("IPFD-010002 支持VLAN子接口特性概述", 12, 12313, 12324),
        ("实现原理", 1, 12325, 12325),
    ]
    assert [(f.file_title, len(f.slices), f.part_min, f.part_max)
            for f in result.files] == expect

    # 两个「实现原理」是不同文件（完整 path 不同）
    impl_files = [f for f in result.files if f.file_title == "实现原理"]
    assert len(impl_files) == 2
    assert {f.folder_path.split("/")[-1] for f in impl_files} == {
        "IPFD-010001 接口管理", "IPFD-010002 支持VLAN子接口",
    }

    # 文件内切片按 part_id 升序
    for f in result.files:
        pids = [int(s["part_id"]) for s in f.slices]
        assert pids == sorted(pids)

    # 目录集合（8 个，含中间层）
    assert len(result.folders) == 8
    assert "特性部署" in result.folders
    assert ("特性部署/特性指南/UDG特性指南/IP基本特性/IPFD-010000 接口与链路"
            in result.folders)


def test_real_sample_heading_titles(sample):
    result = restore_files(sample)
    by_part = {f.part_min: f for f in result.files}
    assert by_part[12294].heading_title == "IPFD-010001 接口管理特性概述"
    assert by_part[12295].heading_title == "定义"
    assert by_part[12308].heading_title == "产生原因"


# ---------------------------------------------------------------- 构造用例


def test_alpha_fallback_single_segment():
    slices = [{"nid": "x", "part_id": 1, "path": f"{PKG} > 孤页", "content": "c"}]
    result = restore_files(slices)
    assert len(result.files) == 1
    f = result.files[0]
    assert f.file_path == "孤页"
    assert f.heading_title == "孤页"
    assert f.folder_path == ""


def test_empty_path_slices_counted_unassigned():
    slices = [
        {"nid": "ok", "part_id": 1, "path": f"{PKG} > A > B", "content": "c"},
        {"nid": "bad", "part_id": 2, "path": "", "content": "c"},
    ]
    result = restore_files(slices)
    assert result.unassigned == 1
    assert result.slice_count == 1
    assert len(result.files) == 1


def test_same_leaf_multi_slice_order_by_part_id():
    # 同叶两切片（demo 实测：同一文件页切多块）→ 同文件内 part_id 升序
    slices = [
        {"nid": "s2", "part_id": 20, "path": f"{PKG} > P > 事件列表", "content": "后"},
        {"nid": "s1", "part_id": 19, "path": f"{PKG} > P > 事件列表", "content": "前"},
    ]
    result = restore_files(slices)
    assert [s["nid"] for s in result.files[0].slices] == ["s1", "s2"]


def test_table_marker_cleaned_in_render():
    slices = [{
        "nid": "t1", "part_id": 1, "path": f"{PKG} > P > Q",
        "content": "说明文字\n[tbl_predict_start]| 项目 | 说明 |\n| --- | --- |\n"
                   "| a | b |[tbl_predict_end]\n结尾",
    }]
    md = render_markdown(restore_files(slices))
    assert "[tbl_predict" not in md
    assert "| 项目 | 说明 |" in md
    assert "由一张网切片重建的逻辑文档" in md
    assert "nid=t1 part_id=1" in md


def test_render_without_source_markers():
    slices = [{"nid": "t1", "part_id": 1, "path": f"{PKG} > P > Q", "content": "正文"}]
    md = render_markdown(restore_files(slices), with_source_markers=False)
    assert "nid=" not in md
    assert "正文" in md
