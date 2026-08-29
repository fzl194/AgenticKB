package com.coremasterkb.serving.evidence;

import java.util.List;

/**
 * Opaque public ref 的解析接口（25 号 §8.1，批次8 R7 实装）。
 *
 * <p>resolve(opaqueRef) → 授权校验 → (snapshotId, refKind, internalRef)。实现必须：
 * 解析后再次校验用户开放 KB、active snapshot/build 与对象类型（§10.1）；ref 非法/过期返回
 * 稳定 typed error（{@code invalid_ref/expired_ref}），未授权返回 {@code out_of_scope}
 * 且不泄漏对象真实归属。</p>
 *
 * <p>实现：{@code structure.StructureRefService}——同一 HMAC 密钥下对授权 snapshot 集内
 * 的候选内部 ref 枚举重编码匹配（72 bit 短哈希，碰撞概率可忽略），活动快照未命中再走有界
 * 历史快照扫描判定 {@code expired_ref}。</p>
 */
public interface EvidenceRefResolver {

    /** ref 类别（与 {@link EvidenceRefCodec} 三前缀一一对应）。 */
    enum RefKind { EVIDENCE, DOCUMENT, STRUCTURE }

    /** 解析结果：opaque ref 绑定的不可变快照与内部身份（evidence=canonical id）。 */
    record ResolvedRef(String snapshotId, RefKind kind, String internalRef) {}

    /**
     * 授权校验后的 ref 解析。
     *
     * @param opaqueRef ev_/doc_/st_ 前缀 opaque ref
     * @param domain    知识域（路由 DB）
     * @param kbIds     请求级库范围（必须非空——内部端点由 MCP 按开放库注入）
     * @param username  调用者身份（X-KB-User 透传；null = anonymous）
     * @throws com.coremasterkb.serving.structure.StructureToolException
     *         稳定错误码（invalid_ref/expired_ref/out_of_scope）
     */
    ResolvedRef resolve(String opaqueRef, String domain, List<String> kbIds, String username);
}
