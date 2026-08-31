package com.coremasterkb.serving.operator;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 批次8 R7 结构工具 mapper 的 SQL 文本契约测试（PG 集成不可用时锁定关键语义）：
 * <ul>
 *   <li><b>无任何 {@code ${}} 插值</b>——字段名/操作符之外的用户输入全部参数绑定
 *       （25 号 §10.1：禁止字符串拼接列/操作符）；</li>
 *   <li>字段名只出现在 {@code ->>#{...}} 绑定（列白名单校验在 Java 侧）；</li>
 *   <li>递归 CTE 深度参数化（{@code down.d < #{maxDepth}}）；</li>
 *   <li>LIMIT/OFFSET 参数化（SQL 内收窄，不拉全量）；</li>
 *   <li>操作符/方向/聚合函数是 {@code <choose>} 固定分支（枚举驱动，非请求文本）。</li>
 * </ul>
 */
@DisplayName("StructureToolMapper SQL contract")
class StructureToolMapperXmlTest {

    private static String mapperXml() throws Exception {
        try (InputStream in = StructureToolMapperXmlTest.class.getClassLoader()
                .getResourceAsStream("mapper/StructureToolMapper.xml")) {
            assertThat(in).as("mapper XML must be on classpath").isNotNull();
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    @Test
    @DisplayName("全文无 ${} 字符串插值——注入面收敛在 #{} 绑定参数")
    void noStringInterpolation() throws Exception {
        assertThat(mapperXml()).doesNotContain("${");
    }

    @Test
    @DisplayName("字段名只作绑定参数：cells->>#{field} / #{orderField} / #{aggField}")
    void fieldNamesAreBoundParameters() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("t.cells ->> #{c.field}");
        assertThat(xml).contains("t.cells ->> #{orderField}");
        assertThat(xml).contains("t.cells ->> #{aggField}");
        // 不存在拼接形态
        assertThat(xml).doesNotContain("->> '");
        assertThat(xml).doesNotContain("->> \"");
    }

    @Test
    @DisplayName("递归 CTE 深度参数化（ancestors/descendants maxDepth 收窄）")
    void recursiveDepthIsParameterized() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("WHERE down.d &lt; #{maxDepth}");
        assertThat(xml).contains("WHERE up.d &lt; #{maxDepth}");
    }

    @Test
    @DisplayName("LIMIT/OFFSET 参数化 + 子女/后代/分页有界")
    void limitsAreParameterized() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("LIMIT #{limit} OFFSET #{offset}");
        assertThat(xml).contains("LIMIT #{limit}");
    }

    @Test
    @DisplayName("操作符/方向/聚合函数是固定 <choose> 分支（枚举驱动）")
    void opsAreFixedBranches() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("test=\"c.op == 'eq'\"");
        assertThat(xml).contains("test=\"c.op == 'contains'\"");
        assertThat(xml).contains("test=\"c.op == 'in'\"");
        assertThat(xml).contains("test=\"orderDir == 'desc'\"");
        assertThat(xml).contains("test=\"aggOp == 'count'\"");
        assertThat(xml).doesNotContain("test=\"aggOp == 'avg'\""); // otherwise 分支兜底
    }

    @Test
    @DisplayName("数值比较走 ::numeric 转换（先去千分位），文本走字典序")
    void numericVsTextBranches() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("NULLIF(REPLACE(t.cells ->> #{c.field}, ',', ''), '')::numeric = #{c.value}");
        assertThat(xml).contains("t.cells ->> #{c.field} = #{c.value}");
    }

    @Test
    @DisplayName("行 pivot 排除表头行（is_header = FALSE）且默认按 row_index 稳定排序")
    void pivotExcludesHeaderRows() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("c.is_header = FALSE");
        assertThat(xml).contains(", t.row_index ASC");
    }

    @Test
    @DisplayName("ref 候选枚举有 LIMIT 硬上限（活动/历史扫描有界）")
    void candidateEnumerationIsBounded() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains("selectStructureRefCandidates");
        assertThat(xml).contains("selectKbSnapshotRefs");
    }

    @Test
    @DisplayName("canonical 候选 ORDER BY 引用子查询真实列（D3 回归：u.ref 是外层别名）")
    void canonicalCandidatesOrderByRealColumns() throws Exception {
        String xml = mapperXml();
        int start = xml.indexOf("selectCanonicalRefCandidates");
        int end = xml.indexOf("</select>", start);
        assertThat(start).as("selectCanonicalRefCandidates must exist").isGreaterThan(0);
        String block = xml.substring(start, end);
        // ref 别名定义在外层 SELECT 列上，ORDER BY u.ref 在 PG 报列不存在（2026-08-31 全量 500）
        assertThat(block).doesNotContain("u.ref");
        assertThat(block).contains("ORDER BY u.snapshot_id ASC, u.canonical_evidence_id ASC");
    }
}
