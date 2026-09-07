package com.coremasterkb.serving.application;

import com.coremasterkb.serving.AgentServingApplication;
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
 * The visibility rule is a SQL expression (a LEFT JOIN whose NULLs decide the anonymous case),
 * so it is verified against a real PostgreSQL. Mirrors mining's {@code KbDB.is_visible}: a
 * divergence between the two is a real privilege bug, not a style difference.
 */
@SpringBootTest(classes = AgentServingApplication.class)
@ActiveProfiles("test-pg")
@Tag("pg-integration")
@DisplayName("KbAccessService IT")
class KbAccessServiceIT {

    private static final String DOMAIN = "cloud_core_network";
    private static final String OTHER_DOMAIN = "generic";

    @Autowired
    private DataSource dataSource;

    @Autowired
    private KbAccessService kbAccessService;

    private JdbcTemplate jdbc;
    private String token;

    private String owner, member, outsider, admin, editor;
    private String kbPrivate, kbShared, kbPublic, kbDeleted, kbOtherDomain;

    @BeforeEach
    void setUp() {
        try (Connection conn = dataSource.getConnection()) {
            assumeTrue(conn.isValid(3), "PostgreSQL not reachable — skipping");
        } catch (SQLException e) {
            assumeTrue(false, "PostgreSQL not reachable — skipping");
        }
        jdbc = new JdbcTemplate(dataSource);
        assumeTrue(tableExists("knowledge_bases") && tableExists("kb_members"),
                "kb schema not present in this database — skipping");

        token = UUID.randomUUID().toString().substring(0, 8);
        owner = "owner-" + token;
        member = "member-" + token;
        outsider = "outsider-" + token;
        admin = "admin-" + token;
        editor = "editor-" + token;
        kbPrivate = "kbPriv-" + token;
        kbShared = "kbShared-" + token; // 现为 private+成员模型（007 收口 shared）
        kbPublic = "kbPub-" + token;
        kbDeleted = "kbDel-" + token;
        kbOtherDomain = "kbOther-" + token;

        insertUser(owner);
        insertUser(member);
        insertUser(outsider);
        insertUser(admin, "admin");
        insertUser(editor);
        insertKb(kbPrivate, DOMAIN, "private", "active");
        insertKb(kbShared, DOMAIN, "private", "active");
        insertKb(kbPublic, DOMAIN, "public", "active");
        insertKb(kbDeleted, DOMAIN, "public", "deleted");
        insertKb(kbOtherDomain, OTHER_DOMAIN, "public", "active");
        jdbc.update("INSERT INTO kb_members (kb_id, user_id, role, added_at) VALUES (?,?, 'viewer', ?)",
                kbShared, member, "2026-01-01T00:00:00Z");
        jdbc.update("INSERT INTO kb_members (kb_id, user_id, role, added_at) VALUES (?,?, 'viewer', ?)",
                kbPrivate, member, "2026-01-01T00:00:00Z");
        jdbc.update("INSERT INTO kb_members (kb_id, user_id, role, added_at) VALUES (?,?, 'editor', ?)",
                kbPrivate, editor, "2026-01-01T00:00:00Z");
    }

    @AfterEach
    void cleanUp() {
        if (jdbc == null || token == null) return;
        jdbc.update("DELETE FROM kb_members WHERE kb_id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM knowledge_bases WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM kb_users WHERE id LIKE ?", "%" + token);
    }

