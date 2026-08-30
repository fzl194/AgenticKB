package com.coremasterkb.serving.operator.operators.output;

import com.coremasterkb.serving.application.KbAccessService;
import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.operator.core.*;
import com.coremasterkb.serving.repository.AssetRepository;
import com.coremasterkb.serving.structure.StructureRefService;
import com.coremasterkb.serving.structure.StructureToolException;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * {@code scope_resolve} — resolve the document snapshots a retrieval may see (25 号 §6.2).
 *
 * <p>Default: the domain + channel's active release. With the {@code kbIds} param set, the scope
 * narrows to those knowledge bases and is resolved from their builds instead — KB mining runs
 * {@code publish=false}, so KB content never reaches a release and the default path would always
 * miss it. See {@link AssetRepository#resolveKbScope(String, List)}.</p>
 *
 * <p>{@code kbIds} lives in the node params, which is what makes "pick a set of KBs" a
 * design-time property of the paradigm: it is frozen into the stored graph rather than supplied
 * per request. The param is authorized against the caller's identity on every execution
 * regardless — a saved graph must not become a way to read a KB you cannot open.</p>
 *
 * <p><b>R1 hard filters 通道（批次8）：</b>请求显式传入的 within/filters（document_refs/
 * section_refs/relative_path_prefix/asset_types/evidence_types/date_range 等）经
 * {@link ExecContext#requestFilters()} 原样透传进 {@code ActiveScope.hardFilters}，作为下游
 * 算子 Top-K 前下推的约束。本算子<b>只透传，不从 query 推断任何范围</b>；授权求交逻辑
 * （批次5/6 三层解析）保持不动。</p>
 */
@Component
public class ScopeResolveOperator implements Operator {

    /**
     * {@code x-widget} is a UI hint, not a validation keyword — JSON Schema ignores unknown
     * keywords, so it changes nothing about how params are validated. The editor uses it to render
     * a knowledge-base picker instead of a free-text tag input.
     */
    private static final String PARAM_SCHEMA = """
            {"type":"object","properties":{\
            "kbIds":{"type":"array","items":{"type":"string"},\
            "x-widget":"kb-picker",\
            "title":"知识库范围",\
            "description":"推荐留空 = 检索时按用户开放库/请求指定的库组合自动注入（通用范式）；写死 = 专属范式，只检索这些知识库并忽略检索请求传入的库"}}}""";

    private final AssetRepository assetRepository;
    private final KbAccessService kbAccessService;
    private final StructureRefService refService;

    public ScopeResolveOperator(AssetRepository assetRepository, KbAccessService kbAccessService,
                                StructureRefService refService) {
        this.assetRepository = assetRepository;
        this.kbAccessService = kbAccessService;
        this.refService = refService;
    }

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "scope_resolve", "scope", "范围解析",
                "根据 domain/channel 解析当前生效的 release 与文档快照范围；可按知识库收窄；透传请求显式 hard filters",
                List.of(),
                List.of(SlotDecl.required("scope", SlotType.SCOPE, "检索范围(snapshotIds+hardFilters)")),
                PARAM_SCHEMA,
                ErrorPolicy.FAIL_FAST);
    }

    @Override
    public SlotValues execute(SlotValues inputs, Params params, ExecContext ctx) {
        // 阶段 A 菜谱+运行时范围：图内写死 kbIds = 专属范式优先（请求值被忽略并留痕）；
        // 图内留空 = 通用范式，用请求现场指定的库组合；请求也未带 = 域级 release（原语义）。
        List<String> paramKbIds = params.getStringList("kbIds");
        List<String> requestKbIds = ctx.requestKbIds() == null ? List.of() : ctx.requestKbIds();
        List<String> effectiveKbIds;
        if (!paramKbIds.isEmpty()) {
            effectiveKbIds = paramKbIds;
            if (!requestKbIds.isEmpty()) {
                ctx.putAttribute("ignoredRequestKbIds", requestKbIds);
            }
        } else {
            effectiveKbIds = requestKbIds;
        }

        List<String> kbIds =
                kbAccessService.authorize(ctx.domain(), effectiveKbIds, ctx.username());

        ActiveScope scope = assetRepository.resolveActiveScope(ctx.domain(), ctx.channel(), kbIds);

        // R1：显式 hard filters 透传（不推断、不改写——规范化归下游算子的确定性映射）。
        // 27号审查修复：①未支持的键（path/date 等 v2 表暂无可下推列）显式 400，
        // 不再静默忽略造成"以为过滤了其实没滤"；②doc_/st_ opaque ref 先解码成
        // 内部 ref——公开身份进 SQL 比较前必须还原（否则恒零命中）。
        Map<String, Object> requestFilters = ctx.requestFilters();
        if (requestFilters != null && !requestFilters.isEmpty()) {
            for (String key : requestFilters.keySet()) {
                if (!SUPPORTED_FILTER_KEYS.contains(key)) {
                    throw new IllegalArgumentException("unsupported_scope_filter:" + key);
                }
            }
            Map<String, Object> decoded = decodeOpaqueRefs(
                    requestFilters, ctx.domain(), kbIds, ctx.username());
            scope = scope.withHardFilters(decoded);
            ctx.putAttribute("hardFilterKeys", List.copyOf(decoded.keySet()));
        }

        ctx.putAttribute("releaseId", scope.releaseId());
        ctx.putAttribute("buildId", scope.buildId());
        ctx.putAttribute("snapshotCount", scope.snapshotIds().size());
        if (!kbIds.isEmpty()) {
            ctx.putAttribute("kbIds", kbIds);
        }
        return SlotValues.of("scope", scope);
    }

    /** 27号审查修复：与请求边界（ParadigmRequests）共用 ActiveScope 单一真相源；
     *  此处二次校验属纵深防御（图参数注入路径）。 */
    static final Set<String> SUPPORTED_FILTER_KEYS =
            ActiveScope.SUPPORTED_FILTER_KEYS;

    /**
     * document_refs/section_refs 里的 doc_/st_ opaque ref 解码为内部 ref。
     *
     * <p>解码走 {@link StructureRefService} 的授权枚举（与结构工具同源：开放库求交
     * + 活动/历史快照扫描），非法/越权/失效统一映射 invalid_scope_ref（400）。
     * 明文内部 ref 原样透传（服务端直连 API 的既有契约）。域级 release 范围
     * （无 kbIds）无法枚举候选——opaque ref 要求请求带库范围。</p>
     */
    private Map<String, Object> decodeOpaqueRefs(Map<String, Object> filters,
                                                  String domain, List<String> kbIds,
                                                  String username) {
        Map<String, Object> out = new LinkedHashMap<>(filters);
        decodeRefList(out, "document_refs", domain, kbIds, username);
        decodeRefList(out, "section_refs", domain, kbIds, username);
        return out;
    }

    private void decodeRefList(Map<String, Object> filters, String key,
                               String domain, List<String> kbIds, String username) {
        Object value = filters.get(key);
        if (!(value instanceof List<?> list) || list.isEmpty()) {
            return;
        }
        List<Object> decoded = new ArrayList<>(list.size());
        for (Object item : list) {
            if (!(item instanceof String ref) || ref.isBlank()) {
                decoded.add(item);
                continue;
            }
            if (!ref.startsWith(EvidenceRefCodec.DOCUMENT_PREFIX)
                    && !ref.startsWith(EvidenceRefCodec.STRUCTURE_PREFIX)) {
                decoded.add(ref); // 明文内部 ref：直连 API 契约，原样透传
                continue;
            }
            if (kbIds == null || kbIds.isEmpty()) {
                throw new IllegalArgumentException("scope_ref_requires_kb");
            }
            try {
                EvidenceRefResolver.ResolvedRef resolved =
                        refService.resolve(ref, domain, kbIds, username);
                decoded.add(resolved.internalRef());
            } catch (StructureToolException e) {
                throw new IllegalArgumentException(
                        "invalid_scope_ref: " + ref + " (" + e.getMessage() + ")");
            }
        }
        filters.put(key, decoded);
    }
}
