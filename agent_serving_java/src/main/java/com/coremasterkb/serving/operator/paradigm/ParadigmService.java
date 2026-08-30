package com.coremasterkb.serving.operator.paradigm;

import com.coremasterkb.serving.operator.core.exceptions.OperatorException;
import com.coremasterkb.serving.operator.engine.ParadigmCompiler;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmMapper;
import com.coremasterkb.serving.operator.paradigm.mapper.ParadigmVersionMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

/**
 * Paradigm lifecycle: create draft → edit draft → publish (immutable version) → rollback.
 *
 * <p>Publishing compiles the draft first (reusing {@link ParadigmCompiler}); only a valid graph
 * becomes a version. Versions are immutable, so a {@code paradigmId + version} call always
 * reproduces the same result. Stored in the control DB via non-routed mapper calls (no
 * {@code DomainContext} set on these threads).</p>
 */
@Service
public class ParadigmService {

    private static final Logger log = LoggerFactory.getLogger(ParadigmService.class);

    /** 官方默认检索范式固定 id（resolve 官方层兜底）——批次8 R8 新 id，旧 id 不复活。 */
    public static final String OFFICIAL_DEFAULT_ID = "system-hybrid-retrieval";

    /** 关键词证据检索预置（§9.1：不要求 embedding 服务）。 */
    public static final String OFFICIAL_LEXICAL_ID = "system-lexical-retrieval";

    /** §9.1 关键词证据检索：scope → fts → evidence_hydrate → assemble。 */
    static final String OFFICIAL_LEXICAL_GRAPH = """
            {
              "schemaVersion": "1.0",
              "nodes": [
                {"nodeId": "scope", "operatorType": "scope_resolve"},
                {"nodeId": "fts", "operatorType": "fts", "params": {"topK": 20}},
                {"nodeId": "hyd", "operatorType": "evidence_hydrate",
                 "params": {"mode": "auto", "topN": 50}},
                {"nodeId": "asm", "operatorType": "assemble",
                 "params": {"maxEvidence": 10, "maxOutputTokens": 3000}}
              ],
              "edges": [
                {"fromNode": "scope", "fromSlot": "scope", "toNode": "fts", "toSlot": "scope"},
                {"fromNode": "fts", "fromSlot": "candidates", "toNode": "hyd", "toSlot": "candidates"},
                {"fromNode": "scope", "fromSlot": "scope", "toNode": "hyd", "toSlot": "scope"},
                {"fromNode": "hyd", "fromSlot": "hydratedEvidence", "toNode": "asm", "toSlot": "hydratedEvidence"}
              ],
              "output": {"nodeId": "asm", "slot": "evidenceResponse"}
            }
            """;

