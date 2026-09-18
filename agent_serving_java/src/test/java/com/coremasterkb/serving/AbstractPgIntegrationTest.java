package com.coremasterkb.serving;

import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.repository.AssetRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.List;

import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Base class for all PostgreSQL integration tests.
 *
 * <p>Features:
 * <ul>
 *   <li>Uses real PG via environment variables</li>
 *   <li>Gracefully skips if PG is unreachable</li>
 *   <li>Dynamically resolves active scope from live data</li>
 * </ul>
 */
@SpringBootTest(classes = AgentServingApplication.class)
@ActiveProfiles("test-pg")
@Tag("pg-integration")
public abstract class AbstractPgIntegrationTest {

    @Autowired
    protected DataSource dataSource;

    @Autowired
    protected AssetRepository assetRepository;

    protected ActiveScope activeScope;

    @BeforeEach
    void ensurePgConnectionAndResolveScope() {
        boolean connectionOk = checkConnection();
        assumeTrue(connectionOk, "PostgreSQL not reachable — skipping PG integration test");

        try (Connection conn = dataSource.getConnection();
             var stmt = conn.prepareStatement(
                     "SELECT id, domain FROM knowledge_bases WHERE status = 'active' ORDER BY id LIMIT 1");
             var rs = stmt.executeQuery()) {
            assumeTrue(rs.next(), "No active KB found — skipping");
            this.activeScope = assetRepository.resolveKbScope(
                    rs.getString("domain"), List.of(rs.getString("id")));
        } catch (Exception e) {
            assumeTrue(false, "Cannot resolve an active KB scope: " + e.getMessage());
        }

        assumeTrue(activeScope.snapshotIds() != null && !activeScope.snapshotIds().isEmpty(),
                "No snapshot IDs found — skipping");
    }

    private boolean checkConnection() {
        try (Connection conn = dataSource.getConnection()) {
            return conn.isValid(3);
        } catch (SQLException e) {
            return false;
        }
    }
}
