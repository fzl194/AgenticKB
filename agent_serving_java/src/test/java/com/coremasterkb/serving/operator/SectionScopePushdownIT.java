package com.coremasterkb.serving.operator;

import com.coremasterkb.serving.AgentServingApplication;
import com.coremasterkb.serving.mapper.result.UnitV2Row;
import com.coremasterkb.serving.operator.mapper.AssetRetrievalUnitV2Mapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * A2 章节范围下推（39 号 §2.2）真 PG 验证：exact / descendants / 存量回落 /
 * 闭包守卫。核心断言是<b>越界率=0</b>——范围内搜索不得混入范围外单元
 * （34 号 P0-3 强约束）。
 */
@SpringBootTest(classes = AgentServingApplication.class)
@ActiveProfiles("test-pg")
@Tag("pg-integration")
@DisplayName("A2 section scope pushdown IT")
class SectionScopePushdownIT {

    private static final String SNAP = "snap-a2-" + UUID.randomUUID().toString().substring(0, 8);
    private static final String DOC = "doc-a2";
    private static final String S0 = DOC + "#section:0";        // 父章节
    private static final String S00 = DOC + "#section:0/0";     // 子章节 A
    private static final String S01 = DOC + "#section:0/1";     // 子章节 B
    private static final String S1 = DOC + "#section:1";        // 范围外章节

    @Autowired
    private DataSource dataSource;

    @Autowired
    private AssetRetrievalUnitV2Mapper mapper;

    private JdbcTemplate jdbc;

    @BeforeEach
    void setUp() {
        try (Connection conn = dataSource.getConnection()) {
            assumeTrue(conn.isValid(3), "PostgreSQL not reachable — skipping");
        } catch (SQLException e) {
            assumeTrue(false, "PostgreSQL not reachable — skipping");
        }
        jdbc = new JdbcTemplate(dataSource);
        assumeTrue(tableExists("asset_retrieval_units_v2"),
                "retrieval v2 schema not present — skipping");

        // 结构树：doc → S0 →（S00, S01）；S1 独立
        insertNode("document", DOC + "#document", null);
        insertNode("section", S0, DOC + "#document");
        insertNode("section", S00, S0);
        insertNode("section", S01, S0);
        insertNode("section", S1, DOC + "#document");

        // 检索单元：每章节一条 prose（token 唯一可分辨）+ 一条存量 NULL 行（target=章节 ref）
        insertUnit("u-s0", "prose", S0, "alpha root content");
        insertUnit("u-s00", "prose", S00, "bravo child a content");
        insertUnit("u-s01", "prose", S01, "charlie child b content");
        insertUnit("u-s1", "prose", S1, "delta outside content");
        insertLegacyUnit("u-legacy", "section", S00, "echo legacy summary content");
    }

    @AfterEach
    void cleanUp() {
        if (jdbc == null) return;
        // 目录用例的旁路快照（'-dir-*'）及其 units；文档先删（links 级联），
        // 快照后删（links 对快照是 ON DELETE RESTRICT）。
        jdbc.update("DELETE FROM asset_retrieval_units_v2 WHERE snapshot_id LIKE ?", SNAP + "-%");
        jdbc.update("DELETE FROM asset_documents WHERE id LIKE ?", "docdir-" + SNAP + "-%");
        jdbc.update("DELETE FROM asset_document_snapshots WHERE id LIKE ?", SNAP + "-%");
        jdbc.update("DELETE FROM asset_retrieval_units_v2 WHERE snapshot_id = ?", SNAP);
        jdbc.update("DELETE FROM asset_structure_nodes WHERE snapshot_id = ?", SNAP);
    }

    @Test
    @DisplayName("exact：命中本节（含章节摘要），不越界到子节/他节")
    void exactScope() {
        List<UnitV2Row> rows = mapper.searchFtsV2(
                "content", List.of(SNAP), List.of(), List.of(), List.of(),
                List.of(S0), false, "", 50);
        assertThat(rows).extracting(UnitV2Row::getRepresentationId)
                .containsExactly("u-s0");
    }

    @Test
    @DisplayName("descendants：命中本节+全部子节，范围外章节零泄漏")
    void descendantsScope() {
        List<UnitV2Row> rows = mapper.searchFtsV2(
                "content", List.of(SNAP), List.of(), List.of(), List.of(),
                List.of(S0), true, "", 50);
        assertThat(rows).extracting(UnitV2Row::getRepresentationId)
                .containsExactlyInAnyOrder("u-s0", "u-s00", "u-s01", "u-legacy");
    }