    /** §9.2 标准混合证据检索（官方默认）：scope+query_embed → fts‖dense → rrf → model_rerank → hydrate → assemble。 */
    static final String OFFICIAL_HYBRID_GRAPH = """
            {
              "schemaVersion": "1.0",
              "nodes": [
                {"nodeId": "qe", "operatorType": "query_embed"},
                {"nodeId": "scope", "operatorType": "scope_resolve"},
                {"nodeId": "fts", "operatorType": "fts", "params": {"topK": 20}},
                {"nodeId": "dv", "operatorType": "dense_vector", "params": {"topK": 20}},
                {"nodeId": "fuse", "operatorType": "rrf", "params": {"k": 60}},
                {"nodeId": "rr", "operatorType": "model_rerank", "params": {"topN": 50, "topK": 10}},
                {"nodeId": "hyd", "operatorType": "evidence_hydrate",
                 "params": {"mode": "auto", "topN": 50}},
                {"nodeId": "asm", "operatorType": "assemble",
                 "params": {"maxEvidence": 10, "maxOutputTokens": 3000}}
              ],
              "edges": [
                {"fromNode": "qe", "fromSlot": "queryEmbedding", "toNode": "dv", "toSlot": "queryEmbedding"},
                {"fromNode": "scope", "fromSlot": "scope", "toNode": "qe", "toSlot": "scope"},
                {"fromNode": "scope", "fromSlot": "scope", "toNode": "dv", "toSlot": "scope"},
                {"fromNode": "scope", "fromSlot": "scope", "toNode": "fts", "toSlot": "scope"},
                {"fromNode": "dv", "fromSlot": "candidates", "toNode": "fuse", "toSlot": "candidates"},
                {"fromNode": "fts", "fromSlot": "candidates", "toNode": "fuse", "toSlot": "candidates"},
                {"fromNode": "fuse", "fromSlot": "candidates", "toNode": "rr", "toSlot": "candidates"},
                {"fromNode": "rr", "fromSlot": "candidates", "toNode": "hyd", "toSlot": "candidates"},
                {"fromNode": "scope", "fromSlot": "scope", "toNode": "hyd", "toSlot": "scope"},
                {"fromNode": "hyd", "fromSlot": "hydratedEvidence", "toNode": "asm", "toSlot": "hydratedEvidence"}
              ],
              "output": {"nodeId": "asm", "slot": "evidenceResponse"}
            }
            """;

    private final ParadigmMapper paradigmMapper;
    private final ParadigmVersionMapper versionMapper;
    private final ParadigmCompiler compiler;
    private final com.coremasterkb.serving.mapper.KnowledgeBaseMapper knowledgeBaseMapper;
    private final ObjectMapper mapper = new ObjectMapper();

    public ParadigmService(ParadigmMapper paradigmMapper,
                           ParadigmVersionMapper versionMapper,
                           ParadigmCompiler compiler,
                           com.coremasterkb.serving.mapper.KnowledgeBaseMapper knowledgeBaseMapper) {
        this.paradigmMapper = paradigmMapper;
        this.versionMapper = versionMapper;
        this.compiler = compiler;
        this.knowledgeBaseMapper = knowledgeBaseMapper;
    }

    // ---- CRUD ----------------------------------------------------------------------------

    /** Create a new draft paradigm. {@code graphJson} (the draft DAG) may be null/blank. */
    public ParadigmEntity create(String name, String description, String graphJson) {
        if (name == null || name.isBlank()) {
            throw new ParadigmBadRequestException("name_required");
        }
        if (paradigmMapper.selectByName(name) != null) {
            throw new ParadigmBadRequestException("paradigm_name_exists");
        }
        ParadigmEntity e = new ParadigmEntity();
        e.setId("pd-" + UUID.randomUUID().toString().substring(0, 8));
        e.setName(name);
        e.setDescription(description);
        e.setDraftGraphJson(blankToNull(graphJson));
        e.setCurrentVersion(0);
        e.setStatus("draft");
        paradigmMapper.insert(e);
        return getOrThrow(e.getId());
    }

    public ParadigmEntity getOrThrow(String id) {
        ParadigmEntity e = paradigmMapper.selectById(id);
        if (e == null) {
            throw new ParadigmNotFoundException("paradigm not found: " + id);
        }
        return e;
    }

    public List<ParadigmEntity> listAll() {
        return paradigmMapper.selectAll();
    }

    /** Published (currently active) paradigms only — those callable via {@code /{id}/search}. */
    public List<ParadigmEntity> listPublished() {
        return paradigmMapper.selectPublished();
    }

    /**
    // ---- 阶段 A：三层解析（library > official）与官方默认 seeding ----------------------
    // 批次6：域绑定退役（用户拍板"范式跨域通用"）——domain 层从解析链移除，
    // selectDefaultByDomain/applyBinding 随之删除；002 DDL 的 bound_domain/is_default
    // 列保留不动（存量零负担，代码不再读写）。

    /** 解析结果：{@code source} ∈ library|official；库级降级时附来源层。 */
    public record Resolution(ParadigmEntity paradigm, String source, String degradedFrom) {}

