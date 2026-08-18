"""PDF CJK 行组装与标题档位（真实中文论文验收驱动的修复，TDD）.

真实语料暴露的三问题（王灿论文验收 2026-08-17）：
1. extract_words 按空格分词 -> 中文连排被拆成"镍/基/M/O"碎片；
   修法：直接用 chars 聚行，CJK-aware 拼接。
2. 学术三线表无横线 -> 默认 find_tables 找不到；
   修法：lines 策略为空时回退 text 策略。
3. heading level 全 1 -> 标题树建不起来；
   修法：heading 字号映射到文档级档位（排序去重 -> level）。
"""
from __future__ import annotations

import pytest

from knowledge_mining.mining.parse_adapters.native_pdf import (
    group_chars_into_lines,
    heading_levels_for,
)


def _c(x0, top, text, size=12.0, x1=None):
    return {
        "text": text, "x0": x0, "x1": x1 if x1 is not None else x0 + size,
        "top": top, "bottom": top + size, "size": size,
    }


class TestCjkLineAssembly:
    def test_cjk_run_joined_without_spaces(self):
        chars = [_c(0, 0, "镍"), _c(12, 0, "基"), _c(24, 0, "材"), _c(36, 0, "料")]
        lines = group_chars_into_lines(chars)
        assert len(lines) == 1
        assert lines[0]["text"] == "镍基材料"

    def test_latin_words_keep_spaces(self):
        # "CO2" 词内紧邻（gap≈1pt），"re" 前有大词间距（gap≈6pt > 0.15×12）
        chars = [
            _c(0, 0, "C", x1=9), _c(10, 0, "O", x1=19), _c(20, 0, "2", x1=27),
            _c(33, 0, "r", x1=40), _c(41, 0, "e", x1=48),
        ]
        lines = group_chars_into_lines(chars)
        assert lines[0]["text"].startswith("CO2 re")

    def test_cjk_latin_boundary_gets_space(self):
        chars = [
            _c(0, 0, "镍", x1=12), _c(12, 0, "基", x1=24),
            _c(24, 0, "M", x1=33), _c(34, 0, "O", x1=43), _c(44, 0, "F", x1=52),
        ]
        lines = group_chars_into_lines(chars)
        assert lines[0]["text"] == "镍基 MOF"

    def test_lines_split_by_top(self):
        chars = [_c(0, 0, "上"), _c(12, 0, "行"), _c(0, 20, "下"), _c(12, 20, "行")]
        lines = group_chars_into_lines(chars)
        assert [l["text"] for l in lines] == ["上行", "下行"]

    def test_bbox_covers_line(self):
        chars = [_c(5, 10, "甲", size=14.0), _c(19, 11, "乙", size=14.0)]
        lines = group_chars_into_lines(chars)
        assert lines[0]["bbox"][0] == 5
        assert lines[0]["size"] == 14.0


class TestHeadingLevels:
    def test_font_sizes_map_to_ranks(self):
        # 26pt 封面题 / 16pt 章 / 14pt 节 -> level 1/2/3
        sizes = [26.0, 16.0, 14.0, 16.0, 14.0, 26.0]
        levels = heading_levels_for(sizes)
        assert levels == [1, 2, 3, 2, 3, 1]

    def test_single_size_all_level_one(self):
        assert heading_levels_for([18.0, 18.0]) == [1, 1]


class TestHeaderFooterAnnotation:
    """家具标注已迁至 Structural Reconciler（IR 级，整改轮）——
    断言保留在此以验证迁移后 adapter 不再做文档级家具判定，端到端
    家具行为在 tests/parse_reconciler/test_reconciler.py 覆盖。"""

    def test_adapter_no_longer_types_furniture(self):
        """adapter 产出的块不再预分家具类型（职责迁出）。"""
        from knowledge_mining.mining.parse_adapters import native_pdf

        assert not hasattr(native_pdf, "classify_furniture")
        assert not hasattr(native_pdf, "_annotate_furniture")


