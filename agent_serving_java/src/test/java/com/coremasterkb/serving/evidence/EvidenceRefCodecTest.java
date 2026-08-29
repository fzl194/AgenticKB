package com.coremasterkb.serving.evidence;

import com.coremasterkb.serving.config.ServingProperties;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 批次8 R6：opaque ref 编码契约——同输入稳定、跨输入/跨类不撞、密钥轮换失效、
 * ev_/doc_/st_ 前缀 + Base64Url 短哈希（无填充、无 +/- 字符）。
 */
@DisplayName("EvidenceRefCodec")
class EvidenceRefCodecTest {

    @Test
    @DisplayName("stable for the same (snapshot, canonical) under the same secret")
    void stableForSameKey() {
        EvidenceRefCodec codec = EvidenceRefCodec.forSecret("s1");
        String a = codec.encodeEvidence("snap-1", "doc:/a#seg:1");
        String b = codec.encodeEvidence("snap-1", "doc:/a#seg:1");
        assertThat(a).isEqualTo(b);
    }

    @Test
    @DisplayName("distinct across keys, snapshots, and ref domains")
    void distinctAcrossInputs() {
        EvidenceRefCodec codec = EvidenceRefCodec.forSecret("s1");
        String evidence = codec.encodeEvidence("snap-1", "doc:/a#seg:1");
        assertThat(codec.encodeEvidence("snap-1", "doc:/a#seg:2")).isNotEqualTo(evidence);
        assertThat(codec.encodeEvidence("snap-2", "doc:/a#seg:1")).isNotEqualTo(evidence);
        // 域分隔：同 (snapshot, ref) 的 doc_/st_ 编码互不相同，防跨类 ref 替换
        assertThat(codec.encodeDocument("snap-1", "doc:/a#seg:1")).isNotEqualTo(evidence);
        assertThat(codec.encodeStructure("snap-1", "doc:/a#seg:1")).isNotEqualTo(evidence);
        assertThat(codec.encodeDocument("snap-1", "doc:/a#seg:1"))
                .isNotEqualTo(codec.encodeStructure("snap-1", "doc:/a#seg:1"));
    }

    @Test
    @DisplayName("prefix + 12-char Base64Url short hash (URL-safe alphabet, no padding)")
    void shape() {
        EvidenceRefCodec codec = EvidenceRefCodec.forSecret("s1");
        String ref = codec.encodeEvidence("snap-1", "doc:/a#seg:1");
        assertThat(ref).startsWith("ev_").hasSize(15);
        String hash = ref.substring(3);
        assertThat(hash).matches("[A-Za-z0-9_-]{12}");
        assertThat(codec.encodeDocument("snap-1", "doc:/a")).startsWith("doc_");
        assertThat(codec.encodeStructure("snap-1", "doc:/a#section:X")).startsWith("st_");
    }

    @Test
    @DisplayName("secret rotation invalidates refs (not enumerable without the secret)")
    void secretRotationChangesRefs() {
        String withA = EvidenceRefCodec.forSecret("secret-a").encodeEvidence("snap-1", "c");
        String withB = EvidenceRefCodec.forSecret("secret-b").encodeEvidence("snap-1", "c");
        assertThat(withA).isNotEqualTo(withB);
    }

    @Test
    @DisplayName("serving.evidence-ref.secret is honored when configured")
    void honorsConfiguredSecret() {
        ServingProperties props = new ServingProperties(null, null, null, null, null, null,
                new ServingProperties.EvidenceRef("configured-secret"));
        EvidenceRefCodec fromProps = new EvidenceRefCodec(props);
        assertThat(fromProps.encodeEvidence("snap", "c"))
                .isEqualTo(EvidenceRefCodec.forSecret("configured-secret").encodeEvidence("snap", "c"));
    }
}
