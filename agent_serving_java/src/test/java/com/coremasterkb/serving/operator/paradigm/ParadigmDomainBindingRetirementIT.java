package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.AgentServingApplication;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.test.context.ActiveProfiles;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * 发布复审 P1 回归：旧域绑定唯一索引的退役前向迁移（003）。
 *
 * <p>场景：存量升级库中 002 创建的 {@code uq_paradigm_domain_default} 部分唯一索引与
 * 绑定列仍在。旧库若残留双默认（archived default + active default 同域），范式重新
 * publish/rollback 的 status 翻转会撞 23505，且绑定写路径已删除、无恢复手段。003 必须
 * 幂等地移除索引与列，让翻转变回普通 UPDATE。</p>
 */
@SpringBootTest(classes = AgentServingApplication.class)
@ActiveProfiles("test-pg")
@Tag("pg-integration")
@DisplayName("003 域绑定退役迁移")
class ParadigmDomainBindingRetirementIT {

    private static final String DOMAIN = "retire_fix_test_domain";
    private static final String LEGACY_INDEX_DDL = """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_paradigm_domain_default
            ON operator_paradigm (bound_domain)
            WHERE is_default AND status = 'active' AND bound_domain IS NOT NULL
            """;

    @Autowired
    @Qualifier("defaultDataSource")
    private DataSource defaultDataSource;

    @Test
    @DisplayName("旧库双默认残留 → 003 迁移 → publish 等价翻转不再 23505（且迁移幂等）")
    void migrationRemovesLegacyIndexAndUnblocksRepublish() throws SQLException {
        try (Connection conn = defaultDataSource.getConnection()) {
            assumeTrue(conn.isValid(3), "PostgreSQL not reachable — skipping");

            cleanup(conn);
            // 1) 还原旧库形态：002 的绑定列 + 部分唯一索引 + 双默认残留
            try (var st = conn.createStatement()) {
                st.execute("ALTER TABLE operator_paradigm ADD COLUMN IF NOT EXISTS bound_domain VARCHAR(64)");
                st.execute("ALTER TABLE operator_paradigm ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE");
                st.execute("ALTER TABLE operator_paradigm ADD COLUMN IF NOT EXISTS bound_at TIMESTAMP");
                st.execute(LEGACY_INDEX_DDL);
                st.execute("INSERT INTO operator_paradigm (id, name, description, draft_graph_json, current_version, status, bound_domain, is_default) "
                        + "VALUES ('retire-fix-a', 'retire-fix-a', '', '{}', 1, 'archived', '" + DOMAIN + "', TRUE), "
                        + "('retire-fix-b', 'retire-fix-b', '', '{}', 1, 'active',   '" + DOMAIN + "', TRUE)");
            }

            // 2) 迁移前：重新发布 A（archived → active）确实被旧索引以 23505 拒绝
            SQLException duplicated = assertThrows(SQLException.class, () -> {
                try (var st = conn.createStatement()) {
                    st.executeUpdate("UPDATE operator_paradigm SET status='active', current_version=2 "
                            + "WHERE id='retire-fix-a'");
                }
            });
            assertThat(duplicated.getSQLState()).isEqualTo("23505");
            // 还原 A 的 archived，使后续步骤语义干净
            try (var st = conn.createStatement()) {
                st.executeUpdate("UPDATE operator_paradigm SET status='archived', current_version=1 WHERE id='retire-fix-a'");
            }

            // 3) 执行 003 前向迁移
            runScript("db/operator/003_paradigm_domain_binding_retirement.sql");

            // 4) 索引与绑定列已被移除
            assertThat(indexExists(conn)).isFalse();
            assertThat(boundColumns(conn)).isEmpty();

            // 5) 同一条翻转语句现在成功（publish/rollback 语义恢复）
            try (var st = conn.createStatement()) {
                int updated = st.executeUpdate("UPDATE operator_paradigm SET status='active', current_version=2 "
                        + "WHERE id='retire-fix-a'");
                assertThat(updated).isEqualTo(1);
            }

            // 6) 迁移幂等：重复执行无异常
            runScript("db/operator/003_paradigm_domain_binding_retirement.sql");

            cleanup(conn);
        }
    }

    private void runScript(String path) {
        ResourceDatabasePopulator populator = new ResourceDatabasePopulator(
                new ClassPathResource(path));
        populator.setContinueOnError(false);
        try (Connection conn = defaultDataSource.getConnection()) {
            populator.populate(conn);
        } catch (SQLException e) {
            throw new IllegalStateException("003 迁移执行失败: " + e.getMessage(), e);
        }
    }

    private boolean indexExists(Connection conn) throws SQLException {
        try (var st = conn.createStatement();
             var rs = st.executeQuery(
                     "SELECT 1 FROM pg_indexes WHERE tablename='operator_paradigm' "
                             + "AND indexname='uq_paradigm_domain_default'")) {
            return rs.next();
        }
    }

    private List<String> boundColumns(Connection conn) throws SQLException {
        try (var st = conn.createStatement();
             var rs = st.executeQuery(
                     "SELECT column_name FROM information_schema.columns "
                             + "WHERE table_name='operator_paradigm' "
                             + "AND column_name IN ('bound_domain','is_default','bound_at')")) {
            var out = new java.util.ArrayList<String>();
            while (rs.next()) out.add(rs.getString(1));
            return out;
        }
    }

    private void cleanup(Connection conn) throws SQLException {
        try (var st = conn.createStatement()) {
            st.execute("DELETE FROM operator_paradigm WHERE id IN ('retire-fix-a','retire-fix-b')");
            st.execute("DROP INDEX IF EXISTS uq_paradigm_domain_default");
            // 列交由 003 清理；此处仅在测试行残留的兜底路径上保持幂等
        }
    }
}