def _line(text, x0, top, x1, bottom, size=12.0):
    return {"text": text, "bbox": (x0, top, x1, bottom), "size": size}


class TestParagraphAssembly:
    """行 -> 段落聚合（段内行距 vs 段间行距，真实论文 gap 7.4/22.8）."""

    def test_lines_merge_into_paragraph_by_gap(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            group_lines_into_paragraphs,
        )
        lines = [
            _line("第一段第一行", 79, 100, 300, 110),
            _line("第一段第二行", 79, 117.4, 300, 127.4),  # gap 7.4 同段
            _line("第二段第一行", 79, 150, 300, 160),      # gap 22.6 断段
        ]
        paras = group_lines_into_paragraphs(lines, intra_gap_threshold=13.5)
        assert len(paras) == 2
        assert paras[0]["text"].startswith("第一段第一行")
        assert "第一段第二行" in paras[0]["text"]
        assert paras[1]["text"] == "第二段第一行"

    def test_cjk_paragraph_text_seamless(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            group_lines_into_paragraphs,
        )
        lines = [
            _line("中文段落跨行", 79, 100, 300, 110),
            _line("接排不断开", 79, 117.4, 300, 127.4),
        ]
        paras = group_lines_into_paragraphs(lines, intra_gap_threshold=13.5)
        assert paras[0]["text"] == "中文段落跨行接排不断开"

    def test_paragraph_bbox_covers_all_lines(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            group_lines_into_paragraphs,
        )
        lines = [
            _line("甲", 79, 100, 200, 110),
            _line("乙", 90, 117, 300, 127),
        ]
        paras = group_lines_into_paragraphs(lines, intra_gap_threshold=13.5)
        assert paras[0]["bbox"] == (79, 100, 300, 127)

    def test_single_line_stands_alone(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            group_lines_into_paragraphs,
        )
        paras = group_lines_into_paragraphs(
            [_line("孤行", 79, 100, 200, 110)], intra_gap_threshold=13.5
        )
        assert len(paras) == 1 and paras[0]["text"] == "孤行"


class TestTableQualityFilter:
    """text 回退策略的假表过滤（page5 英文摘要被当 78×6 表，空格子大半）."""

    def test_sparse_grid_rejected(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            _table_grid_effective_ratio,
        )
        grid = [["a", None, None], [None, None, "b"], [None, None, None]]
        assert _table_grid_effective_ratio(grid) < 0.5

    def test_dense_grid_accepted(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            _table_grid_effective_ratio,
        )
        grid = [["h1", "h2", "h3"], ["a", "b", "c"], ["d", "e", "f"]]
        assert _table_grid_effective_ratio(grid) >= 0.5


class TestHeadingFrequencyFilter:
    """低频字号档（封面/内封装饰字，出现 1-2 次）不参与档位表."""

    def test_rare_sizes_do_not_create_levels(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            heading_levels_for,
        )
        # 26pt 出现 2 次（封面装饰）、16pt×5（章）、14pt×8（节）
        sizes = [26.0, 26.0] + [16.0] * 5 + [14.0] * 8
        levels = heading_levels_for(sizes, min_occurrences=3)
        # 26pt 被剔除档位表，映射到最近的高频档（16pt -> level 1）
        assert levels[0] == 1 and levels[1] == 1
        assert levels[2] == 1  # 16pt -> 1
        assert levels[7] == 2  # 14pt -> 2

    def test_no_frequent_sizes_all_level_one(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            heading_levels_for,
        )
        assert heading_levels_for([], min_occurrences=3) == []


class TestNumberedHeading:
    """编号标题模式（与正文同字号，字号启发式天然盲区）."""

    def test_chapter_pattern(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            numbered_heading_level,
        )
        assert numbered_heading_level("第一章 绪论") == 1
        assert numbered_heading_level("第十二章 结论") == 1

    def test_section_depth(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            numbered_heading_level,
        )
        assert numbered_heading_level("1.2 光催化还原技术概述") == 2
        assert numbered_heading_level("1.2.2 光催化还原技术的影响因素") == 3
        assert numbered_heading_level("2.3.1.2 深层编号") == 4

    def test_plain_text_not_heading(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            numbered_heading_level,
        )
        assert numbered_heading_level("这是一个普通句子。") is None
        assert numbered_heading_level("2023 年数据统计如下") is None  # 年份非标题
        assert numbered_heading_level("表 2-2 主要仪器") is None


