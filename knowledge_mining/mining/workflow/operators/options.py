from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OperatorOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class EmptyOptions(OperatorOptions):
    pass


class ParseSegmentOptions(OperatorOptions):
    structural_context_mode: Literal["breadcrumb", "off"] = Field(
        "breadcrumb",
        alias="structuralContextMode",
        title="结构上下文",
        description="是否保留文档与章节路径。",
    )
    merge_small_segments: bool = Field(
        True,
        alias="mergeSmallSegments",
        title="合并同章节小分段",
    )
    min_segment_tokens: int = Field(
        80,
        ge=1,
        le=2048,
        alias="minSegmentTokens",
        title="最小分段 Token 数",
    )
    max_segment_tokens: int = Field(
        512,
        ge=1,
        le=8192,
        alias="maxSegmentTokens",
        title="最大分段 Token 数",
    )
    absorb_child_orphans: bool = Field(
        True,
        alias="absorbChildOrphans",
        title="子章节孤立段吸收到父章节",
    )
    merge_lead_into_child: bool = Field(
        True,
        alias="mergeLeadIntoChild",
        title="父章节引导语合入子章节",
    )
    enable_image_caption: bool = Field(
        False,
        alias="enableImageCaption",
        title="启用图片 VLM 图注",
        description=(
            "在分段阶段对抽出的图片（PDF/MD/HTML/DOCX 等）调用视觉模型生成图注"
            "（费用较高）。"
        ),
    )
    image_caption_model: str = Field(
        "glm-4.5v",
        alias="imageCaptionModel",
        title="图注视觉模型",
        description="llm_service.yaml 里 provider.models 的键名或 API model id。",
    )
    max_images_per_doc: int = Field(
        20,
        ge=0,
        le=200,
        alias="maxImagesPerDoc",
        title="每文档最多图注数",
    )
    fetch_remote_images: bool = Field(
        False,
        alias="fetchRemoteImages",
        title="下载远程图片",
        description=(
            "解析 Markdown/HTML 时下载 http(s) 图片到本地后再图注；"
            "默认跳过远程图以免同步拉网。"
        ),
    )

    @model_validator(mode="after")
    def validate_token_range(self) -> "ParseSegmentOptions":
        if self.min_segment_tokens > self.max_segment_tokens:
            raise ValueError("minSegmentTokens cannot exceed maxSegmentTokens")
        return self


class EnrichOptions(OperatorOptions):
    min_enrich_tokens: int = Field(
        30,
        ge=0,
        le=2048,
        alias="minEnrichTokens",
        title="最小增强段落 Token 数",
    )


class DiscourseOptions(OperatorOptions):
    window_size: int = Field(
        15,
        ge=2,
        le=100,
        alias="windowSize",
        title="关系分析窗口",
    )
    min_confidence: float = Field(
        0.5,
        ge=0,
        le=1,
        alias="minConfidence",
        title="最低关系置信度",
    )


class RetrievalUnitOptions(OperatorOptions):
    raw_text_unit: bool = Field(
        True,
        alias="rawTextUnit",
        title="生成原文检索单元",
    )
    generated_question_unit: bool = Field(
        True,
        alias="generatedQuestionUnit",
        title="生成问题检索单元",
    )
    table_row_unit: bool | None = Field(
        None,
        alias="tableRowUnit",
        title="生成表格行检索单元",
        description=(
            "默认 None：按域包 retrieval_policy.table_row 决定（'off' 则不生成），"
            "与 legacy 路径行为一致；显式 True/False 才覆盖域包。"
            "（旧默认 True 会无视域包的 table_row:off，导致表格按行爆出大量单元。）"
        ),
    )
    max_questions_per_segment: int = Field(
        2,
        ge=0,
        le=10,
        alias="maxQuestionsPerSegment",
        title="每段最大问题数",
    )
    min_questionworthy_tokens: int = Field(
        50,
        ge=0,
        le=2048,
        alias="minQuestionworthyTokens",
        title="最小可提问 Token 数",
    )


class EmbeddingOptions(OperatorOptions):
    unit_types: list[Literal["raw_text", "generated_question", "table_row"]] = Field(
        default_factory=lambda: ["raw_text", "generated_question", "table_row"],
        alias="unitTypes",
        title="向量化单元类型",
    )


class EntityExtractOptions(OperatorOptions):
    allow_out_of_schema: bool = Field(
        True,
        alias="allowOutOfSchema",
        title="允许本体外实体",
    )
    min_confidence: float = Field(
        0.5,
        ge=0,
        le=1,
        alias="minConfidence",
        title="最低实体置信度",
    )
    max_entities_per_segment: int = Field(
        20,
        ge=1,
        le=200,
        alias="maxEntitiesPerSegment",
        title="每段最大实体数",
    )


class EntityResolveOptions(OperatorOptions):
    auto_resolve_aliases: bool = Field(
        True,
        alias="autoResolveAliases",
        title="自动解析别名",
    )


class EntityRelationOptions(OperatorOptions):
    require_resolved_entities: bool = Field(
        False,
        alias="requireResolvedEntities",
        title="要求已归一实体",
        description="启用后，关系抽取必须位于实体归一之后。",
    )


class FinalizeOptions(OperatorOptions):
    publish_on_partial_failure: bool = Field(
        False,
        alias="publishOnPartialFailure",
        title="允许发布部分成功结果",
    )


OPTIONS_BY_OPERATOR: dict[str, type[OperatorOptions]] = {
    "input_ingest": EmptyOptions,
    "parse_segment": ParseSegmentOptions,
    "enrich": EnrichOptions,
    "discourse_line": DiscourseOptions,
    "contextual_retrieval_enrich": EmptyOptions,
    "retrieval_unit_build": RetrievalUnitOptions,
    "embedding": EmbeddingOptions,
    "entity_extract": EntityExtractOptions,
    "entity_resolve": EntityResolveOptions,
    "entity_relation_extract": EntityRelationOptions,
    "asset_persist": EmptyOptions,
    "entity_review_gate": EmptyOptions,
    "ontology_induction": EmptyOptions,
    "ontology_review_gate": EmptyOptions,
    "graph_write": EmptyOptions,
    "mining_finalize": FinalizeOptions,
}
