package com.coremasterkb.serving.evidence;

import com.coremasterkb.serving.config.ServingProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Base64;

/**
 * Opaque public ref 编码器（25 号 §6.8-9/§10.1，批次8 R6）。
 *
 * <p>ref = {@code ev_|doc_|st_} 前缀 + HMAC-SHA256(密钥, 域分隔符 + snapshotId + canonical/
 * structure ref) 的 Base64Url 短哈希（12 字符 ≈ 72 bit）。同一密钥下对同一 (snapshot, ref)
 * 确定、稳定；无密钥不可枚举、不可逆——满足"防篡改/不可枚举"。密钥来自
 * {@code serving.evidence-ref.secret}（生产经 {@code SERVING_EVIDENCE_REF_SECRET} 注入）；
 * 留空时生成进程内随机遇 boot 密钥并告警（refs 进程内稳定、重启后变化，仅适合开发/测试）。</p>
 */
@Component
public class EvidenceRefCodec {

    private static final Logger log = LoggerFactory.getLogger(EvidenceRefCodec.class);

    public static final String EVIDENCE_PREFIX = "ev_";
    public static final String DOCUMENT_PREFIX = "doc_";
    public static final String STRUCTURE_PREFIX = "st_";

    /** HMAC 输出取前 12 个 Base64Url 字符（72 bit）：对不可枚举性足够，人读友好。 */
    public static final int SHORT_HASH_CHARS = 12;

    /** 域分隔符：ev/doc/st 三类 ref 的 HMAC 输入不同域，防跨类替换。 */
    private static final byte DOMAIN_EVIDENCE = 0x01;
    private static final byte DOMAIN_DOCUMENT = 0x02;
    private static final byte DOMAIN_STRUCTURE = 0x03;

    private final byte[] secret;

    public EvidenceRefCodec(ServingProperties properties) {
        this(bootSecret(properties));
    }

    EvidenceRefCodec(String secret) {
        this.secret = secret.getBytes(StandardCharsets.UTF_8);
    }

    /** 测试/显式密钥构造：同密钥实例输出一致。 */
    public static EvidenceRefCodec forSecret(String secret) {
        return new EvidenceRefCodec(secret);
    }

    /** 证据 ref：绑定 (snapshotId, canonicalEvidenceId)。 */
    public String encodeEvidence(String snapshotId, String canonicalEvidenceId) {
        return EVIDENCE_PREFIX + shortHash(DOMAIN_EVIDENCE, snapshotId, canonicalEvidenceId);
    }

    /** 文档 ref：绑定 (snapshotId, documentRef)。 */
    public String encodeDocument(String snapshotId, String documentRef) {
        return DOCUMENT_PREFIX + shortHash(DOMAIN_DOCUMENT, snapshotId, documentRef);
    }

    /** 结构/资产 ref：绑定 (snapshotId, structureRef)。 */
    public String encodeStructure(String snapshotId, String structureRef) {
        return STRUCTURE_PREFIX + shortHash(DOMAIN_STRUCTURE, snapshotId, structureRef);
    }

    private static String bootSecret(ServingProperties properties) {
        String configured = properties != null && properties.evidenceRef() != null
                ? properties.evidenceRef().secret() : null;
        if (configured != null && !configured.isBlank()) {
            return configured;
        }
        byte[] random = new byte[32];
        new SecureRandom().nextBytes(random);
        log.warn("[evidence-ref] serving.evidence-ref.secret 未配置——使用进程内随机遇 boot 密钥。"
                + "refs 在本进程内稳定，重启后变化（get_evidence 跨重启解析需 R8 实装持久解析）。"
                + "生产请注入 SERVING_EVIDENCE_REF_SECRET。");
        return Base64.getEncoder().encodeToString(random);
    }

    private String shortHash(byte domain, String snapshotId, String ref) {
        String input = (snapshotId == null ? "" : snapshotId) + "\n" + (ref == null ? "" : ref);
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            mac.update(domain);
            byte[] digest = mac.doFinal(input.getBytes(StandardCharsets.UTF_8));
            String full = Base64.getUrlEncoder().withoutPadding().encodeToString(digest);
            return full.substring(0, Math.min(SHORT_HASH_CHARS, full.length()));
        } catch (GeneralSecurityException e) {
            // JDK 保证 HmacSHA256 存在；到这里的唯一路径是 JCA 被裁剪，属系统性错误
            throw new IllegalStateException("HmacSHA256 unavailable for evidence ref encoding", e);
        }
    }
}
