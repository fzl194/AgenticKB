package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.AgentServingApplication;
import com.coremasterkb.serving.domainpack.DomainContext;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.TestPropertySource;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * The catalog against a real PostgreSQL.
 *
 * <p>What only a real DB can show: that the anonymous-readability verdict comes out of the same SQL
 * the retrieval path uses (a LEFT JOIN whose NULLs decide the anonymous case), and that the
 * disclosure filter really does hide a KB the requesting user cannot see. A mocked mapper would
 * assert nothing but the shape of our own stubs.</p>
 *
 * <p>Seeds its own fixture under a per-run token and deletes it afterwards, so it neither depends
 * on nor disturbs whatever else is in the database.</p>
 *
 * <p><b>The registry override is not optional.</b> This is a domain-routed test: the KB read runs
 * with {@code DomainContext} set, so it goes through {@code DomainPoolManager}. The shipped
 * registry gives every domain an inline {@code database:} block pointing at the production host,
 * and {@code getDataSource} runs {@code ensureSchema} DDL on each pool it builds — so without this
 * override the test would read someone else's data and run CREATE TABLE against production.
 * {@code DomainRoutingIT} was fixed for exactly this; the registry here removes the trap for good
 * rather than working around it. The fixtures below are written through the default DataSource,
 * and with no inline block the routed read resolves to that same database.</p>
 */
@SpringBootTest(classes = AgentServingApplication.class)
@ActiveProfiles("test-pg")
@TestPropertySource(properties = "serving.domain-registry-path=src/test/resources/domain-registry-local.yaml")
@Tag("pg-integration")
@DisplayName("ParadigmCatalogService IT")
class ParadigmCatalogIT {

    private static final String DOMAIN = "cloud_core_network";

    private static final String GRAPH_UNSCOPED = """
            {"nodes":[{"nodeId":"asm","operatorType":"assemble"}],
             "output":{"nodeId":"asm","slot":"contextPack"}}""";

    @Autowired
    private DataSource dataSource;

    @Autowired
    private ParadigmCatalogService catalogService;

    private JdbcTemplate jdbc;
    private String token;
    private String owner, outsider;
    private String kbPublic, kbPrivate;
    private String pdPublic, pdPrivate, pdUnscoped;

    @BeforeEach
    void setUp() {
        try (Connection conn = dataSource.getConnection()) {
            assumeTrue(conn.isValid(3), "PostgreSQL not reachable — skipping");
        } catch (SQLException e) {
            assumeTrue(false, "PostgreSQL not reachable — skipping");
        }
        jdbc = new JdbcTemplate(dataSource);
        assumeTrue(tableExists("knowledge_bases") && tableExists("operator_paradigm"),
                "kb / paradigm schema not present in this database — skipping");

        token = UUID.randomUUID().toString().substring(0, 8);
        owner = "owner-" + token;
        outsider = "outsider-" + token;
        kbPublic = "kbPub-" + token;
        kbPrivate = "kbPriv-" + token;
        pdPublic = "pdPub-" + token;
        pdPrivate = "pdPriv-" + token;
        pdUnscoped = "pdPlain-" + token;

        insertUser(owner);
        insertUser(outsider);
        insertKb(kbPublic, "public");
        insertKb(kbPrivate, "private");

        // All fixtures are is_default=false on purpose: uq_paradigm_domain_default is a partial
        // unique index over (bound_domain) WHERE is_default, so claiming the slot would collide
        // with whatever this shared database already has bound. The isDomainDefault field is
        // covered by the unit test, which needs no database to do it.
        insertParadigm(pdPublic, "公开语料检索-" + token, DOMAIN, scoped(kbPublic));
        insertParadigm(pdPrivate, "内部资料检索-" + token, DOMAIN, scoped(kbPrivate));
        insertParadigm(pdUnscoped, "通用检索-" + token, DOMAIN, GRAPH_UNSCOPED);
    }

    @AfterEach
    void cleanUp() {
        DomainContext.clear();
        if (jdbc == null || token == null) return;
        jdbc.update("DELETE FROM operator_paradigm_version WHERE paradigm_id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM operator_paradigm WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM knowledge_bases WHERE id LIKE ?", "%" + token);
        jdbc.update("DELETE FROM kb_users WHERE id LIKE ?", "%" + token);
    }

