"""Segment Compiler layer (M5, SRS §4.12 / §C11).

知识快照的编译视图：element graph -> 检索切片（结构边界优先、标题链
注入、表格行带表头、图文绑定、每条切片到原文元素/证据的映射）。
"""
from knowledge_mining.mining.segment_compiler.compiler import compile_segments

__all__ = ["compile_segments"]
