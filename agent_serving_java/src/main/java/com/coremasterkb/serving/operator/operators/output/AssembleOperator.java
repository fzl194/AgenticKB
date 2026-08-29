package com.coremasterkb.serving.operator.operators.output;

import com.coremasterkb.serving.domain.EvidenceResponse;
import com.coremasterkb.serving.domain.HydratedEvidence;
import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.operator.core.ErrorPolicy;
import com.coremasterkb.serving.operator.core.ExecContext;
import com.coremasterkb.serving.operator.core.Operator;
import com.coremasterkb.serving.operator.core.OperatorDef;
import com.coremasterkb.serving.operator.core.Params;
import com.coremasterkb.serving.operator.core.SlotDecl;
import com.coremasterkb.serving.operator.core.SlotType;
import com.coremasterkb.serving.operator.core.SlotValues;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;

/**
 * {@code assemble} — 终点算子（批次8 R6 重写，25 号 §6.9/§5.3）。
 *
 * <p><b>唯一职责</b>（纯内存确定性处理，无 DB/网络/LLM）：</p>
 * <ol>
 *   <li>按 canonical evidence 合并；</li>
 *   <li>span 重叠与父子包含去重（先到先得 = rerank/RRF 顺序；父到时吸收已保留子项）；</li>
 *   <li>保持 rerank/RRF 证据顺序（同证据扩展片段按源 ordinal 已由 hydrate 排好）；</li>
 *   <li>执行 maxEvidence/maxOutputTokens 预算：item truncated + 顶层 has_more；</li>
 *   <li>投影 §5.3 公开协议并剥离内部信息（不暴露内部 UUID/unit id/score/rank/边/范式节点）。</li>
 * </ol>
 *
 * <p>opaque ref：{@code ev_} 前缀 + (snapshotId, canonicalEvidenceId) 的 HMAC 短哈希
 * （{@link EvidenceRefCodec}，不可枚举不可逆、同输入稳定）；document/structure ref 同理
 * （{@code doc_}/{@code st_}）。每次执行把 ref→(snapshot, canonical) 写入 ctx attribute
 * {@code evidenceRefIndex}（请求级解析缓存，供 R8 get_evidence /
 * {@link EvidenceRefResolver} 先查同请求缓存再走持久解析）。</p>
 */
@Component
public class AssembleOperator implements Operator {

    private static final Logger log = LoggerFactory.getLogger(AssembleOperator.class);

    /** 预算内至少保留的条目 token 数：连这个都不够时不再塞半截证据，直接 has_more。 */
    static final int MIN_ITEM_TOKENS = 64;

    private static final String PARAM_SCHEMA = """
            {"type":"object","properties":{
              "maxEvidence":{"type":"integer","minimum":1,"maximum":50,"default":10,\
            "title":"最大证据条数"},
              "maxOutputTokens":{"type":"integer","minimum":256,"maximum":100000,"default":3000,\
            "title":"输出 token 预算","description":"超出预算的条目截断（truncated=true）或顺延（has_more=true）"}
            }}""";

    /** ctx attribute：ref → (snapshotId, canonicalEvidenceId) 请求级解析缓存。 */
    public static final String REF_INDEX_ATTRIBUTE = "evidenceRefIndex";

    private final EvidenceRefCodec refCodec;