    /**
     * 库为中心的三层范式解析（18 号方案 §1.3）。kbIds 为空 = 未指定库，直接官方默认。
     *
     * <p>库级仅当目标库绑定<b>一致</b>（DISTINCT 恰好一个）且该范式可用时生效；绑定不一致
     * 或范式失效 → 降级官方默认（degradedFrom=library）。全空 → null（调用方明确报
     * "未配置检索范式"，不回落 legacy）。</p>
     */
    public Resolution resolveFor(String domain, List<String> kbIds) {
        boolean libraryEligible = kbIds != null && !kbIds.isEmpty();
        if (libraryEligible) {
            List<String> defaults = knowledgeBaseMapper.selectDefaultParadigmIds(domain, kbIds);
            if (defaults.size() == 1) {
                ParadigmEntity e = usable(paradigmMapper.selectById(defaults.get(0)));
                if (e != null) {
                    return new Resolution(e, "library", null);
                }
            }
            if (!defaults.isEmpty()) {
                // 有库级绑定但不可用（归档/未发布）或绑定不一致：降级官方默认，留痕。
                ParadigmEntity o = officialDefault();
                return (o == null) ? null : new Resolution(o, "official", "library");
            }
        }
        ParadigmEntity fallback = officialDefault();
        return (fallback == null) ? null : new Resolution(fallback, "official", null);
    }

    private ParadigmEntity officialDefault() {
        return usable(paradigmMapper.selectById(OFFICIAL_DEFAULT_ID));
    }

    /** 可用 = 已发布（status=active）且有当前版本；不可用（含 null 入参）返回 null。 */
    private ParadigmEntity usable(ParadigmEntity e) {
        if (e == null) return null;
        if (!"active".equals(e.getStatus())) return null;
        if (e.getCurrentVersion() <= 0) return null;
        return e;
    }

    /**
     * 批次8 R8 恢复 seeding（25 号 §9）：两套官方检索预置——
     * {@link #OFFICIAL_LEXICAL_ID system-lexical-retrieval}（关键词，不要求 embedding）与
     * {@link #OFFICIAL_DEFAULT_ID system-hybrid-retrieval}（标准混合，官方默认/resolve 兜底）。
     *
     * <p>幂等：缺失则建+发布；已存在但从未发布（version=0）则补发布；用户显式归档
     * （archived）的官方范式<b>不复活</b>——那是运营决定。固定 id；R0 退役的旧 id
     * {@code system-official-default} 不复活。Best-effort：控制库不可用/名称被占 →
     * log warn，下次启动重试。</p>
     */
    @Transactional
    public void ensureOfficialParadigms() {
        ensureSeeded(OFFICIAL_LEXICAL_ID, "系统关键词证据检索",
                "官方预置（§9.1）：scope_resolve → fts → evidence_hydrate → assemble。"
                        + "不要求 embedding 服务；配套轻量关键词资产。检索范围留空"
                        + "（检索时按开放库/请求注入）。",
                OFFICIAL_LEXICAL_GRAPH);
        ensureSeeded(OFFICIAL_DEFAULT_ID, "系统标准混合证据检索（官方默认）",
                "官方默认预置（§9.2）：scope_resolve + query_embed → fts‖dense → rrf → "
                        + "model_rerank → evidence_hydrate → assemble。rerank 失败自动保序降级；"
                        + "配套标准混合资产。检索范围留空（检索时按开放库/请求注入）。",
                OFFICIAL_HYBRID_GRAPH);
    }

