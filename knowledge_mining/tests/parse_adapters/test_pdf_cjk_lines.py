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
    def test_repeated_line_marked_page_header(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            classify_furniture,
        )
        # 跨 6 页完全相同的长行 -> page_header；其余不变
        lines = [
            {"text": "硕士学位论文某复合催化剂研究", "pages": {0, 1, 2, 3, 4, 5}},
            {"text": "正常正文一句话。", "pages": {0, 1}},
        ]
        verdict = classify_furniture(lines, page_count=73)
        assert verdict[0] == "page_header"
        assert verdict[1] is None

    def test_pure_number_short_line_is_page_number(self):
        from knowledge_mining.mining.parse_adapters.native_pdf import (
            classify_furniture,
        )
        lines = [
            {"text": "17", "pages": {16}},
            {"text": "VI", "pages": {5}},
            {"text": "2023 年数据", "pages": {6}},
        ]
        verdict = classify_furniture(lines, page_count=73)
        assert verdict[0] == "page_number"
        assert verdict[1] == "page_number"
        assert verdict[2] is None  # 含年份数字的长行不是页码