    @Test
    @DisplayName("descendants 从子节起：不含父节正文，也不含兄弟子节")
    void descendantsFromChild() {
        List<UnitV2Row> rows = mapper.searchFtsV2(
                "content", List.of(SNAP), List.of(), List.of(), List.of(),
                List.of(S00), true, "", 50);
        assertThat(rows).extracting(UnitV2Row::getRepresentationId)
                .containsExactlyInAnyOrder("u-s00", "u-legacy");
    }

    @Test
    @DisplayName("存量行（section_ref NULL）回落 target_ref 精确匹配——exact 与 descendants 各自成立")
    void legacyFallback() {
        List<UnitV2Row> exact = mapper.searchFtsV2(
                "legacy", List.of(SNAP), List.of(), List.of(), List.of(),
                List.of(S00), false, "", 50);
        assertThat(exact).extracting(UnitV2Row::getRepresentationId)
                .containsExactly("u-legacy");

        // 旧语义回归：父节 exact 不吞子节（37 号 D4 的旧行为仅对存量行保留）
        List<UnitV2Row> parentExact = mapper.searchFtsV2(
                "content", List.of(SNAP), List.of(), List.of(), List.of(),
                List.of(S0), false, "", 50);
        assertThat(parentExact).extracting(UnitV2Row::getRepresentationId)
                .doesNotContain("u-legacy", "u-s00");
    }

    @Test
    @DisplayName("闭包守卫计数：root 含自身，子树大小正确")
    void closureCounts() {
        List<Map<String, Object>> counts = mapper.countSectionClosure(
                List.of(SNAP), List.of(S0, S1));
        assertThat(counts).hasSize(2);
        Map<String, Object> s0 = counts.stream()
                .filter(r -> S0.equals(r.get("root"))).findFirst().orElseThrow();
        // S0 闭包 = {S0, S00, S01} → total 3
        assertThat(((Number) s0.get("total")).longValue()).isEqualTo(3L);
    }

    // ---------------------------------------------------------------------

    private void insertNode(String type, String ref, String parentRef) {
        jdbc.update(
                "INSERT INTO asset_structure_nodes "
                        + "(snapshot_id, node_type, ref, parent_ref, ordinal, title) "
                        + "VALUES (?,?,?,?,?,?)",
                SNAP, type, ref, parentRef, 0, ref);
    }

    @Test
    @DisplayName("57号 directory_prefix：目录子树递归命中，兄弟前缀目录零误中")
    void directoryPrefixScope() {
        // 1.1.14 内网勘误：目录过滤改按 asset_documents.directory_path（快照经
        // asset_document_snapshot_links 归属）。播种刻意用不含目录的 document_key
        // （= 一张网形态 onenet:{source}:{sha}）——旧 facets LIKE 'doc:/…' 对此整库
        // 零命中，正是内网实测 Bug 的复现形态。
        String snapA = insertDirectoryDoc("a", "产品文档/手册");           // 目录自身
        String snapDeep = insertDirectoryDoc("deep", "产品文档/手册/交换机"); // 递归子目录
        String snapB = insertDirectoryDoc("b", "产品文档");               // 父目录
        String snapC = insertDirectoryDoc("c", "产品文档2");            // 兄弟前缀（不命中）
        // 无目录归属的存量行（SNAP 无 links 行=目录 NULL）不进入目录过滤结果
        insertUnit("u-dir-nodefault", "prose", S1, "kilo nodefault content", SNAP);

        List<String> scope = List.of(SNAP, snapA, snapDeep, snapB, snapC);
        List<UnitV2Row> subtree = mapper.searchFtsV2(
                "content", scope, List.of(), List.of(),
                List.of(), List.of(), false, "产品文档/手册", 50);
        assertThat(subtree).extracting(UnitV2Row::getRepresentationId)
                .containsExactlyInAnyOrder("u-dir-a", "u-dir-deep");

        // '产品文档' 命中自身+全部子树，不误中兄弟前缀 '产品文档2'，也不吞无目录行
        List<UnitV2Row> whole = mapper.searchFtsV2(
                "content", scope, List.of(), List.of(),
                List.of(), List.of(), false, "产品文档", 50);
        assertThat(whole).extracting(UnitV2Row::getRepresentationId)
                .containsExactlyInAnyOrder("u-dir-a", "u-dir-deep", "u-dir-b");

        // 目录名含 %/_：strpos 字面匹配（1.1.14 勘误后无 LIKE 通配符语义）——
        // 兄弟目录 100X Y覆盖 不命中；%/_ 无需转义原样绑定（IT 走生产映射入口）。
        String snapWild = insertDirectoryDoc("wild", "100%_覆盖");
        String snapSib = insertDirectoryDoc("sib", "100X Y覆盖");
        String literalWild = com.coremasterkb.serving.operator.operators.retrieve.ScopeFilterPushdown
                .fromFilters(java.util.Map.of("directory_prefix", "100%_覆盖"))
                .directoryPrefix();
        List<UnitV2Row> wild = mapper.searchFtsV2(
                "content", List.of(snapWild, snapSib), List.of(), List.of(), List.of(),
                List.of(), false, literalWild, 50);
        assertThat(wild).extracting(UnitV2Row::getRepresentationId)
                .containsExactly("u-dir-wild");

        // 不过滤时无目录行照常可检索（目录过滤是收窄不是屏蔽）
        List<UnitV2Row> unscoped = mapper.searchFtsV2(
                "content", List.of(SNAP), List.of(), List.of(), List.of(),
                List.of(), false, "", 50);
        assertThat(unscoped).extracting(UnitV2Row::getRepresentationId)
                .contains("u-dir-nodefault");
    }

