package com.coremasterkb.serving.repository;

import com.coremasterkb.serving.AgentServingApplication;
import com.coremasterkb.serving.mapper.AssetDocumentMapper;
import com.coremasterkb.serving.mapper.param.SegmentWindow;
import com.coremasterkb.serving.mapper.result.DocumentFileRow;
import com.coremasterkb.serving.mapper.result.DocumentSourceRow;
import com.coremasterkb.serving.mapper.result.FtsResultRow;
import com.coremasterkb.serving.mapper.result.SegmentFullRow;
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
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * The full-text drill-down against a real PostgreSQL.
 *
 * <p>Every guarantee worth testing here lives in SQL and is invisible to a mocked mapper: that
 * {@code selectFullByIds} returns one row per segment despite the 1:N snapshot-link table, that the
 * unconditional scope filters actually filter, that {@code selectWindows} OR-s its ranges without
 * bleeding across snapshots, and that {@code selectFileLocations} de-duplicates.</p>
 *
 * <p>Seeds its own fixture under a per-run token and deletes it afterwards, so it neither depends
 * on nor disturbs whatever else is in the database.</p>
 */
@SpringBootTest(classes = AgentServingApplication.class)
@ActiveProfiles("test-pg")
@Tag("pg-integration")
@DisplayName("AssetRepository full-text IT")
class AssetRepositoryFullTextIT {

    private static final String DOMAIN = "cloud_core_network";

    @Autowired
    private DataSource dataSource;

    @Autowired
    private AssetRepository assetRepository;

    @Autowired
    private AssetDocumentMapper documentMapper;

    private JdbcTemplate jdbc;
    private String token;

    private String userId, kbA, kbB;
    private String docA, docB, docLegacy;
    /** snapShared backs both docA and docB — content-level dedup, the 1:N source. */
    private String snapShared, snapOther, snapLegacy;
    private String segA0, segA1, segA2, segA3, segOther1, segLegacy0;
    private String unitA, unitOther;

    @BeforeEach
    void setUp() {
        try (Connection conn = dataSource.getConnection()) {
            assumeTrue(conn.isValid(3), "PostgreSQL not reachable — skipping");
        } catch (SQLException e) {
            assumeTrue(false, "PostgreSQL not reachable — skipping");
        }
        jdbc = new JdbcTemplate(dataSource);

        assumeTrue(tableExists("knowledge_bases") && tableExists("kb_users"),
                "kb schema not present in this database — skipping");
        assumeTrue(columnExists("asset_documents", "kb_id")
                        && columnExists("asset_documents", "storage_path"),
                "kb columns not present on asset_documents — skipping");

        token = UUID.randomUUID().toString().substring(0, 8);
        assignIds();
        seed();
    }

    @AfterEach
    void cleanUp() {
        if (jdbc == null || token == null) return;
        jdbc.update("DELETE FROM asset_retrieval_units WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM asset_raw_segments WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM asset_document_snapshot_links WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM asset_documents WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM asset_document_snapshots WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM knowledge_bases WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM kb_users WHERE id LIKE ?", "%" + token);
    }

    // -------------------------------------------------------------------------
    // selectFullByIds
    // -------------------------------------------------------------------------

    @Test
    @DisplayName("a segment whose snapshot backs two documents still comes back exactly once")
    void oneRowPerSegmentDespiteSharedSnapshot() {
        // This is the regression lock for not joining asset_document_snapshot_links. snapShared is
        // linked to both docA and docB, so the join selectWithMeta does would return two rows for
        // every segment in it — and any consumer that assumed row uniqueness would double-count.
        assertThat(linkCountFor(snapShared)).isEqualTo(2);

        List<SegmentFullRow> rows =
                assetRepository.resolveSegmentsFull(List.of(segA1), List.of(snapShared));

        assertThat(rows).hasSize(1);
        assertThat(rows.get(0).getId()).isEqualTo(segA1);
        assertThat(rows.get(0).getRawText()).isEqualTo("段落一的完整原文");
        assertThat(rows.get(0).getSegmentIndex()).isEqualTo(1);
        assertThat(rows.get(0).getSnapshotTitle()).isNotBlank();
    }

    @Test
    @DisplayName("the scope filter is real: a segment outside the given snapshots is not returned")
    void scopeFilterExcludesForeignSegments() {
        List<SegmentFullRow> rows = assetRepository.resolveSegmentsFull(
                List.of(segA1, segOther1), List.of(snapShared));

        assertThat(rows).extracting(SegmentFullRow::getId).containsExactly(segA1);
    }