    public AssembleOperator(EvidenceRefCodec refCodec) {
        this.refCodec = refCodec;
    }

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "assemble", "output", "组装证据响应（生产用）",
                "终点算子：canonical 合并→span/父子去重→保序→maxEvidence/maxOutputTokens 预算→投影 EvidenceResponse",
                List.of(SlotDecl.required("hydratedEvidence", SlotType.HYDRATED_EVIDENCE_LIST, "水合证据")),
                List.of(SlotDecl.required("evidenceResponse", SlotType.EVIDENCE_RESPONSE, "证据响应(终点)")),
                PARAM_SCHEMA,
                ErrorPolicy.FAIL_FAST);
    }

    @Override
    public SlotValues execute(SlotValues inputs, Params params, ExecContext ctx) {
        List<HydratedEvidence> hydrated = inputs.getHydratedEvidence("hydratedEvidence");
        int maxEvidence = ctx.resolveTopK(params.getInt("maxEvidence", 10), 50);
        int maxOutputTokens = params.getInt("maxOutputTokens", 3000);

        String query = ctx.query() == null ? "" : ctx.query();

        // 1) canonical 合并（先到先得 = rerank 顺序）
        Map<String, HydratedEvidence> merged = new LinkedHashMap<>();
        for (HydratedEvidence e : hydrated) {
            merged.putIfAbsent(mergeKey(e), e);
        }

        // 2) span 重叠 / 父子包含去重
        List<HydratedEvidence> deduped = dedupe(merged.values());

        // 3+4) 预算 + 5) 投影
        Map<String, EvidenceRefResolver.ResolvedRef> refIndex = new LinkedHashMap<>();
        List<EvidenceResponse.EvidenceItem> items = new ArrayList<>();
        int remaining = maxOutputTokens;
        boolean hasMore = false;
        int truncatedCount = 0;

        for (HydratedEvidence e : deduped) {
            if (items.size() >= maxEvidence) {
                hasMore = true;
                break;
            }
            int cost = e.tokenEstimate();
            boolean itemTruncated = isHydrationTruncated(e);
            String content = e.contentText();
            if (cost > remaining) {
                if (remaining < MIN_ITEM_TOKENS) {
                    hasMore = true;
                    break;
                }
                int charBudget = remaining * 4;
                content = content.length() > charBudget ? content.substring(0, charBudget) : content;
                itemTruncated = true;
                remaining = 0;
            } else {
                remaining -= cost;
            }

            String ref = refCodec.encodeEvidence(e.snapshotId(), e.canonicalEvidenceId());
            refIndex.put(ref, new EvidenceRefResolver.ResolvedRef(
                    e.snapshotId(), EvidenceRefResolver.RefKind.EVIDENCE, e.canonicalEvidenceId()));
            if (itemTruncated) {
                truncatedCount++;
            }
            items.add(toItem(e, ref, content, itemTruncated));
        }

        ctx.putAttribute(REF_INDEX_ATTRIBUTE, refIndex);
        ctx.putAttribute("assembleMergedCount", merged.size());
        ctx.putAttribute("assembleDedupDropped", merged.size() - deduped.size());
        ctx.putAttribute("assembleTruncatedCount", truncatedCount);
        ctx.putAttribute("assembleHasMore", hasMore);

        EvidenceResponse response = new EvidenceResponse(query, items, hasMore);
        return SlotValues.of("evidenceResponse", response);
    }

    // -------------------------------------------------------------------------
    // Dedup: span overlap + parent/child containment
    // -------------------------------------------------------------------------

    /**
     * 去重规则（先到先得，保持 rerank 顺序）：
     * <ul>
     *   <li>父子包含：已保留 document 吸收同文档一切后到项；已保留 section/table 吸收其
     *       子 segment/table_row；反之，后到的父（section/table/document）吸收已保留的子项；</li>
     *   <li>span 重叠：同 parent 下已保留窗口覆盖新证据的 exact ordinal → 丢弃新证据；
     *       新证据窗口完整覆盖已保留证据的 exact ordinal → 吸收（移除旧项，新项按自身位次保留）。</li>
     * </ul>
     */
    private static List<HydratedEvidence> dedupe(java.util.Collection<HydratedEvidence> merged) {
        LinkedList<HydratedEvidence> kept = new LinkedList<>();
        for (HydratedEvidence incoming : merged) {
            boolean dropIncoming = false;
            Iterator<HydratedEvidence> it = kept.iterator();
            while (it.hasNext()) {
                HydratedEvidence held = it.next();
                if (!held.snapshotId().equals(incoming.snapshotId())) {
                    continue;
                }
                if (contains(held, incoming)) {
                    dropIncoming = true;
                    break;
                }
                if (contains(incoming, held)) {
                    it.remove();
                }
            }
            if (!dropIncoming) {
                kept.add(incoming);
            }
        }
        return new ArrayList<>(kept);
    }

    /** a 是否完整包含 b 的证据范围（父子包含或 span 覆盖）。 */
    private static boolean contains(HydratedEvidence a, HydratedEvidence b) {
        // document ⊃ 同文档一切
        if ("document".equals(a.targetType())) {
            return a.documentRef() != null && a.documentRef().equals(b.documentRef());
        }
        // section ⊃ 其直接子 segment（b.parentRef == a.targetRef）
        if ("section".equals(a.targetType())) {
            return a.targetRef() != null && a.targetRef().equals(b.parentRef())
                    && "segment".equals(b.targetType());
        }
        // table ⊃ 其 table_row（b.parentRef = table_ref；用 structureRefs 里的 asset ref 判定）
        if ("table".equals(a.targetType())) {
            String assetRef = a.structureRefs().isEmpty() ? null : a.structureRefs().get(0);
            return assetRef != null && b.parentRef() != null && assetRef.endsWith("#table:" + b.parentRef());
        }
        // segment span 覆盖：同 parent，a 窗口 ⊇ b 的 exact ordinal
        if ("segment".equals(a.targetType()) && "segment".equals(b.targetType())
                && a.parentRef() != null && a.parentRef().equals(b.parentRef())
                && b.ordinal() != null && a.windowFrom() != null && a.windowTo() != null) {
            return b.ordinal() >= a.windowFrom() && b.ordinal() <= a.windowTo();
        }
        return false;
    }

    // -------------------------------------------------------------------------
    // Projection
    // -------------------------------------------------------------------------

    private EvidenceResponse.EvidenceItem toItem(HydratedEvidence e, String ref, String content,
                                                 boolean truncated) {
        HydratedEvidence.SourceProjection src = e.source();
        EvidenceResponse.EvidenceSource source = new EvidenceResponse.EvidenceSource(
                src != null ? src.knowledgeBase() : null,
                src != null ? src.fileName() : null,
                src != null ? src.relativePath() : null,
                e.documentRef() != null ? refCodec.encodeDocument(e.snapshotId(), e.documentRef()) : null,
                src != null ? src.section() : null,
                src != null ? src.page() : null);
        String structureRef = e.navigable() && !e.structureRefs().isEmpty()
                ? refCodec.encodeStructure(e.snapshotId(), e.structureRefs().get(0)) : null;
        return new EvidenceResponse.EvidenceItem(ref, e.evidenceType(), content, source,
                truncated, structureRef);
    }

    /** hydrate 阶段已判不完整（章节/文档/表格有界截断）→ 条目 truncated=true。 */
    private static boolean isHydrationTruncated(HydratedEvidence e) {
        return e.provenance().get("truncated") instanceof Boolean b && b;
    }

    private static String mergeKey(HydratedEvidence e) {
        return e.snapshotId() + "|" + e.canonicalEvidenceId();
    }
}