    @Test
    @DisplayName("owner reads their own private KB")
    void ownerReadsPrivate() {
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPrivate), username(owner)))
                .containsExactly(kbPrivate);
    }

    @Test
    @DisplayName("a stranger cannot read a private KB")
    void outsiderCannotReadPrivate() {
        assertThatThrownBy(() ->
                kbAccessService.authorize(DOMAIN, List.of(kbPrivate), username(outsider)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");
    }

    @Test
    @DisplayName("a member reads a shared KB, a non-member does not")
    void membershipGrantsRead() {
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbShared), username(member)))
                .containsExactly(kbShared);

        assertThatThrownBy(() ->
                kbAccessService.authorize(DOMAIN, List.of(kbShared), username(outsider)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("anonymous callers see public KBs and nothing else")
    void anonymousSeesPublicOnly() {
        // mcp_server and any pre-existing client send no X-KB-User at all.
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPublic), null))
                .containsExactly(kbPublic);

        assertThatThrownBy(() -> kbAccessService.authorize(DOMAIN, List.of(kbPrivate), null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("an unknown username is anonymous, not an error")
    void unknownUsernameFallsBackToPublic() {
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPublic), "nobody-" + token))
                .containsExactly(kbPublic);
    }

    @Test
    @DisplayName("a soft-deleted KB is invisible even to its owner")
    void softDeletedIsInvisible() {
        assertThatThrownBy(() ->
                kbAccessService.authorize(DOMAIN, List.of(kbDeleted), username(owner)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");
    }

    @Test
    @DisplayName("a KB from another domain is invisible under this domain")
    void otherDomainIsInvisible() {
        assertThatThrownBy(() ->
                kbAccessService.authorize(DOMAIN, List.of(kbOtherDomain), username(owner)))
                .isInstanceOf(IllegalArgumentException.class);

        assertThat(kbAccessService.authorize(OTHER_DOMAIN, List.of(kbOtherDomain), username(owner)))
                .containsExactly(kbOtherDomain);
    }

    @Test
    @DisplayName("one denied KB rejects the batch, even alongside readable ones")
    void mixedBatchIsRejected() {
        assertThatThrownBy(() ->
                kbAccessService.authorize(DOMAIN, List.of(kbPublic, kbPrivate), username(outsider)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");
    }

    // ---- 批次一（1.0.2 修复）：站点管理员全通，与 mining kb/db.py 对齐 ------------------

    @Test
    @DisplayName("a site admin reads any active KB in the domain, private included")
    void siteAdminReadsAnyActiveKbInDomain() {
        // admin 不是 owner、不是成员、库是 private——修复前这里抛 kb_not_found（1.0.2 现场）。
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPrivate), username(admin)))
                .containsExactly(kbPrivate);
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbShared, kbPublic), username(admin)))
                .containsExactlyInAnyOrder(kbShared, kbPublic);
    }

    @Test
    @DisplayName("site admin powers stop at soft-deleted and other-domain KBs")
    void siteAdminExclusions() {
        assertThatThrownBy(() ->
                kbAccessService.authorize(DOMAIN, List.of(kbDeleted), username(admin)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");

        assertThatThrownBy(() ->
                kbAccessService.authorize(DOMAIN, List.of(kbOtherDomain), username(admin)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");
        assertThat(kbAccessService.authorize(OTHER_DOMAIN, List.of(kbOtherDomain), username(admin)))
                .containsExactly(kbOtherDomain);
    }

    @Test
    @DisplayName("a non-admin site_role does not grant blanket read")
    void nonAdminSiteRoleIsNotAGrant() {
        // site_role 只有 'admin' 是全通；普通用户 site_role='member'（006 DDL NOT NULL DEFAULT）。
        assertThatThrownBy(() ->
                kbAccessService.authorize(DOMAIN, List.of(kbPrivate), username(outsider)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessage("kb_not_found");
    }

    @Test
    @DisplayName("permission matrix: role × visibility")
    void permissionMatrix() {
        // private（owner=owner，成员 viewer=member、editor=editor）
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPrivate), username(owner)))
                .containsExactly(kbPrivate);                       // owner × private
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPrivate), username(member)))
                .containsExactly(kbPrivate);                       // viewer × private
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPrivate), username(editor)))
                .containsExactly(kbPrivate);                       // editor × private
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPrivate), username(admin)))
                .containsExactly(kbPrivate);                       // admin × private
        assertThatThrownBy(() ->
                kbAccessService.authorize(DOMAIN, List.of(kbPrivate), username(outsider)))
                .hasMessage("kb_not_found");                       // non-member × private

        // public
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPublic), username(outsider)))
                .containsExactly(kbPublic);                        // non-member × public
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPublic), null))
                .containsExactly(kbPublic);                        // anonymous × public
        assertThat(kbAccessService.authorize(DOMAIN, List.of(kbPublic), "name-nobody-" + token))
                .containsExactly(kbPublic);                        // 未知用户 × public（LEFT JOIN NULL 语义）
    }

    // -------------------------------------------------------------------------

    /** kb_users.username, which is what X-KB-User actually carries (ids are internal). */
    private String username(String userId) {
        return "name-" + userId;
    }

    private void insertUser(String id) {
        insertUser(id, "member");
    }

    private void insertUser(String id, String siteRole) {
        jdbc.update("INSERT INTO kb_users (id, username, status, site_role, created_at) VALUES (?,?,?,?,'2026-01-01T00:00:00Z')",
                id, username(id), "active", siteRole);
    }

    private void insertKb(String id, String domain, String visibility, String status) {
        jdbc.update("INSERT INTO knowledge_bases "
                        + "(id, domain, name, owner_id, visibility, status, created_at, updated_at) "
                        + "VALUES (?,?,?,?,?,?,?,?)",
                id, domain, id, owner, visibility, status,
                "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z");
    }

    private boolean tableExists(String table) {
        Boolean present = jdbc.queryForObject(
                "SELECT to_regclass(?) IS NOT NULL", Boolean.class, table);
        return Boolean.TRUE.equals(present);
    }
}
