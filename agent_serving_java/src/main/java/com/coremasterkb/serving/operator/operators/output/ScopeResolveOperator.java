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
            "description":"推荐留空 = 检索时按用户开放库/请求指定的库组合自动注入（通用范式）；写死 = 专属范式，只检索这些知识库并忽略检索请求传入的库"}}}""";

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
        ctx.putAttribute("releaseId", scope.releaseId());
        ctx.putAttribute("buildId", scope.buildId());
        ctx.putAttribute("snapshotCount", scope.snapshotIds().size());
        if (!kbIds.isEmpty()) {
            ctx.putAttribute("kbIds", kbIds);
        }
        return SlotValues.of("scope", scope);
    }
}