class TestHeadingContinuation:
    """跨行标题合并（通用规则：相邻同字号 heading 行、行距为段内级）."""

    def test_multiline_heading_merged(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            merge_heading_runs,
        )
        runs = [
            _line("第三章 某材料的制备及其性能", 79, 100, 400, 114, size=16),
            _line("研究", 79, 118, 120, 132, size=16),  # gap 4pt 续行
            _line("3.1 引言", 79, 180, 160, 194, size=16),  # gap 48pt 断开
        ]
        merged = merge_heading_runs(runs, intra_gap=13.5)
        assert len(merged) == 2
        assert merged[0]["text"] == "第三章 某材料的制备及其性能 研究"
        assert merged[1]["text"] == "3.1 引言"


class TestTableDoesNotSwallowHeadings:
    """表格 bbox 吞标题防御（通用：heading 行不属表格，顶边收缩）.

    整改轮：``_shrink_table_below_headings`` 死代码已删除，行为由
    ``shrink_bbox_to_data_core``（数据核心收缩）承担——断言迁移到
    新函数：标题行（单片段）在上、数据行（多片段）在下时，顶边
    收缩到数据核心首行。"""

    def test_heading_row_excluded_and_bbox_shrunk(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            shrink_bbox_to_data_core,
        )
        region_lines = [
            {"text": "第二章 实验试剂", "bbox": (79, 90, 300, 104),
             "_chars": []},
            {"text": "试剂名称", "bbox": (79, 130, 160, 144),
             "_chars": []},
        ]
        # 标题行单片段、数据行多片段：用 _chars 提供片段信号
        region_lines[1]["_chars"] = [
            {"x0": 79, "x1": 100, "text": "试剂", "top": 130},
            {"x0": 130, "x1": 160, "text": "名称", "top": 130},
        ]
        out = shrink_bbox_to_data_core((79, 83, 524, 400), region_lines)
        assert out[1] >= 130  # 顶边压到数据核心首行（标题行不在表内）


class TestLigatureSafety:
    """连字/多字符 text 防御（对抗自审发现：fi 连字会崩单字符假设）."""

    def test_multichar_ligature_handled(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            group_chars_into_lines,
        )
        chars = [_c(0, 0, "fi", x1=12), _c(12, 0, "镍", x1=24, size=12.0)]
        lines = group_chars_into_lines(chars)
        assert lines[0]["text"] == "fi 镍"


class TestTwoLineTrapTable:
    """上下框陷阱防御：仅两条横线夹住的正文不是表格（通用语义）.

    真表格的文本在列方向上对齐成 >=2 列（多行共享相近的 x 起点）；
    普通正文行 x0 高度一致但每行是一整段——用"行内文本是否可切出
    >=2 个列簇"判定。
    """

    def test_trap_rejected(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            _region_has_column_structure,
        )
        # 普通正文行：每行一个连续文本块，无列簇
        region = [
            {"x0": 79, "x1": 500, "text": "第二章 实验试剂及研究方法"},
            {"x0": 79, "x1": 500, "text": "2.1 实验试剂"},
            {"x0": 79, "x1": 500, "text": "本论文实验部分所涉及的全部化学试剂"},
        ]
        assert _region_has_column_structure(region) is False

    def test_real_columns_accepted(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            _region_has_column_structure,
        )
        # 表格行：试剂名 | 规格 | 厂商 三列，行间 x0 形成簇
        region = [
            {"x0": 79, "x1": 200, "text": "乙腈"},
            {"x0": 210, "x1": 320, "text": "分析纯"},
            {"x0": 330, "x1": 480, "text": "广东光华"},
            {"x0": 79, "x1": 200, "text": "三乙醇胺"},
            {"x0": 210, "x1": 320, "text": "分析纯"},
            {"x0": 330, "x1": 480, "text": "光华科技"},
        ]
        assert _region_has_column_structure(region) is True


