package com.coremasterkb.serving.operator;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * P1-4/P2-15 回归：section 闭包 SQL 的 snapshot 绑定与 section-only 遍历。
 *
 * <p>缺陷：递归 CTE 只带 ref 不带 snapshot_id——多 KB 范围下 KB-A 的根
 * 可沿 KB-B 的 parent_ref 链爬进 KB-B 子树（合并两棵章节树）；递归腿也
 * 未限定 node_type='section'，segment/table 节点被计入后代（一个无子章
 * 节但有 513 个段落的章节被误拒 section_scope_too_broad）。</p>
 *
 * <p>契约：</p>
 * <ul>
 *   <li>闭包 CTE 携带 (snapshot_id, ref) 对，递归腿 JOIN 同时匹配
 *       snapshot_id 与 parent_ref；</li>
 *   <li>种子与递归腿均限定 node_type='section'；</li>
 *   <li>countSectionClosure 守卫同口径；</li>
 *   <li>unitPushdownFilters 的 descendants 谓词按行内 snapshot 过滤
 *       （u.snapshot_id = sec.snapshot_id），杜绝跨库 ref 命中。</li>
 * </ul>
 */
@DisplayName("Section closure SQL: snapshot-bound + section-only")
class SectionClosureSnapshotBoundXmlTest {

    private static String mapperXml() throws Exception {
        try (InputStream in = SectionClosureSnapshotBoundXmlTest.class.getClassLoader()
                .getResourceAsStream("mapper/AssetRetrievalUnitV2Mapper.xml")) {
            assertThat(in).as("mapper XML must be on classpath").isNotNull();
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    @Test
    @DisplayName("closure CTE carries (snapshot_id, ref) pairs through recursion")
    void closureCarriesSnapshotIdentity() throws Exception {
        String xml = mapperXml();
        // 递归 CTE 声明双列（snapshot_id, ref），而非仅 ref
        assertThat(xml).contains("WITH RECURSIVE sec(snapshot_id, ref)");
        // 递归腿 JOIN 同时匹配快照与父 ref（防跨库爬树）
        assertThat(xml).contains("c.snapshot_id = sec.snapshot_id");
        assertThat(xml).contains("c.parent_ref = sec.ref");
    }

    @Test
    @DisplayName("closure traversal restricted to section nodes")
    void closureOnlyTraversesSectionNodes() throws Exception {
        String xml = mapperXml();
        // 种子与递归腿都限定 node_type='section'
        int seedStart = xml.indexOf("WITH RECURSIVE sec(snapshot_id, ref)");
        int guardStart = xml.indexOf("WITH RECURSIVE sec(root");
        String closureBlock = xml.substring(
                seedStart, guardStart > seedStart ? guardStart : xml.length());
        assertThat(seedStart).isGreaterThanOrEqualTo(0);
        assertThat(closureBlock.split("node_type = 'section'", -1).length - 1)
                .as("closure seed + recursive leg must both filter node_type='section'")
                .isGreaterThanOrEqualTo(2);
    }

    @Test
    @DisplayName("count guard closure uses same snapshot-pair + section-only shape")
    void countGuardSameShape() throws Exception {
        String xml = mapperXml();
        int guardStart = xml.indexOf("WITH RECURSIVE sec(root");
        assertThat(guardStart).isGreaterThanOrEqualTo(0);
        String guardBlock = xml.substring(
                guardStart, xml.indexOf("</select>", guardStart));
        assertThat(guardBlock).contains("c.snapshot_id = sec.snapshot_id");
        assertThat(guardBlock).contains("node_type = 'section'");
        // 根计数按 (root, snapshot) 分组——多 KB 同 ref 各自计数不合并
        assertThat(guardBlock).contains("GROUP BY root");
    }

    @Test
    @DisplayName("descendants pushdown filters rows by the closure's snapshot")
    void pushdownFiltersBySnapshotPair() throws Exception {
        String xml = mapperXml();
        assertThat(xml).contains(
                "section_ref IN (SELECT ref FROM sec WHERE snapshot_id ="
        );
    }
}