    @Test
    @DisplayName("only the anonymously-readable paradigm is visible")
    void anonymousReadabilityDecidesVisibility() {
        ParadigmCatalogService.Catalog c = catalogService.build(DOMAIN, null);

        assertThat(ids(c.paradigms())).contains(pdPublic, pdUnscoped).doesNotContain(pdPrivate);
        assertThat(hidden(c, pdPrivate).reason())
                .isEqualTo(ParadigmCatalogService.KB_NOT_READABLE);
    }

    @Test
    @DisplayName("an anonymous caller learns that it is hidden, and nothing about which KBs")
    void anonymousLearnsNothingAboutTheKbs() {
        ParadigmCatalogService.Hidden h = hidden(catalogService.build(DOMAIN, null), pdPrivate);

        assertThat(h.reason()).isEqualTo(ParadigmCatalogService.KB_NOT_READABLE);
        assertThat(h.details()).isEmpty();
        assertThat(h.undisclosedCount()).isZero();
    }

    @Test
    @DisplayName("the KB is named to a user who can already see it")
    void ownerSeesTheOffendingKbId() {
        ParadigmCatalogService.Hidden h =
                hidden(catalogService.build(DOMAIN, username(owner)), pdPrivate);

        assertThat(h.details()).containsExactly(kbPrivate);
        assertThat(h.undisclosedCount()).isZero();
    }

    @Test
    @DisplayName("...and stays unnamed to a user who cannot")
    void outsiderStillGetsNoName() {
        ParadigmCatalogService.Hidden h =
                hidden(catalogService.build(DOMAIN, username(outsider)), pdPrivate);

        assertThat(h.details()).isEmpty();
        assertThat(h.undisclosedCount()).isEqualTo(1);
    }

    /**
     * The catalog reads operator_paradigm (control DB) and knowledge_bases (domain DB) in one call.
     * If it left a DomainContext behind, the next control-DB read on this thread would be routed
     * into a domain pool — silently, since every domain currently points at the same physical DB.
     */
    @Test
    @DisplayName("leaves no DomainContext behind")
    void leavesNoDomainContext() {
        catalogService.build(DOMAIN, username(owner));

        assertThat(DomainContext.get()).isNull();
    }

    // -------------------------------------------------------------------------

    private static String scoped(String kbId) {
        return """
                {"nodes":[{"nodeId":"sc","operatorType":"scope_resolve","params":{"kbIds":["%s"]}},
                          {"nodeId":"asm","operatorType":"assemble"}],
                 "output":{"nodeId":"asm","slot":"contextPack"}}""".formatted(kbId);
    }

    private static List<String> ids(List<ParadigmCatalogService.Entry> entries) {
        return entries.stream().map(ParadigmCatalogService.Entry::id).toList();
    }

    private static ParadigmCatalogService.Hidden hidden(
            ParadigmCatalogService.Catalog c, String id) {
        return c.hidden().stream().filter(h -> h.id().equals(id)).findFirst()
                .orElseThrow(() -> new AssertionError(id + " is not in the hidden list"));
    }

    /** kb_users.username, which is what X-KB-User actually carries (ids are internal). */
    private String username(String userId) {
        return "name-" + userId;
    }

    private void insertUser(String id) {
        jdbc.update("INSERT INTO kb_users (id, username, status, created_at) VALUES (?,?,'active',?)",
                id, username(id), "2026-01-01T00:00:00Z");
    }

    private void insertKb(String id, String visibility) {
        jdbc.update("INSERT INTO knowledge_bases "
                        + "(id, domain, name, owner_id, visibility, status, created_at, updated_at) "
                        + "VALUES (?,?,?,?,?,'active',?,?)",
                id, DOMAIN, id, owner, visibility,
                "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z");
    }

    private void insertParadigm(String id, String name, String domain, String graph) {
        jdbc.update("INSERT INTO operator_paradigm "
                        + "(id, name, description, current_version, status, bound_domain, is_default) "
                        + "VALUES (?,?,?,1,'active',?,false)",
                id, name, "IT fixture " + token, domain);
        jdbc.update("INSERT INTO operator_paradigm_version "
                        + "(id, paradigm_id, version, graph_json, schema_version) "
                        + "VALUES (?,?,1,?::jsonb,'1.0')",
                "v-" + id, id, graph);
    }

    private boolean tableExists(String table) {
        Boolean present = jdbc.queryForObject(
                "SELECT to_regclass(?) IS NOT NULL", Boolean.class, table);
        return Boolean.TRUE.equals(present);
    }
}
