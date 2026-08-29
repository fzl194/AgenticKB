from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OperatorOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class EmptyOptions(OperatorOptions):
    pass


class ParseSegmentOptions(OperatorOptions):
    """Legacy pipeline-internal options retained until its v2 replacement lands.

    This model is intentionally not registered in the v2 operator catalog.
    """

    structural_context_mode: Literal["breadcrumb", "off"] = Field(
        "breadcrumb", alias="structuralContextMode"
    )
    merge_small_segments: bool = Field(True, alias="mergeSmallSegments")
    min_segment_tokens: int = Field(80, ge=1, le=2048, alias="minSegmentTokens")
    max_segment_tokens: int = Field(512, ge=1, le=8192, alias="maxSegmentTokens")
    absorb_child_orphans: bool = Field(True, alias="absorbChildOrphans")
    merge_lead_into_child: bool = Field(True, alias="mergeLeadIntoChild")
    enable_image_caption: bool = Field(False, alias="enableImageCaption")
    image_caption_model: str = Field("glm-4.5v", alias="imageCaptionModel")
    max_images_per_doc: int = Field(20, ge=0, le=200, alias="maxImagesPerDoc")
    fetch_remote_images: bool = Field(False, alias="fetchRemoteImages")

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
    """embedding 参数（批次8 M4，24 号 §5.7）：默认矩阵之上的最小覆盖面."""

    strategy_overrides: dict[str, str] = Field(
        default_factory=dict,
        alias="strategyOverrides",
        title="按表示类型覆盖策略",
        description="representation_type → skip/isolated/structural 等；默认矩阵见 emb-policy-1。",
    )
    strategy_fallbacks: dict[str, str] = Field(
        default_factory=dict,
        alias="strategyFallbacks",
        title="按表示类型显式降级",
        description="provider 不支持覆盖策略时的显式 fallback；未配置则编译/执行期显式失败。",
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


class DocumentParseOptions(OperatorOptions):
    """document_parse 参数（SRS §10.2：只表达解析策略；M6/R5 档位）."""

    quality_profile: Literal["default", "strict", "lenient"] = Field(
        "default",
        alias="qualityProfile",
        title="质量档位",
        description="strict 提高入库门槛，lenient 放宽为尽量入库。",
    )
    max_backend_attempts: int = Field(
        3,
        alias="maxBackendAttempts",
        title="后端尝试预算",
        ge=1,
        le=5,
        description="主解析失败后自动切换备用的总次数上限。",
    )


class RetrieProjectOptions(OperatorOptions):
    """retrieval_unit_project 参数（批次8 M2，24 号 §5.4）.

    只表达投影开关；类型矩阵与 eligibility 默认固定在契约层（versioned
    default），用户可覆盖项收敛到最小集。
    """

    include_sections: bool = Field(
        False, alias="includeSections", title="生成章节表示",
        description="按真实标题树聚合有界直接内容（不生成 LLM 摘要）。",
    )


class SegmentCompileOptions(OperatorOptions):
    """segment_compile 参数（SRS §10.2：只表达分段策略；M6/R2 档位）.

    字段与 SegmentPolicy 一一对应（handler 层映射）。
    """

    max_tokens: int = Field(2048, alias="maxTokens", title="切片上限", ge=64)
    min_tokens: int = Field(512, alias="minTokens", title="切片下限", ge=1)
    merge_adjacent_paragraphs: bool = Field(
        True, alias="mergeAdjacentParagraphs", title="合并同章节相邻段",
    )
    inject_heading_context: bool = Field(
        True, alias="injectHeadingContext", title="注入章节路径",
    )
    table_view: Literal["whole", "rows", "both"] = Field(
        "whole", alias="tableView", title="表格视图",
        description="整表（默认，一表一片）/ 逐行（行自带表头上下文）/ 两者。",
    )
    include_figure_captions: bool = Field(
        True, alias="includeFigureCaptions", title="图题编译",
    )


# 批次8 M0（24 号 §11）：正式算子参数面只覆盖目录内算子。
# 退役算子（enrich/discourse_line/contextual_retrieval_enrich/retrieval_unit_build）
# 与研究算子（entity/ontology/graph_write）的 Options 类暂保留定义——
# pipeline 阶段实现与实体研究代码仍引用，随 M1/M2 bundle 重构与
# retrieval_unit_project 落地后一并清除；此处仅断开注册映射。
OPTIONS_BY_OPERATOR: dict[str, type[OperatorOptions]] = {
    "input_ingest": EmptyOptions,
    "document_parse": DocumentParseOptions,
    "segment_compile": SegmentCompileOptions,
    "retrieval_unit_project": RetrieProjectOptions,
    "embedding": EmbeddingOptions,
    "asset_persist": EmptyOptions,
    "mining_finalize": FinalizeOptions,
}
