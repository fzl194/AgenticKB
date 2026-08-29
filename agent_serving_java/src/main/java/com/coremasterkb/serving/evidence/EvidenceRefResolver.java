package com.coremasterkb.serving.evidence;

/**
 * Opaque evidence ref 的解析接口（25 号 §8.1 {@code get_evidence}，批次8 R8 实装留口）。
 *
 * <p>resolve(opaqueRef) → 授权校验 → (snapshotId, canonicalEvidenceId)。实现必须：
 * 解析后再次校验用户开放 KB、active snapshot/build 与对象类型（§10.1）；ref 非法/过期返回
 * 稳定 typed error（{@code invalid_ref/expired_ref}），未授权返回 {@code out_of_scope}
 * 且不泄漏对象真实归属。</p>
 *
 * <p>R6 的 assemble 在每次执行的 {@code ExecContext} 中内置了 ref→(snapshot, canonical)
 * 的请求级解析缓存（attribute {@code evidenceRefIndex}）；R8 的实现可先查该同请求缓存，
 * 再走持久解析（同一 HMAC 密钥下由 ref 反查需要 ref 索引存储或重新绑定）。</p>
 */
public interface EvidenceRefResolver {

    /** 解析结果：opaque ref 绑定的不可变快照与 canonical 证据身份。 */
    record ResolvedEvidence(String snapshotId, String canonicalEvidenceId) {}

    /**
     * 授权校验后的 ref 解析。
     *
     * @param opaqueRef EvidenceResponse.evidence[].ref（ev_ 前缀）
     * @param username  调用者身份（X-KB-User；null = anonymous，仅公开库）
     * @return 绑定的 (snapshotId, canonicalEvidenceId)
     * @throws com.coremasterkb.serving.operator.core.exceptions.OperatorException
     *         R8 实装时以稳定错误码（invalid_ref/expired_ref/out_of_scope）抛出
     */
    ResolvedEvidence resolve(String opaqueRef, String username);
}
