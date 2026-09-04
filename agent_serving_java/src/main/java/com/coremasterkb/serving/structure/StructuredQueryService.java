package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.result.TableAssetRow;
import com.coremasterkb.serving.mapper.result.TableCellRow;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import com.coremasterkb.serving.util.JsonUtils;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * {@code structured_query}——确定性后端能力（批次8 R7，25 号 §6.11）。
 *
 * <p>由 {@code query_structured_asset} MCP 工具调用，不进模糊搜索主图。schema-bound DSL：
 * select/where(eq/ne/lt/lte/gt/gte/in/contains/is_null)/order_by/limit+cursor/
 * aggregate(count/sum/min/max/avg)。字段名 = schema display name（columns_json /
 * asset_table_cells.column_name），<b>白名单校验后仅以参数绑定进入 SQL</b>
 * （{@code cells->>#{field}}）；操作符/方向/聚合函数是 mapper 固定分支——禁止任意 SQL、
 * 内部表名、表达式执行、join、NL-to-SQL。</p>
 *
 * <p>列能力：数值列判定 = cells 值扫描全数值（与 mining readiness.column_aggregability 同
 * 口径）；扫描截断（超 {@link #TYPING_SCAN_CAP} cells）即保守降级为文本列（聚合拒绝，
 * typed error {@code unsupported_operation}）。表格 readiness 不足 →
 * {@code structured_query_unavailable}（Agent 退回 search/get_evidence）。</p>
 */
@Service
public class StructuredQueryService {

    static final int MAX_LIMIT = 200;
    static final int DEFAULT_LIMIT = 50;
    /** 29号 R0 复审护栏：where 子句数与 in 数组长度上限。 */
    static final int MAX_WHERE_CLAUSES = 32;
    static final int MAX_IN_VALUES = 200;
    /** 列能力扫描上限：超出即保守按文本列处理（不放大内存/判定成本）。 */
    static final int TYPING_SCAN_CAP = 2000;
    /** 无过滤聚合的行数护栏：超过即 result_too_large（提示加 filter）。 */
    static final int UNFILTERED_AGGREGATE_ROW_GUARD = 50_000;

    private static final Set<String> FILTER_OPS = Set.of(
            "eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "is_null");
    private static final Set<String> AGG_OPS = Set.of("count", "sum", "min", "max", "avg");
    private static final Set<String> NUMERIC_ONLY_OPS = Set.of("lt", "lte", "gt", "gte");

    private final StructureRefService refService;
    private final StructureToolMapper toolMapper;

    public StructuredQueryService(StructureRefService refService, StructureToolMapper toolMapper) {
        this.refService = refService;
        this.toolMapper = toolMapper;
    }

    // ------------------------------------------------------------------ DTO

    public record WhereClause(String field, String op, JsonNode value) {}

    public record OrderClause(String field, String direction) {}

    public record Aggregate(String op, String field) {}

    public record QuerySpec(
            List<String> select,
            List<WhereClause> where,
            List<OrderClause> order_by,
            Integer limit,
            String cursor,
            Aggregate aggregate) {}

    /** 字段能力（inspect 同源；字段名即 display name）。 */
    public record FieldSchema(
            String name,
            String value_type,
            boolean sortable,
            boolean can_aggregate,
            List<String> operations) {}

    public record QueryResult(
            String asset_ref,
            String table_name,
            List<FieldSchema> columns,
            List<Map<String, Object>> rows,
            String cursor,
            boolean has_more,
            AggregateResult aggregate) {}

    public record AggregateResult(String op, String field, Double value, long row_count) {}

    /** 表格 schema（columns + 能力），inspect_knowledge 复用。 */
    public record TableSchema(String asset_ref, String readiness, List<FieldSchema> columns,
                              Integer row_count) {}

    // ------------------------------------------------------------------ entry

    public QueryResult query(String assetRef, QuerySpec spec,
                             String domain, List<String> kbIds, String username) {
        EvidenceRefResolver.ResolvedRef resolved = refService.resolve(assetRef, domain, kbIds, username);
        TableAssetRow asset =
                toolMapper.selectTableAssetByAssetRef(resolved.snapshotId(), resolved.internalRef());
        if (asset == null) {
            throw StructureToolException.unsupportedOperation(
                    "目标不是可查询的结构化资产（structured_query 仅支持 table 类 asset ref）",
                    Map.of("hint", "先 inspect_knowledge 确认 can_query_structured"));
        }
        if (!"ready".equals(asset.getReadiness())) {
            throw StructureToolException.structuredQueryUnavailable(
                    "该表格未通过结构化就绪（缺表头或质量不足），无法 structured query");
        }

        TableSchema schema = schemaOf(resolved.snapshotId(), asset);
        List<FieldSchema> fields = schema.columns();
        if (fields.isEmpty()) {
            throw StructureToolException.structuredQueryUnavailable(
                    "该表格没有可用列（columns 为空），无法 structured query");
        }

        List<String> columnNames = fields.stream().map(FieldSchema::name).toList();
        Map<String, FieldSchema> byName = new LinkedHashMap<>();
        fields.forEach(f -> byName.put(f.name(), f));

        List<StructureToolMapper.Criterion> criteria = new ArrayList<>();
        if (spec.where() != null) {
            // 29号 R0 复审：where 数量上限（防超大 OR/AND 构造打爆 SQL 规划）。
            if (spec.where().size() > MAX_WHERE_CLAUSES) {
                throw StructureToolException.resultTooLarge(
                        "where 子句超上限 " + MAX_WHERE_CLAUSES);
            }
            for (WhereClause w : spec.where()) {
                criteria.add(toCriterion(w, byName, columnNames));
            }
        }

        // 聚合模式
        if (spec.aggregate() != null && spec.aggregate().op() != null) {
            return aggregateMode(assetRef, asset, schema, spec.aggregate(), criteria, byName);
        }

        // 行模式
        List<String> select = validatedSelect(spec.select(), columnNames);
        String orderField = null;
        String orderDir = "asc";
        boolean numericOrder = false;
        if (spec.order_by() != null && !spec.order_by().isEmpty()) {
            if (spec.order_by().size() > 1) {
                throw StructureToolException.unsupportedOperation(
                        "order_by 仅支持单字段稳定排序", Map.of());
            }
            OrderClause o = spec.order_by().get(0);
            FieldSchema f = byName.get(o.field());
            if (f == null) {
                throw StructureToolException.unknownField(o.field(), columnNames);
            }
            if (o.direction() != null && !o.direction().equalsIgnoreCase("asc")
                    && !o.direction().equalsIgnoreCase("desc")) {
                throw StructureToolException.typeMismatch(o.field() + ".direction", "asc|desc");
            }
            orderField = f.name();
            orderDir = "desc".equalsIgnoreCase(o.direction()) ? "desc" : "asc";
            numericOrder = "number".equals(f.value_type());
        }

        int limit = spec.limit() == null ? DEFAULT_LIMIT : spec.limit();
        // 29号 R0 复审：limit<=0 会形成空页且 cursor 不前进（0）/负数下沉
        // 成 SQL 错误（<0）——按 typed 400 拒绝。
        if (limit <= 0) {
            throw StructureToolException.resultTooLarge(
                    "limit 必须为 1-" + MAX_LIMIT + " 的正整数（当前 " + limit + "）");
        }
        if (limit > MAX_LIMIT) {
            throw StructureToolException.resultTooLarge(
                    "limit 超上限 " + MAX_LIMIT + "——请用 cursor 分页或收窄过滤条件");
        }
        int offset = Cursors.decodeOffset(spec.cursor());

        List<StructureToolMapper.StructuredRow> rows = toolMapper.selectStructuredRows(
                resolved.snapshotId(), asset.getTableRef(), criteria,
                orderField, orderDir, numericOrder, limit + 1, offset);
        boolean hasMore = rows.size() > limit;
        List<Map<String, Object>> projected = new ArrayList<>(Math.min(rows.size(), limit));
        for (int i = 0; i < Math.min(rows.size(), limit); i++) {
            projected.add(projectRow(rows.get(i), select));
        }
        return new QueryResult(assetRef, asset.getTableRef(), fields, projected,
                hasMore ? Cursors.encodeOffset(offset + limit) : null, hasMore, null);
    }

    // ------------------------------------------------------------------ schema

    /** 列能力扫描 + schema 组装（inspect_knowledge 与 query 同源）。 */
    public TableSchema schemaOf(String snapshotId, TableAssetRow asset) {
        List<String> declared = parseColumnsJson(asset.getColumnsJson());
        List<TableCellRow> cells =
                toolMapper.selectCellsForTyping(snapshotId, asset.getTableRef(), TYPING_SCAN_CAP);

        // 列名清单：columns_json 优先，回退 header cells
        Set<String> names = new LinkedHashSet<>(declared);
        for (TableCellRow c : cells) {
            if (Boolean.TRUE.equals(c.getIsHeader()) && c.getColumnName() != null
                    && !c.getColumnName().isBlank()) {
                names.add(c.getColumnName());
            }
        }

        boolean truncated = cells.size() >= TYPING_SCAN_CAP;
        Map<String, Boolean> numericByColumn = new LinkedHashMap<>();
        int nonHeader = 0;
        for (TableCellRow c : cells) {
            if (Boolean.TRUE.equals(c.getIsHeader()) || c.getColumnName() == null) {
                continue;
            }
            nonHeader++;
            String col = c.getColumnName();
            boolean numericValue = isNumber(c.getValue());
            numericByColumn.merge(col, numericValue, (a, b) -> a && b);
        }

        List<FieldSchema> out = new ArrayList<>(names.size());
        for (String name : names) {
            Boolean numeric = numericByColumn.get(name);
            boolean isNumeric = !truncated && numeric != null && numeric && nonHeader > 0;
            out.add(new FieldSchema(
                    name,
                    isNumeric ? "number" : "text",
                    true,
                    isNumeric,
                    isNumeric
                            ? List.of("eq", "ne", "lt", "lte", "gt", "gte", "in", "is_null",
                                      "count", "sum", "min", "max", "avg")
                            : List.of("eq", "ne", "in", "contains", "is_null", "count")));
        }
        return new TableSchema(asset.getAssetRef(), asset.getReadiness(), out, asset.getRowCount());
    }

    // ------------------------------------------------------------------ internals

    private QueryResult aggregateMode(String assetRef, TableAssetRow asset, TableSchema schema,
                                      Aggregate aggregate,
                                      List<StructureToolMapper.Criterion> criteria,
                                      Map<String, FieldSchema> byName) {
        String op = aggregate.op();
        if (!AGG_OPS.contains(op)) {
            throw StructureToolException.unsupportedOperation(
                    "未知聚合操作: " + op + "；允许：" + AGG_OPS,
                    Map.of("allowed", new ArrayList<>(AGG_OPS.stream().sorted().toList())));
        }
        FieldSchema field = null;
        if (!"count".equals(op)) {
            if (aggregate.field() == null) {
                throw StructureToolException.typeMismatch("aggregate.field", "字段名（count 之外必填）");
            }
            field = byName.get(aggregate.field());
            if (field == null) {
                throw StructureToolException.unknownField(aggregate.field(),
                        schema.columns().stream().map(FieldSchema::name).toList());
            }
            if (!field.can_aggregate()) {
                throw StructureToolException.unsupportedOperation(
                        "字段 " + field.name() + " 是文本列（或值扫描不完整），仅允许 count 聚合",
                        Map.of("field", field.name(), "allowed", List.of("count")));
            }
        }
        if (criteria.isEmpty()) {
            Integer rowCount = asset.getRowCount();
            if (rowCount != null && rowCount > UNFILTERED_AGGREGATE_ROW_GUARD) {
                throw StructureToolException.resultTooLarge(
                        "无过滤条件的全表聚合超出护栏（" + rowCount + " 行）——请先加 where 过滤");
            }
        }

        StructureToolMapper.AggregateRow agg = toolMapper.aggregateStructuredRows(
                asset.getSnapshotId(), asset.getTableRef(), criteria, op,
                field == null ? null : field.name());
        long count = toolMapper.countStructuredRows(asset.getSnapshotId(), asset.getTableRef(),
                criteria);
        return new QueryResult(assetRef, asset.getTableRef(), schema.columns(), null, null, false,
                new AggregateResult(op, field == null ? null : field.name(),
                        agg == null ? null : agg.value(), count));
    }

    private StructureToolMapper.Criterion toCriterion(WhereClause w,
                                                      Map<String, FieldSchema> byName,
                                                      List<String> columnNames) {
        if (w.field() == null || w.op() == null) {
            throw StructureToolException.unsupportedOperation(
                    "where 子句缺少 field/op", Map.of());
        }
        FieldSchema f = byName.get(w.field());
        if (f == null) {
            throw StructureToolException.unknownField(w.field(), columnNames);
        }
        String op = w.op();
        if (!FILTER_OPS.contains(op)) {
            throw StructureToolException.unsupportedOperation(
                    "未知过滤操作: " + op + "；允许：" + FILTER_OPS,
                    Map.of("field", w.field(), "allowed", f.operations()));
        }
        boolean numeric = "number".equals(f.value_type());
        if (NUMERIC_ONLY_OPS.contains(op) && !numeric) {
            throw StructureToolException.unsupportedOperation(
                    "文本列不支持 " + op + "（仅数值列）", Map.of("field", w.field(),
                            "allowed", f.operations()));
        }
        if ("contains".equals(op) && numeric) {
            throw StructureToolException.unsupportedOperation(
                    "数值列不支持 contains（文本匹配）", Map.of("field", w.field(),
                            "allowed", f.operations()));
        }
        if ("is_null".equals(op)) {
            return new StructureToolMapper.Criterion(w.field(), "is_null", null, null, numeric);
        }
        if ("in".equals(op)) {
            if (w.value() == null || !w.value().isArray() || w.value().isEmpty()) {
                throw StructureToolException.typeMismatch(w.field() + " in", "非空数组");
            }
            if (w.value().size() > MAX_IN_VALUES) {
                throw StructureToolException.resultTooLarge(
                        "in 数组超上限 " + MAX_IN_VALUES + "（field=" + w.field() + "）");
            }
            List<String> values = new ArrayList<>();
            for (JsonNode item : w.value()) {
                values.add(coerce(w.field(), item, numeric));
            }
            return new StructureToolMapper.Criterion(w.field(), "in", null, values, numeric);
        }
        if (w.value() == null || w.value().isNull()) {
            throw StructureToolException.typeMismatch(w.field(),
                    numeric ? "数值" : "字符串");
        }
        String coerced = coerce(w.field(), w.value(), numeric);
        return new StructureToolMapper.Criterion(w.field(), op,
                numeric ? Double.parseDouble(coerced) : coerced, null, numeric);
    }

    /** 数值列：number 或可解析数字串 → 规范数字串；文本列：必须字符串。 */
    private static String coerce(String field, JsonNode value, boolean numeric) {
        if (numeric) {
            if (value.isNumber()) {
                return value.decimalValue().toPlainString();
            }
            if (value.isTextual() && isNumber(value.asText())) {
                return value.asText().replace(",", "").trim();
            }
            throw StructureToolException.typeMismatch(field, "number");
        }
        if (value.isTextual()) {
            return value.asText();
        }
        throw StructureToolException.typeMismatch(field, "string");
    }

    private static List<String> validatedSelect(List<String> select, List<String> columnNames) {
        if (select == null || select.isEmpty()) {
            return columnNames; // 缺省 = 全部列
        }
        List<String> out = new ArrayList<>();
        for (String s : select) {
            if (!columnNames.contains(s)) {
                throw StructureToolException.unknownField(s, columnNames);
            }
            if (!out.contains(s)) {
                out.add(s);
            }
        }
        return out;
    }

    private static Map<String, Object> projectRow(StructureToolMapper.StructuredRow row,
                                                  List<String> select) {
        Map<String, Object> cells = row.cellsJson() == null
                ? Map.of() : JsonUtils.safeJsonParse(row.cellsJson());
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("_row", row.rowIndex());
        for (String col : select) {
            Object v = cells.get(col);
            out.put(col, v == null ? null : String.valueOf(v));
        }
        return out;
    }

    private static List<String> parseColumnsJson(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            List<?> raw = JsonUtils.mapper().readValue(json, List.class);
            return raw.stream().map(String::valueOf)
                    .filter(s -> !s.isBlank()).toList();
        } catch (Exception e) {
            return List.of();
        }
    }

    /** 与 mining readiness._is_number 同口径：去千分位后可解析 float。 */
    static boolean isNumber(String value) {
        if (value == null || value.isBlank()) {
            return false;
        }
        try {
            Double.parseDouble(value.replace(",", ""));
            return true;
        } catch (NumberFormatException e) {
            return false;
        }
    }
}
