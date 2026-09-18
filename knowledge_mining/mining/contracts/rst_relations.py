"""Single source of truth for RST discourse relations.

Canonical form is the uppercase verb (third-person singular) matching how
domain.yaml prompts ask the LLM to label relations. The lowercase form is
what the retired discourse projection used as relation types.

When adding a new relation here, also extend the DB CHECK constraint on
the legacy relation projection
002_asset_core_postgresql.sql) and the structural-relation portion of
VALID_RELATION_TYPES in models.py stays untouched.
"""
from __future__ import annotations


RST_RELATIONS: dict[str, tuple[str, str]] = {
    # 10 standard RST relations
    "ELABORATES":     ("elaborates",     "后段对前段进行详述/展开"),
    "SEQUENCES":      ("sequences",      "两段按时间/步骤顺序排列"),
    "CAUSES":         ("causes",         "前段是后段的原因"),
    "EVIDENCES":      ("evidences",      "后段为前段提供证据/依据"),
    "BACKGROUNDS":    ("backgrounds",    "前段为后段提供背景知识"),
    "EXEMPLIFIES":    ("exemplifies",    "后段是前段的具体例子"),
    "CONTRASTS_WITH": ("contrasts_with", "两段形成对比或转折"),
    "CONCEDES":       ("concedes",       "承认一方观点但强调另一方"),
    "CONDITIONS":     ("conditions",     "前段是后段的触发条件"),
    "PURPOSES":       ("purposes",       "后段说明前段操作的目的/意图"),
    # 5 extensions used by cloud_core_network and other technical domains
    "RESULTS_IN":     ("results_in",     "前段操作产生后段的结果"),
    "SUMMARIZES":     ("summarizes",     "后段对前段内容进行概括总结"),
    "JUSTIFIES":      ("justifies",      "后段解释前段设计/操作的理由"),
    "ENABLES":        ("enables",        "前段提供使后段成为可能的机制或能力"),
    "PARALLELS":      ("parallels",      "两段描述并列的功能或流程"),
}

LLM_TO_DB_RELATION: dict[str, str] = {k: v[0] for k, v in RST_RELATIONS.items()}
RST_DB_VALUES: frozenset[str] = frozenset(LLM_TO_DB_RELATION.values())
RST_LLM_LABELS: frozenset[str] = frozenset(LLM_TO_DB_RELATION.keys())


# Nuclearity per RST relation, keyed by the lowercase DB value. Decides which
# segment is the discourse nucleus (core info) vs satellite (supporting), used to
# derive asset_raw_segments.metadata_json["discourse_role"]. Direction is resolved
# against segment order (segment_index), not the LLM source/target labels.
MULTINUCLEAR = "multinuclear"  # both ends are nucleus (no core/support asymmetry)
MONO_PREV = "mono_prev"        # earlier segment (smaller segment_index) is nucleus
MONO_POST = "mono_post"        # later segment (larger segment_index) is nucleus

RST_NUCLEARITY: dict[str, str] = {
    "elaborates":     MONO_PREV,   # 前段是被详述的主干
    "exemplifies":    MONO_PREV,   # 前段是被举例的主干
    "evidences":      MONO_PREV,   # 前段是被佐证的论断
    "justifies":      MONO_PREV,   # 前段是被解释的设计/操作
    "purposes":       MONO_PREV,   # 前段是操作本身
    "results_in":     MONO_PREV,   # 前段是产生结果的操作  ⚠ 方向待与 serving 对齐
    "backgrounds":    MONO_POST,   # 后段是被铺垫的主体
    "conditions":     MONO_POST,   # 后段是受条件触发的主体
    "enables":        MONO_POST,   # 后段是被使能的能力     ⚠ 方向待与 serving 对齐
    "causes":         MONO_POST,   # 后段是结果/现象        ⚠ 方向待与 serving 对齐
    "concedes":       MONO_POST,   # 后段是被强调的一方      ⚠ 方向待与 serving 对齐
    "summarizes":     MULTINUCLEAR, # ⚠ 方向待与 serving 对齐
    "sequences":      MULTINUCLEAR,
    "contrasts_with": MULTINUCLEAR,
    "parallels":      MULTINUCLEAR,
}
