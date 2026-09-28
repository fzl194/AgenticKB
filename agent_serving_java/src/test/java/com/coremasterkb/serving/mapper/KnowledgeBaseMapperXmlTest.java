package com.coremasterkb.serving.mapper;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("KnowledgeBaseMapper identity boundary contract")
class KnowledgeBaseMapperXmlTest {

    private static String mapperXml() throws Exception {
        try (InputStream in = KnowledgeBaseMapperXmlTest.class.getClassLoader()
                .getResourceAsStream("mapper/KnowledgeBaseMapper.xml")) {
            assertThat(in).as("mapper XML must be on classpath").isNotNull();
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    @Test
    @DisplayName("KB access resolves only active, non-deleted users")
    void userJoinRejectsDisabledAndDeletedIdentities() throws Exception {
        String xml = mapperXml();
        int joinStart = xml.indexOf("LEFT JOIN kb_users u");
        int joinEnd = xml.indexOf("WHERE kb.status = 'active'", joinStart);

        assertThat(joinStart).isGreaterThanOrEqualTo(0);
        assertThat(joinEnd).isGreaterThan(joinStart);
        assertThat(xml.substring(joinStart, joinEnd))
                .contains("u.status = 'active'")
                .contains("u.deleted_at IS NULL");
    }
}
