package com.coremasterkb.serving.operator.operators.output;

import com.coremasterkb.serving.application.KbAccessService;
import com.coremasterkb.serving.domain.ActiveScope;
import com.coremasterkb.serving.operator.core.*;
import com.coremasterkb.serving.repository.AssetRepository;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * {@code scope_resolve} — resolve the document snapshots a retrieval may see.
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
            "description":"留空 = 全域生效发布；选择后只检索这些知识库已挖掘的内容"}}}""";

    private final AssetRepository assetRepository;
    private final KbAccessService kbAccessService;

    public ScopeResolveOperator(AssetRepository assetRepository, KbAccessService kbAccessService) {
        this.assetRepository = assetRepository;
        this.kbAccessService = kbAccessService;
    }

    @Override
    public OperatorDef definition() {
        return new OperatorDef(
                "scope_resolve", "scope", "范围解析",
                "根据 domain/channel 解析当前生效的 release 与文档快照范围；可按知识库收窄",
                List.of(),
                List.of(SlotDecl.required("scope", SlotType.SCOPE, "检索范围(snapshotIds)")),
                PARAM_SCHEMA,
                ErrorPolicy.FAIL_FAST);
    }

    @Override
    public SlotValues execute(SlotValues inputs, Params params, ExecContext ctx) {
        List<String> kbIds =
                kbAccessService.authorize(ctx.domain(), params.getStringList("kbIds"), ctx.username());

        ActiveScope scope = assetRepository.resolveActiveScope(ctx.domain(), ctx.channel(), kbIds);
        ctx.putAttribute("releaseId", scope.releaseId());
        ctx.putAttribute("buildId", scope.buildId());
        ctx.putAttribute("snapshotCount", scope.snapshotIds().size());
        if (!kbIds.isEmpty()) {
            ctx.putAttribute("kbIds", kbIds);
        }
        return SlotValues.of("scope", scope);
    }
}