    /**
     * 1.1.14 勘误：在指定目录下造一个"文档+快照+归属链+检索单元"。document_key
     * 刻意不含目录（一张网形态）；目录只存 directory_path——新过滤路径的唯一真相。
     */
    private String insertDirectoryDoc(String tag, String directoryPath) {
        String snap = SNAP + "-dir-" + tag;
        jdbc.update(
                "INSERT INTO asset_document_snapshots (id, domain, normalized_content_hash, "
                        + "raw_content_hash, mime_type, created_at) VALUES (?,?,?,?,?,?)",
                snap, "generic", "nh-dir-" + tag, "rh-dir-" + tag,
                "text/markdown", "2026-10-08T00:00:00Z");
        String docId = "docdir-" + SNAP + "-" + tag;
        jdbc.update(
                "INSERT INTO asset_documents (id, domain, document_key, document_name, "
                        + "created_at, directory_path) VALUES (?,?,?,?,?,?)",
                docId, "generic", "dk-dir-" + tag, tag + ".pdf",
                "2026-10-08T00:00:00Z", directoryPath);
        jdbc.update(
                "INSERT INTO asset_document_snapshot_links (id, document_id, "
                        + "document_snapshot_id, relative_path, source_uri, linked_at) "
                        + "VALUES (?,?,?,?,?,?)",
                "lnkdir-" + SNAP + "-" + tag, docId, snap, tag + ".pdf",
                "uri://" + tag, "2026-10-08T00:00:00Z");
        insertUnit("u-dir-" + tag, "prose", S1, "dir " + tag + " content", snap);
        return snap;
    }

    private void insertUnit(String repId, String type, String sectionRef, String text) {
        insertUnit(repId, type, sectionRef, text, SNAP);
    }

    private void insertUnit(String repId, String type, String sectionRef, String text,
                            String snapshotId) {
        jdbc.update(
                "INSERT INTO asset_retrieval_units_v2 (representation_id, snapshot_id, "
                        + "representation_type, content_type, content_text, lexical_text, "
                        + "target_type, target_ref, canonical_evidence_id, section_ref, "
                        + "lexical_eligible, dense_eligible, returnable) "
                        + "VALUES (?,?,?,?,?,?,?,?,?,?,TRUE,FALSE,TRUE)",
                repId, snapshotId, type, type, text, text, type,
                sectionRef, repId, sectionRef);
    }

    /** 存量行：015 之前落库（无 section_ref），旧语义挂在 target_ref。 */
    private void insertLegacyUnit(String repId, String type, String targetRef, String text) {
        jdbc.update(
                "INSERT INTO asset_retrieval_units_v2 (representation_id, snapshot_id, "
                        + "representation_type, content_type, content_text, lexical_text, "
                        + "target_type, target_ref, canonical_evidence_id, "
                        + "lexical_eligible, dense_eligible, returnable) "
                        + "VALUES (?,?,?,?,?,?,?,?,?,TRUE,FALSE,TRUE)",
                repId, SNAP, type, type, text, text, type, targetRef, repId);
    }

    private boolean tableExists(String table) {
        Boolean present = jdbc.queryForObject(
                "SELECT to_regclass(?) IS NOT NULL", Boolean.class, table);
        return Boolean.TRUE.equals(present);
    }
}
