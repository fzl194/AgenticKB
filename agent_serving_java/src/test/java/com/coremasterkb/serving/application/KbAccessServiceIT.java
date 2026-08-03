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

    private String owner, member, outsider;
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
        kbPrivate = "kbPriv-" + token;
        kbShared = "kbShared-" + token;
        kbPublic = "kbPub-" + token;
        kbDeleted = "kbDel-" + token;
        kbOtherDomain = "kbOther-" + token;

        insertUser(owner);
        insertUser(member);
        insertUser(outsider);
        insertKb(kbPrivate, DOMAIN, "private", "active");
        insertKb(kbShared, DOMAIN, "shared", "active");
        insertKb(kbPublic, DOMAIN, "public", "active");
        insertKb(kbDeleted, DOMAIN, "public", "deleted");
        insertKb(kbOtherDomain, OTHER_DOMAIN, "public", "active");
        jdbc.update("INSERT INTO kb_members (kb_id, user_id, role, added_at) VALUES (?,?, 'viewer', ?)",
                kbShared, member, "2026-01-01T00:00:00Z");
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

    // -------------------------------------------------------------------------

    /** kb_users.username, which is what X-KB-User actually carries (ids are internal). */
    private String username(String userId) {
        return "name-" + userId;
    }

    private void insertUser(String id) {
        jdbc.update("INSERT INTO kb_users (id, username, status, created_at) VALUES (?,?,'active',?)",
                id, username(id), "2026-01-01T00:00:00Z");
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
