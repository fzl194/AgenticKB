package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.application.KbAccessService;
import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.AssetBuildDocumentSnapshotMapper;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Set;

/**
 * Opaque public ref 反查 + 授权校验（批次8 R7，25 号 §6.8-9/§10.1）。
 *
 * <p>HMAC 短哈希不可逆，反查 = 对<b>授权 snapshot 集</b>内的候选内部 ref 枚举重编码匹配：
 * 同一密钥下 encode(snapshot, internalRef) 确定，72 bit 短哈希在 ≤20 万候选内的碰撞概率
 * ~10^-10，可忽略。活动快照未命中时再对授权库的<b>历史快照</b>做一次有界扫描——命中即
 * {@code expired_ref}（库已重新挖掘，ref 绑定的旧快照被替换），仍未命中即
 * {@code out_of_scope}（不存在/无权/跨用户同响应，不泄漏归属）。</p>
 *
 * <p>授权链与检索一致（§10.1）：{@link KbAccessService#authorize} 求交开放库 →
 * {@code AssetRepository.resolveKbScope} 解析活动快照；任何一步失败都是 out_of_scope，
 * 不区分"库不存在"与"无权读"。</p>
 */
@Service
public class StructureRefService implements EvidenceRefResolver {

    private static final Logger log = LoggerFactory.getLogger(StructureRefService.class);

    /** 活动 snapshot 候选枚举硬上限（超限按未命中处理，不放大内存/HMAC 成本）。 */
    static final int ACTIVE_CANDIDATE_CAP = 200_000;
    /** 历史 snapshot 候选枚举硬上限（仅 expired_ref 判定路径）。 */
    static final int HISTORICAL_CANDIDATE_CAP = 50_000;

    private final EvidenceRefCodec codec;
    private final KbAccessService kbAccessService;
    private final AssetBuildDocumentSnapshotMapper buildSnapshotMapper;
    private final StructureToolMapper toolMapper;

    public StructureRefService(EvidenceRefCodec codec,
                               KbAccessService kbAccessService,
                               AssetBuildDocumentSnapshotMapper buildSnapshotMapper,
                               StructureToolMapper toolMapper) {
        this.codec = codec;
        this.kbAccessService = kbAccessService;
        this.buildSnapshotMapper = buildSnapshotMapper;
        this.toolMapper = toolMapper;
    }

    @Override
    public ResolvedRef resolve(String opaqueRef, String domain, List<String> kbIds, String username) {
        if (opaqueRef == null || opaqueRef.isBlank()) {
            throw StructureToolException.invalidRef("ref 缺失");
        }
        EvidenceRefResolver.RefKind kind = kindOf(opaqueRef);
        if (kind == null) {
            throw StructureToolException.invalidRef("ref 前缀非法（期望 ev_/doc_/st_）");
        }
        String hash = opaqueRef.substring(3);
        if (hash.length() != EvidenceRefCodec.SHORT_HASH_CHARS) {
            throw StructureToolException.invalidRef("ref 格式非法");
        }

        List<String> authorized = authorizeScope(domain, kbIds, username);
        List<String> activeSnapshots = activeSnapshots(domain, authorized);

        // 1) 活动 snapshot 候选枚举 + HMAC 匹配
        String matched = match(kind, opaqueRef, activeSnapshots, ACTIVE_CANDIDATE_CAP);
        if (matched != null) {
            return new ResolvedRef(snapshotOfMatch(matched), kind, internalOfMatch(matched));
        }

        // 2) 有界历史 snapshot 扫描（expired_ref 判定）
        List<String> historical = historicalSnapshots(domain, authorized, activeSnapshots);
        if (!historical.isEmpty()) {
            String histMatch = match(kind, opaqueRef, historical, HISTORICAL_CANDIDATE_CAP);
            if (histMatch != null) {
                throw StructureToolException.expiredRef(
                        "ref 绑定的快照已不在当前活动范围（库可能已重新挖掘）——请重新 search 拿新 ref");
            }
        }

        log.debug("ref unresolved: kind={}, user={}, kb_count={}",
                kind, username != null ? username : "<anonymous>",
                authorized != null ? authorized.size() : 0);
        throw StructureToolException.outOfScope("ref 不在当前授权范围内（未开放、不存在或已失效）");
    }