    @Test
    @DisplayName("an empty scope fails instead of matching every knowledge base")
    void emptyScopeIsRejected() {
        // The guard is in Java, but the reason it exists is SQL: selectWithMeta wraps the same
        // filter in an <if>, where an empty list silently means "no filter at all".
        assertThatThrownBy(() -> assetRepository.resolveSegmentsFull(List.of(segA1), List.of()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("empty_scope");
    }

    // -------------------------------------------------------------------------
    // selectWindows
    // -------------------------------------------------------------------------

    @Test
    @DisplayName("a window returns the inclusive index range around its target")
    void windowReturnsInclusiveRange() {
        List<SegmentFullRow> rows = assetRepository.resolveSegmentWindows(
                List.of(new SegmentWindow(snapShared, 0, 2)), List.of(snapShared, snapOther));

        assertThat(rows).extracting(SegmentFullRow::getId).containsExactly(segA0, segA1, segA2);
        assertThat(rows).extracting(SegmentFullRow::getSegmentIndex).containsExactly(0, 1, 2);
    }

    @Test
    @DisplayName("several windows resolve in one query, still one row per segment")
    void multipleWindowsInOneQuery() {
        // Overlapping windows around index 1 and index 2 must not return segA1/segA2 twice.
        List<SegmentFullRow> rows = assetRepository.resolveSegmentWindows(
                List.of(new SegmentWindow(snapShared, 0, 2), new SegmentWindow(snapShared, 1, 3)),
                List.of(snapShared));

        assertThat(rows).extracting(SegmentFullRow::getId)
                .containsExactly(segA0, segA1, segA2, segA3)
                .doesNotHaveDuplicates();
    }

    @Test
    @DisplayName("a window is bounded to its own snapshot, not to the index range alone")
    void windowDoesNotBleedAcrossSnapshots() {
        // segOther1 sits at index 1 of a different document. If the range predicate were not
        // anded with the snapshot id, it would be swept in by any window covering index 1.
        List<SegmentFullRow> rows = assetRepository.resolveSegmentWindows(
                List.of(new SegmentWindow(snapShared, 0, 3)), List.of(snapShared, snapOther));

        assertThat(rows).extracting(SegmentFullRow::getId).doesNotContain(segOther1);
    }

    @Test
    @DisplayName("the scope filter applies to windows too")
    void windowRespectsScope() {
        List<SegmentFullRow> rows = assetRepository.resolveSegmentWindows(
                List.of(new SegmentWindow(snapOther, 0, 5)), List.of(snapShared));

        assertThat(rows).isEmpty();
    }

    // -------------------------------------------------------------------------
    // units
    // -------------------------------------------------------------------------

    @Test
    @DisplayName("units are fetched with their text and refs, and confined to the scope")
    void unitsRespectScope() {
        List<FtsResultRow> inScope =
                assetRepository.resolveUnitsFull(List.of(unitA, unitOther), List.of(snapShared));

        assertThat(inScope).extracting(FtsResultRow::getId).containsExactly(unitA);
        assertThat(inScope.get(0).getText()).isEqualTo("检索单元的完整文本");
        assertThat(inScope.get(0).getSourceRefsJson()).contains(segA1);
        assertThat(inScope.get(0).getUnitType()).isEqualTo("raw_text");
    }

    // -------------------------------------------------------------------------
    // file locations
    // -------------------------------------------------------------------------

    @Test
    @DisplayName("a document linked to several in-scope snapshots yields one row, not one per link")
    void fileLocationsAreDeduplicated() {
        // docA links to snapShared and snapOther; both are in scope. Without DISTINCT this is two
        // rows, and RawFileService reads rows.get(0) as though it were the only one.
        List<DocumentFileRow> rows = assetRepository.resolveFileLocations(
                List.of(docA), List.of(snapShared, snapOther));

        assertThat(rows).hasSize(1);
        DocumentFileRow row = rows.get(0);
        assertThat(row.getKbId()).isEqualTo(kbA);
        assertThat(row.getStoragePath()).isEqualTo("/tmp/uploads/" + kbA + "/a.md");
        assertThat(row.getDocumentName()).isEqualTo("a.md");
        assertThat(row.hasRawFile()).isTrue();
    }

    @Test
    @DisplayName("a legacy document reports no original file rather than a broken path")
    void legacyDocumentHasNoFile() {
        List<DocumentFileRow> rows =
                assetRepository.resolveFileLocations(List.of(docLegacy), List.of(snapLegacy));

        assertThat(rows).hasSize(1);
        assertThat(rows.get(0).getKbId()).isNull();
        assertThat(rows.get(0).getStoragePath()).isNull();
        assertThat(rows.get(0).hasRawFile()).isFalse();
    }

    @Test
    @DisplayName("a document whose snapshots are all out of scope is not resolvable")
    void fileLocationsRespectScope() {
        List<DocumentFileRow> rows =
                assetRepository.resolveFileLocations(List.of(docLegacy), List.of(snapShared));

        assertThat(rows).isEmpty();
    }

    // -------------------------------------------------------------------------
    // source refs (the kbId column added for provenance)
    // -------------------------------------------------------------------------

    @Test
    @DisplayName("document sources carry kbId so an answer can name the knowledge base it came from")
    void documentSourcesCarryKbId() {
        List<DocumentSourceRow> rows =
                documentMapper.selectDocumentSources(List.of(docA, docLegacy),
                        List.of(snapShared, snapLegacy));

        assertThat(rows).isNotEmpty();
        assertThat(rows).filteredOn(r -> docA.equals(r.getId()))
                .allSatisfy(r -> assertThat(r.getKbId()).isEqualTo(kbA));
        // Legacy documents belong to no KB — null, not a placeholder.
        assertThat(rows).filteredOn(r -> docLegacy.equals(r.getId()))
                .allSatisfy(r -> assertThat(r.getKbId()).isNull());
    }

    // -------------------------------------------------------------------------
    // Fixture
    // -------------------------------------------------------------------------

    private void assignIds() {
        userId = "u-" + token;
        kbA = "kbA-" + token;
        kbB = "kbB-" + token;
        docA = "docA-" + token;
        docB = "docB-" + token;
        docLegacy = "docLegacy-" + token;
        snapShared = "snapShared-" + token;
        snapOther = "snapOther-" + token;
        snapLegacy = "snapLegacy-" + token;
        segA0 = "segA0-" + token;
        segA1 = "segA1-" + token;
        segA2 = "segA2-" + token;
        segA3 = "segA3-" + token;
        segOther1 = "segOther1-" + token;
        segLegacy0 = "segLegacy0-" + token;
        unitA = "unitA-" + token;
        unitOther = "unitOther-" + token;
    }

    private void seed() {
        insertUser(userId);
        insertKb(kbA, "KB A " + token);
        insertKb(kbB, "KB B " + token);

        insertDocument(docA, kbA, "doc:/a.md", "a.md", "/tmp/uploads/" + kbA + "/a.md");
        insertDocument(docB, kbB, "doc:/b.md", "b.md", "/tmp/uploads/" + kbB + "/b.md");
        // Ingested through /api/runs: no KB, no storage path, no original file.
        insertDocument(docLegacy, null, "doc:/legacy.md", "legacy.md", null);

        insertSnapshot(snapShared);
        insertSnapshot(snapOther);
        insertSnapshot(snapLegacy);

        // snapShared backs two documents — content-level dedup, which is what makes the link
        // table 1:N and is the reason selectFullByIds must not join it.
        insertLink(docA, snapShared, "a.md");
        insertLink(docB, snapShared, "b.md");
        // docA also has a second snapshot, so its file location resolves through two links.
        insertLink(docA, snapOther, "a-v2.md");
        insertLink(docLegacy, snapLegacy, "legacy.md");

        insertSegment(segA0, snapShared, 0, "段落零的完整原文");
        insertSegment(segA1, snapShared, 1, "段落一的完整原文");
        insertSegment(segA2, snapShared, 2, "段落二的完整原文");
        insertSegment(segA3, snapShared, 3, "段落三的完整原文");
        insertSegment(segOther1, snapOther, 1, "另一个文档的第 1 段");
        insertSegment(segLegacy0, snapLegacy, 0, "legacy 文档的段落");

        insertUnit(unitA, snapShared, segA1);
        insertUnit(unitOther, snapOther, segOther1);
    }

    private void insertUser(String id) {
        jdbc.update("INSERT INTO kb_users (id, username, display_name, status, created_at) "
                        + "VALUES (?, ?, ?, 'active', ?)",
                id, "tester-" + token, "Tester", "2026-01-01T00:00:00Z");
    }

    private void insertKb(String id, String name) {
        jdbc.update("INSERT INTO knowledge_bases "
                        + "(id, domain, name, owner_id, visibility, status, created_at, updated_at) "
                        + "VALUES (?, ?, ?, ?, 'private', 'active', ?, ?)",
                id, DOMAIN, name, userId, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z");
    }

    private void insertDocument(String id, String kbId, String documentKey,
                                String documentName, String storagePath) {
        jdbc.update("INSERT INTO asset_documents "
                        + "(id, domain, document_key, document_name, created_at, kb_id, "
                        + " storage_path, directory_path, owner_id) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?, '', ?)",
                id, DOMAIN, documentKey, documentName, "2026-01-01T00:00:00Z", kbId,
                storagePath, kbId == null ? null : userId);
    }

    private void insertSnapshot(String id) {
        jdbc.update("INSERT INTO asset_document_snapshots "
                        + "(id, domain, normalized_content_hash, raw_content_hash, mime_type, "
                        + " title, created_at) "
                        + "VALUES (?, ?, ?, ?, 'text/markdown', ?, ?)",
                id, DOMAIN, "nh-" + id, "rh-" + id, "Snapshot " + id, "2026-01-01T00:00:00Z");
    }

    private void insertLink(String documentId, String snapshotId, String relativePath) {
        jdbc.update("INSERT INTO asset_document_snapshot_links "
                        + "(id, document_id, document_snapshot_id, relative_path, source_uri, "
                        + " title, linked_at) "
                        + "VALUES (?, ?, ?, ?, ?, ?, ?)",
                "link-" + documentId + "-" + snapshotId, documentId, snapshotId,
                relativePath, "file://" + relativePath, relativePath, "2026-01-01T00:00:00Z");
    }

    // JSON columns are JSONB in the deployed schema (002_asset_core_postgresql migrates them off
    // the TEXT declared in 001), so every JSON literal needs an explicit ::jsonb cast — a plain
    // parameter binds as varchar and PostgreSQL refuses the assignment.
    private void insertSegment(String id, String snapshotId, int index, String rawText) {
        jdbc.update("INSERT INTO asset_raw_segments "
                        + "(id, document_snapshot_id, segment_key, segment_index, section_path, "
                        + " section_title, block_type, semantic_role, raw_text, normalized_text, "
                        + " content_hash, normalized_hash, token_count) "
                        + "VALUES (?, ?, ?, ?, ?::jsonb, ?, 'paragraph', 'concept', ?, ?, ?, ?, ?)",
                id, snapshotId, "seg-" + index, index, "[\"3\",\"3.2\"]", "注册管理",
                rawText, rawText, "ch-" + id, "nh-" + id, rawText.length());
    }

    private void insertUnit(String id, String snapshotId, String segmentId) {
        jdbc.update("INSERT INTO asset_retrieval_units "
                        + "(id, document_snapshot_id, unit_key, unit_type, target_type, "
                        + " target_ref_json, title, text, search_text, source_refs_json, created_at) "
                        // unit_type/target_type are CHECK-constrained; 'raw_text' and
                        // 'raw_segment' are members of those enumerations.
                        + "VALUES (?, ?, ?, 'raw_text', 'raw_segment', ?::jsonb, ?, ?, ?, ?::jsonb, ?)",
                id, snapshotId, "unit-" + id,
                "{\"raw_segment_id\":\"" + segmentId + "\"}",
                "单元标题", "检索单元的完整文本", "检索单元的完整文本",
                "{\"raw_segment_ids\":[\"" + segmentId + "\"]}",
                "2026-01-01T00:00:00Z");
    }

    private int linkCountFor(String snapshotId) {
        Integer count = jdbc.queryForObject(
                "SELECT count(*) FROM asset_document_snapshot_links WHERE document_snapshot_id = ?",
                Integer.class, snapshotId);
        return count == null ? 0 : count;
    }

    private boolean tableExists(String table) {
        Boolean present = jdbc.queryForObject(
                "SELECT to_regclass(?) IS NOT NULL", Boolean.class, table);
        return Boolean.TRUE.equals(present);
    }

    private boolean columnExists(String table, String column) {
        Integer count = jdbc.queryForObject(
                "SELECT count(*) FROM information_schema.columns "
                        + "WHERE table_name = ? AND column_name = ?",
                Integer.class, table, column);
        return count != null && count > 0;
    }
}