    private void ensureSeeded(String id, String name, String description, String graph) {
        ParadigmEntity existing = paradigmMapper.selectById(id);
        if (existing == null) {
            ParadigmEntity e = new ParadigmEntity();
            e.setId(id);
            e.setName(name);
            e.setDescription(description);
            e.setDraftGraphJson(graph);
            e.setCurrentVersion(0);
            e.setStatus("draft");
            paradigmMapper.insert(e);
            publish(id, "system-seeder");
            log.info("[paradigm] official paradigm seeded: {}", id);
        } else if (existing.getCurrentVersion() == 0) {
            if (existing.getDraftGraphJson() == null || existing.getDraftGraphJson().isBlank()) {
                paradigmMapper.updateDraft(id, graph);
            }
            publish(id, "system-seeder");
            log.info("[paradigm] official paradigm published: {}", id);
        } else if ("active".equals(existing.getStatus()) && drifted(existing, graph)) {
            // 29号 R07：active 官方范式随源码定义演进——草稿对齐并发布新版本
            // （与挖掘侧 system-preset-refresh 同款语义；运行中请求继续用旧
            // 冻结版本，新请求解析到新 current version）。用户显式归档的
            // 官方范式不复活。系统预置不被用户编辑是本刷新的前提。
            paradigmMapper.updateDraft(id, graph);
            try {
                publish(id, "system-preset-refresh");
                log.info("[paradigm] official paradigm drift-refreshed: {}", id);
            } catch (Exception race) {
                // 并发实例已刷新（版本唯一键冲突）——吸收，另一实例已发布
                log.warn("[paradigm] drift-refresh race on {} absorbed: {}", id,
                        race.getMessage());
            }
        }
        // archived（用户显式下线）不动——幂等且尊重运营决定。
    }

    /** 草稿与官方图是否语义漂移（规范化 JSON 比较，忽略空白）。 */
    private boolean drifted(ParadigmEntity existing, String officialGraph) {
        String draft = existing.getDraftGraphJson();
        if (draft == null || draft.isBlank()) {
            return true;
        }
        try {
            return !mapper.readTree(draft).equals(mapper.readTree(officialGraph));
        } catch (Exception e) {
            return true; // 存量草稿不可解析——按漂移处理重写
        }
    }

    /** Replace the editable draft graph. */
    public ParadigmEntity updateDraft(String id, String graphJson) {
        getOrThrow(id);
        paradigmMapper.updateDraft(id, blankToNull(graphJson));
        return getOrThrow(id);
    }

    // ---- versions / publish --------------------------------------------------------------

    public List<ParadigmVersionEntity> listVersions(String id) {
        getOrThrow(id);
        return versionMapper.selectVersionsByParadigm(id);
    }

    public ParadigmVersionEntity getVersionOrThrow(String id, int version) {
        getOrThrow(id);
        ParadigmVersionEntity v = versionMapper.selectByParadigmAndVersion(id, version);
        if (v == null) {
            throw new ParadigmNotFoundException("paradigm " + id + " has no version " + version);
        }
        return v;
    }

    /** Compile-validate the current draft, then snapshot it as a new immutable version and activate it. */
    @Transactional
    public ParadigmVersionEntity publish(String id, String createdBy) {
        ParadigmEntity p = getOrThrow(id);
        String draft = p.getDraftGraphJson();
        if (draft == null || draft.isBlank()) {
            throw new OperatorException("nothing to publish: draft graph is empty");
        }
        JsonNode graph = parse(draft);
        compiler.compile(graph); // throws ParadigmCompileException on invalid graph

        // Next version = max existing + 1 (NOT current_version + 1): after a rollback to a
        // non-latest version, current_version < max, so current+1 would collide with an
        // existing version and violate uq_operator_paradigm_version.
        Integer maxVersion = versionMapper.selectMaxVersion(id);
        int newVersion = (maxVersion == null ? 0 : maxVersion) + 1;
        ParadigmVersionEntity v = new ParadigmVersionEntity();
        v.setId("pdv-" + UUID.randomUUID().toString().substring(0, 8));
        v.setParadigmId(id);
        v.setVersion(newVersion);
        v.setGraphJson(draft);
        v.setSchemaVersion(extractSchemaVersion(graph));
        v.setCreatedBy(createdBy);
        versionMapper.insert(v);

        paradigmMapper.updatePublish(id, newVersion, "active");
        log.info("[paradigm] published {} version {}", id, newVersion);
        return v;
    }