class TestDataCoreShrink:
    """表格 bbox 收缩到数据核心区（通用：列对齐行块反推边界）."""

    def test_shrink_to_multifragment_rows(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            shrink_bbox_to_data_core,
        )
        # 上半部单片段（标题/正文），下半部三片段（表头+数据）
        region_lines = [
            {"text": "第二章", "bbox": (79, 98, 400, 110)},
            {"text": "2.1", "bbox": (79, 140, 300, 152)},
            {"text": "正文段落", "bbox": (79, 171, 500, 183)},
            {"text": "表头A 表头B", "bbox": (79, 269, 500, 281), "segments": 3},
            {"text": "d1 d2", "bbox": (79, 290, 500, 302), "segments": 3},
            {"text": "e1 e2", "bbox": (79, 311, 500, 323), "segments": 3},
            {"text": "f1 f2", "bbox": (79, 332, 500, 344), "segments": 3},
        ]
        # 多片段行 4/7 >= 40% 且收缩是"剥掉上部单片段区"——允许
        new_box = shrink_bbox_to_data_core((79, 60, 508, 350), region_lines)
        assert new_box[1] >= 250  # 顶边压到多片段行块

    def test_no_multifragment_no_shrink(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            shrink_bbox_to_data_core,
        )
        lines = [{"text": "只有标题和正文", "bbox": (79, 98, 400, 110), "segments": 1}]
        assert shrink_bbox_to_data_core((79, 60, 508, 310), lines) == (79, 60, 508, 310)


class TestFragmentBlockClustering:
    """片段块聚类（第四层表格候选来源，验收 v7，通用）."""

    def test_cluster_multifragment_lines_into_blocks(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            cluster_fragment_lines,
        )
        lines = [
            {"text": "正文行", "bbox": (79, 100, 500, 112), "segments": 1},
            {"text": "表头 c1 c2", "bbox": (79, 269, 500, 281), "segments": 3},
            {"text": "d1 d2 d3", "bbox": (79, 298, 500, 310), "segments": 3},
            {"text": "e1 e2 e3", "bbox": (79, 326, 500, 338), "segments": 3},
            {"text": "隔开的另一块", "bbox": (79, 500, 500, 512), "segments": 3},
            {"text": "f1 f2", "bbox": (79, 530, 500, 542), "segments": 2},
        ]
        blocks = cluster_fragment_lines(lines, max_gap=45, min_rows=2)
        assert len(blocks) == 2
        assert len(blocks[0]) == 3
        assert blocks[0][0]["text"].startswith("表头")


class TestTwoColumnSplit:
    """双栏切分（通用：栏间空白带检测）."""

    def test_column_gap_detected_and_split(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            detect_column_split,
        )
        # 左栏 x0≈79-300，右栏 x0≈312-533，中缝 300-312
        lines = [
            {"text": "left col line", "bbox": (79, 100, 295, 112)},
            {"text": "left col 2", "bbox": (79, 120, 290, 132)},
            {"text": "right col line", "bbox": (315, 100, 530, 112)},
            {"text": "right col 2", "bbox": (318, 120, 525, 132)},
            # 通栏行（跨两栏）
            {"text": "full width title", "bbox": (79, 60, 530, 74)},
        ]
        result = detect_column_split(lines, page_width=612, min_lines=4)
        assert result is not None
        boundary, fullwidth = result
        assert 295 < boundary < 315
        assert len(fullwidth) == 1  # 标题识别为通栏

    def test_single_column_returns_none(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            detect_column_split,
        )
        lines = [
            {"text": "only left", "bbox": (79, 100, 500, 112)},
            {"text": "still left", "bbox": (79, 120, 490, 132)},
        ]
        assert detect_column_split(lines, page_width=612) is None
