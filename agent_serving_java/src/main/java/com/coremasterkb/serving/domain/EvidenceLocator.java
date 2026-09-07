package com.coremasterkb.serving.domain;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * A1 证据精确定位（37/38 号）——{@code EvidenceSource.locator} 的公开协议形状。
 *
 * <p>只增不改：全部字段 null 时整体省略（NON_NULL）；{@code kind} 词表与挖掘侧
 * {@code asset_source_locators.locator_kind} 单一真相源对齐：
 * {@code page | line_range | sheet_cell | native | section_only | unavailable}。
 * 前三者为"已声明可精确定位"（进来源可解析率分母）；{@code native} 携带人读
 * {@code description}（DOCX 段索引 / PPTX slide / HTML xpath 等，不冒充精确定位）。</p>
 *
 * @param kind        定位类型（词表见上）
 * @param page        PDF 页码（1 基）
 * @param lineStart   MD/TXT 行号（0 基 end-exclusive，同 IR 契约）
 * @param lineEnd     行号区间末端（exclusive）
 * @param sheet       XLSX sheet 名
 * @param cell        首列 cell 绝对坐标（"A1"）
 * @param tableRef    表格证据的 table_id（网页"查看表格"跳转锚）
 * @param rowIndex    表格行号
 * @param description 人读位置说明（native 级）
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record EvidenceLocator(
        String kind,
        Integer page,
        @JsonProperty("line_start") Integer lineStart,
        @JsonProperty("line_end") Integer lineEnd,
        String sheet,
        String cell,
        @JsonProperty("table_ref") String tableRef,
        @JsonProperty("row_index") Integer rowIndex,
        String description
) {}
