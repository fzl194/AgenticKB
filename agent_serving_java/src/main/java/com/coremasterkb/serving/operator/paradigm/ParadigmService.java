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

    /** 官方默认检索范式固定 id（resolve 第④层兜底；seeding 见 OfficialParadigmSeeder）。 */
    public static final String OFFICIAL_DEFAULT_ID = "system-official-default";

    /**
     * 官方默认图：query_embed ‖ fts → weighted_rrf → model_rerank → assemble。
     * 最简可发表 servable 图（assemble 终点产 ContextPack）。检索效果对齐 legacy 固定
     * 链路要等 C 阶段四能力算子化（树导航/语义缓存/多查询扩展/级联重排），本图只保证
     * "无任何绑定时检索可达"。scope 留空 = 运行时按请求注入库范围（菜谱+运行时范围）。
     */
    static final String OFFICIAL_DEFAULT_GRAPH = """
            {
              "schemaVersion": "1.0",
              "nodes": [
                {"nodeId": "qe", "operatorType": "query_embed"},
                {"nodeId": "scope", "operatorType": "scope_resolve"},
                {"nodeId": "dv", "operatorType": "dense_vector", "params": {"textKind": "both", "topK": 20}},
                {"nodeId": "fts", "operatorType": "fts", "params": {"topK": 20}},
                {"nodeId": "fuse", "operatorType": "weighted_rrf", "params": {"k": 60}},
                {"nodeId": "rr", "operatorType": "model_rerank", "params": {"topK": 10}},
                {"nodeId": "asm", "operatorType": "assemble", "params": {"maxItems": 10, "relationExpansion": true}}
              ],
              "edges": [
                {"fromNode": "qe", "fromSlot": "queryEmbedding", "toNode": "dv", "toSlot": "queryEmbedding"},
                {"fromNode": "scope", "fromSlot": "scope", "toNode": "dv", "toSlot": "scope"},
                {"fromNode": "scope", "fromSlot": "scope", "toNode": "fts", "toSlot": "scope"},
                {"fromNode": "dv", "fromSlot": "candidates", "toNode": "fuse", "toSlot": "candidates"},
                {"fromNode": "fts", "fromSlot": "candidates", "toNode": "fuse", "toSlot": "candidates"},
                {"fromNode": "fuse", "fromSlot": "candidates", "toNode": "rr", "toSlot": "candidates"},
                {"fromNode": "rr", "fromSlot": "candidates", "toNode": "asm", "toSlot": "candidates"},
                {"fromNode": "scope", "fromSlot": "scope", "toNode": "asm", "toSlot": "scope"}
              ],
              "output": {"nodeId": "asm", "slot": "contextPack"}
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
     * Seed the official default paradigm（固定 id，幂等）。缺失则建+发布；已存在但从未
     * 发布（version=0）则补发布；用户归档（archived）的官方范式不复活——那是显式决定。
     * Best-effort：控制库不可用/名称被占 → log warn，下次启动重试。
     */
    @Transactional
    public void ensureOfficialDefault() {
        ParadigmEntity existing = paradigmMapper.selectById(OFFICIAL_DEFAULT_ID);
        if (existing == null) {
            ParadigmEntity e = new ParadigmEntity();
            e.setId(OFFICIAL_DEFAULT_ID);
            e.setName("系统官方默认检索范式");
            e.setDescription("无任何绑定时的兜底检索管线：向量+关键词混合召回、加权融合、"
                    + "模型重排、组装上下文包。检索范围默认留空（检索时按开放库注入）。");
            e.setDraftGraphJson(OFFICIAL_DEFAULT_GRAPH);
            e.setCurrentVersion(0);
            e.setStatus("draft");
            paradigmMapper.insert(e);
            publish(OFFICIAL_DEFAULT_ID, "system-seeder");
            log.info("[paradigm] official default seeded: {}", OFFICIAL_DEFAULT_ID);
        } else if (existing.getCurrentVersion() == 0) {
            if (existing.getDraftGraphJson() == null || existing.getDraftGraphJson().isBlank()) {
                paradigmMapper.updateDraft(OFFICIAL_DEFAULT_ID, OFFICIAL_DEFAULT_GRAPH);
            }
            publish(OFFICIAL_DEFAULT_ID, "system-seeder");
            log.info("[paradigm] official default published: {}", OFFICIAL_DEFAULT_ID);
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