    /** Point {@code current_version} back at an existing historical version (versions are unchanged). */
    public ParadigmEntity rollback(String id, int version) {
        getVersionOrThrow(id, version);
        paradigmMapper.updatePublish(id, version, "active");
        log.info("[paradigm] rolled back {} to version {}", id, version);
        return getOrThrow(id);
    }

    /** Archive a paradigm (status → archived); its versions and current_version are kept intact. */
    public ParadigmEntity archive(String id) {
        ParadigmEntity p = getOrThrow(id);
        paradigmMapper.updatePublish(id, p.getCurrentVersion(), "archived");
        log.info("[paradigm] archived {}", id);
        return getOrThrow(id);
    }

    /**
     * Permanently delete a paradigm and all its versions. Only allowed once the paradigm is
     * archived — a two-step guard (archive → delete) against accidental loss of a live paradigm.
     */
    @Transactional
    public void delete(String id) {
        ParadigmEntity p = getOrThrow(id);
        if (!"archived".equals(p.getStatus())) {
            throw new ParadigmBadRequestException("paradigm_not_archived");
        }
        versionMapper.deleteByParadigm(id);
        paradigmMapper.deleteById(id);
        log.info("[paradigm] deleted {}", id);
    }

    // ---- resolution for execution --------------------------------------------------------

    /**
     * Resolve the graph JSON to execute for {@code (id, version?)}: a specific version if given,
     * otherwise the paradigm's {@code current_version}. Throws if unpublished or version missing.
     */
    public JsonNode resolveExecutableGraph(String id, Integer version) {
        return parse(resolveExecutableVersion(id, version).getGraphJson());
    }

    /**
     * As {@link #resolveExecutableGraph} but returns the whole version row, so a caller that also
     * needs the <em>effective</em> version number (e.g. to attribute a query log when the request
     * said "current") gets it from this same read instead of querying again.
     */
    public ParadigmVersionEntity resolveExecutableVersion(String id, Integer version) {
        ParadigmEntity p = getOrThrow(id);
        int v = (version != null) ? version : p.getCurrentVersion();
        if (v <= 0) {
            throw new ParadigmNotFoundException("paradigm " + id + " has no published version");
        }
        ParadigmVersionEntity ve = versionMapper.selectByParadigmAndVersion(id, v);
        if (ve == null) {
            throw new ParadigmNotFoundException("paradigm " + id + " has no version " + v);
        }
        return ve;
    }

    /** Compile-validate a graph (no execution), returning the structured error list. */
    public List<com.coremasterkb.serving.operator.engine.CompileError> validateDraft(JsonNode graph) {
        return compiler.validate(graph);
    }

    /** Parse the editable draft graph (for validate / dry-run). */
    public JsonNode resolveDraftGraph(String id) {
        ParadigmEntity p = getOrThrow(id);
        if (p.getDraftGraphJson() == null || p.getDraftGraphJson().isBlank()) {
            throw new OperatorException("paradigm " + id + " has no draft graph");
        }
        return parse(p.getDraftGraphJson());
    }

    // ---- helpers -------------------------------------------------------------------------

    private JsonNode parse(String json) {
        try {
            return mapper.readTree(json);
        } catch (Exception e) {
            throw new OperatorException("stored graph_json is not valid JSON: " + e.getMessage());
        }
    }

    private static String extractSchemaVersion(JsonNode graph) {
        JsonNode sv = graph.get("schemaVersion");
        return (sv != null && sv.isTextual() && !sv.asText().isBlank()) ? sv.asText() : "1.0";
    }

    private static String blankToNull(String s) {
        return (s == null || s.isBlank()) ? null : s;
    }
}
