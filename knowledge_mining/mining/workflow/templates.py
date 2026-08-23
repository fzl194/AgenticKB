from __future__ import annotations

from .graph import EdgeDef, NodeDef, OutputDef, WorkflowGraph


_POSITIONS = {
    "input_ingest": (40, 260),
    "document_parse": (180, 260),
    "segment_compile": (380, 260),
    "enrich": (520, 80),
    "discourse_line": (760, 80),
    "contextual_retrieval_enrich": (1000, 80),
    "retrieval_unit_build": (1240, 80),
    "embedding": (1480, 80),
    "entity_extract": (520, 440),
    "entity_resolve": (760, 440),
    "entity_relation_extract": (1000, 440),
    "asset_persist": (1720, 260),
    "entity_review_gate": (1960, 440),
    "ontology_induction": (2200, 440),
    "ontology_review_gate": (2440, 440),
    "graph_write": (2680, 440),
    "mining_finalize": (2920, 260),
}

_DOCUMENT_DISCOURSE_ORDER = (
    "enrich",
    "discourse_line",
    "contextual_retrieval_enrich",
    "retrieval_unit_build",
    "embedding",
)
_DOCUMENT_ONTOLOGY_ORDER = (
    "entity_extract",
    "entity_resolve",
    "entity_relation_extract",
)
_TEMPLATE_BRANCHES = {
    "minimal": ((), (), False),
    "fast_retrieval": (("retrieval_unit_build", "embedding"), (), False),
    "discourse_only": (_DOCUMENT_DISCOURSE_ORDER, (), False),
    "entity_graph": ((), _DOCUMENT_ONTOLOGY_ORDER, False),
    "hybrid_knowledge": (
        _DOCUMENT_DISCOURSE_ORDER,
        _DOCUMENT_ONTOLOGY_ORDER,
        False,
    ),
    "ontology_only": ((), _DOCUMENT_ONTOLOGY_ORDER, True),
    "full": (_DOCUMENT_DISCOURSE_ORDER, _DOCUMENT_ONTOLOGY_ORDER, True),
}

EDITABLE_BY_TEMPLATE = {
    name: set(discourse_branch)
    | set(entity_branch)
    | ({"ontology_induction"} if includes_ontology_induction else set())
    for name, (
        discourse_branch,
        entity_branch,
        includes_ontology_induction,
    ) in _TEMPLATE_BRANCHES.items()
}


def _node(operator_type: str) -> NodeDef:
    x, y = _POSITIONS[operator_type]
    return NodeDef(
        node_id=operator_type,
        operator_type=operator_type,
        params={},
        ui={"x": x, "y": y},
    )


def _connect_chain(types: tuple[str, ...], slot: str) -> list[EdgeDef]:
    return [
        EdgeDef(source, slot, target, slot)
        for source, target in zip(types, types[1:])
    ]


def _template(template_name: str) -> WorkflowGraph:
    """Build one v2 paradigm with the fixed parse-and-compile head."""
    parse_ops = ("document_parse", "segment_compile")
    discourse_branch, entity_branch, includes_ontology_induction = (
        _TEMPLATE_BRANCHES[template_name]
    )

    types = ["input_ingest", *parse_ops]
    types.extend(discourse_branch)
    types.extend(entity_branch)
    types.append("asset_persist")
    if entity_branch:
        types.append("entity_review_gate")
        if includes_ontology_induction:
            types.extend(("ontology_induction", "ontology_review_gate"))
        types.append("graph_write")
    types.append("mining_finalize")

    edges = [
        EdgeDef("input_ingest", "rawFiles", parse_ops[0], "rawFiles"),
        EdgeDef(parse_ops[-1], "documents", "asset_persist", "documents"),
    ]
    if len(parse_ops) > 1:
        for upstream, downstream in zip(parse_ops, parse_ops[1:]):
            edges.append(
                EdgeDef(upstream, "documents", downstream, "documents")
            )
    if discourse_branch:
        edges.append(
            EdgeDef(parse_ops[-1], "documents", discourse_branch[0], "documents")
        )
        edges.extend(_connect_chain(discourse_branch, "documents"))
        edges.append(
            EdgeDef(
                discourse_branch[-1],
                "documents",
                "asset_persist",
                "discourseAssets",
            )
        )
    if entity_branch:
        edges.append(
            EdgeDef(parse_ops[-1], "documents", entity_branch[0], "documents")
        )
        edges.extend(_connect_chain(entity_branch, "documents"))
        edges.append(
            EdgeDef(
                entity_branch[-1],
                "documents",
                "asset_persist",
                "ontologyAssets",
            )
        )
        global_chain = ["asset_persist", "entity_review_gate"]
        if includes_ontology_induction:
            global_chain.extend(("ontology_induction", "ontology_review_gate"))
        global_chain.extend(("graph_write", "mining_finalize"))
    else:
        global_chain = ["asset_persist", "mining_finalize"]
    edges.extend(_connect_chain(tuple(global_chain), "finalizeInput"))

    return WorkflowGraph(
        schema_version="2.0",
        nodes=tuple(_node(operator_type) for operator_type in types),
        edges=tuple(edges),
        output=OutputDef("mining_finalize", "result"),
    )


_BUILTIN_TEMPLATES = {name: _template(name) for name in _TEMPLATE_BRANCHES}


def builtin_templates() -> dict[str, WorkflowGraph]:
    return dict(_BUILTIN_TEMPLATES)


def builtin_templates_v2() -> dict[str, WorkflowGraph]:
    """Compatibility import name for the only supported template generation."""
    return builtin_templates()