    /**
     * 活动 snapshot 集（与检索的 KB scope 判定同源）。
     *
     * @throws StructureToolException out_of_scope——kb_not_found/no_active_kb_build 同响应
     *         （授权库无挖掘产物时任何 ref 都不可能在范围内）
     */
    public List<String> activeSnapshotIds(String domain, List<String> kbIds, String username) {
        List<String> authorized = authorizeScope(domain, kbIds, username);
        return activeSnapshots(domain, authorized);
    }

    /** 授权 kbIds（KbAccessService 抛出的 kb_not_found 统一映射 out_of_scope）。 */
    private List<String> authorizeScope(String domain, List<String> kbIds, String username) {
        List<String> normalized = ActiveScope.normalizeKbIds(kbIds);
        if (normalized.isEmpty()) {
            throw StructureToolException.outOfScope("缺少知识库范围（kb_ids 必填）");
        }
        try {
            return kbAccessService.authorize(domain, normalized, username);
        } catch (IllegalArgumentException e) {
            // 不区分不存在/无权——同一响应
            throw StructureToolException.outOfScope("ref 不在当前授权范围内");
        }
    }

    private List<String> activeSnapshots(String domain, List<String> authorizedKbIds) {
        try {
            return buildSnapshotMapper.selectLatestKbSnapshots(
                            domain != null ? domain : "default", authorizedKbIds).stream()
                    .map(s -> s.getDocumentSnapshotId())
                    .filter(id -> id != null && !id.isEmpty())
                    .distinct()
                    .toList();
        } catch (Exception e) {
            log.warn("[structure-ref] active snapshot resolution failed: {}", e.getMessage());
            throw StructureToolException.outOfScope("ref 不在当前授权范围内");
        }
    }

    /** 授权库的全部历史 snapshot（去活动集；有界）。 */
    private List<String> historicalSnapshots(String domain, List<String> authorizedKbIds,
                                             List<String> activeSnapshots) {
        try {
            Set<String> active = Set.copyOf(activeSnapshots);
            return toolMapper.selectKbSnapshotRefs(
                            domain != null ? domain : "default", authorizedKbIds,
                            HISTORICAL_CANDIDATE_CAP).stream()
                    .map(StructureToolMapper.RefRow::snapshotId)
                    .filter(id -> id != null && !active.contains(id))
                    .distinct()
                    .toList();
        } catch (Exception e) {
            log.warn("[structure-ref] historical snapshot scan failed (treated as none): {}",
                    e.getMessage());
            return List.of();
        }
    }

    /** 枚举候选并匹配；返回 "snapshot|internal"（找到时），null = 未命中。 */
    private String match(EvidenceRefResolver.RefKind kind, String opaqueRef,
                         List<String> snapshotIds, int cap) {
        if (snapshotIds.isEmpty()) {
            return null;
        }
        List<StructureToolMapper.RefRow> candidates = switch (kind) {
            case STRUCTURE -> toolMapper.selectStructureRefCandidates(snapshotIds, cap);
            case DOCUMENT -> toolMapper.selectDocumentRefCandidates(snapshotIds, cap);
            case EVIDENCE -> toolMapper.selectCanonicalRefCandidates(snapshotIds, cap);
        };
        for (StructureToolMapper.RefRow row : candidates) {
            if (row.snapshotId() == null || row.ref() == null) {
                continue;
            }
            String encoded = switch (kind) {
                case EVIDENCE -> codec.encodeEvidence(row.snapshotId(), row.ref());
                case DOCUMENT -> codec.encodeDocument(row.snapshotId(), row.ref());
                case STRUCTURE -> codec.encodeStructure(row.snapshotId(), row.ref());
            };
            if (opaqueRef.equals(encoded)) {
                return row.snapshotId() + "|" + row.ref();
            }
        }
        return null;
    }

    private static EvidenceRefResolver.RefKind kindOf(String ref) {
        if (ref.startsWith(EvidenceRefCodec.EVIDENCE_PREFIX)) return EvidenceRefResolver.RefKind.EVIDENCE;
        if (ref.startsWith(EvidenceRefCodec.DOCUMENT_PREFIX)) return EvidenceRefResolver.RefKind.DOCUMENT;
        if (ref.startsWith(EvidenceRefCodec.STRUCTURE_PREFIX)) return EvidenceRefResolver.RefKind.STRUCTURE;
        return null;
    }

    private static String snapshotOfMatch(String matched) {
        return matched.substring(0, matched.indexOf('|'));
    }

    private static String internalOfMatch(String matched) {
        return matched.substring(matched.indexOf('|') + 1);
    }
}
