package com.coremasterkb.serving.structure;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

import com.coremasterkb.serving.structure.StructuredQueryService.Aggregate;
import com.coremasterkb.serving.structure.StructuredQueryService.OrderClause;
import com.coremasterkb.serving.structure.StructuredQueryService.QuerySpec;
import com.coremasterkb.serving.structure.StructuredQueryService.WhereClause;

/**
 * 结构化查询 DSL 解析（29 号 2.9 白名单键契约）。
 *
 * <p>A3（39 号 §3.3）从 internal controller 抽出为公共工具——网页 REST
 *（{@code /api/v1/structure/{ref}/query}）与 MCP internal
 *（{@code /api/internal/structured-query}）必须同一解析与校验，不允许第二套规则。</p>
 */
public final class StructureQueryDsl {

    private StructureQueryDsl() {
    }

    /**
     * P2-14：形状严格校验——非 object 的 query、非数组的 select/where/order_by、
     * 非 object 的 item、缺 field/op 的 where 项一律
     * {@code IllegalArgumentException}（controller 映射 typed 400）。
     * 缺陷回顾：错形状被静默当缺省值（筛选请求可能变成全表查询）。
     */
    public static QuerySpec parseSpec(JsonNode q) {
        if (q == null || q.isNull()) {
            return new QuerySpec(null, null, null, null, null, null);
        }
        if (!q.isObject()) {
            throw new IllegalArgumentException(
                    "unsupported_query_shape: query 必须是 object（got "
                            + q.getNodeType() + "）");
        }
        Set<String> fieldNames = new HashSet<>();
        q.fieldNames().forEachRemaining(fieldNames::add);
        fieldNames.removeAll(Set.of(
                "select", "where", "order_by", "limit", "cursor", "aggregate"));
        if (!fieldNames.isEmpty()) {
            throw new IllegalArgumentException(
                    "unsupported_query_key:" + String.join(",", fieldNames));
        }
        List<String> select = stringList(q, "select");
        List<WhereClause> where = new ArrayList<>();
        JsonNode whereNode = requireArray(q, "where");
        if (whereNode != null) {
            for (JsonNode w : whereNode) {
                if (w == null || !w.isObject()) {
                    throw new IllegalArgumentException(
                            "unsupported_query_shape: where 项必须是 object");
                }
                String field = text(w, "field");
                if (field == null) {
                    throw new IllegalArgumentException(
                            "unsupported_query_shape: where 项缺 field");
                }
                where.add(new WhereClause(field, text(w, "op"), w.get("value")));
            }
        }
        List<OrderClause> orderBy = new ArrayList<>();
        JsonNode orderNode = requireArray(q, "order_by");
        if (orderNode != null) {
            for (JsonNode o : orderNode) {
                if (o == null || !o.isObject()) {
                    throw new IllegalArgumentException(
                            "unsupported_query_shape: order_by 项必须是 object");
                }
                String field = text(o, "field");
                if (field == null) {
                    throw new IllegalArgumentException(
                            "unsupported_query_shape: order_by 项缺 field");
                }
                orderBy.add(new OrderClause(field, text(o, "direction")));
            }
        }
        Aggregate aggregate = null;
        JsonNode agg = q.get("aggregate");
        if (agg != null && !agg.isNull()) {
            if (!agg.isObject()) {
                throw new IllegalArgumentException(
                        "unsupported_query_shape: aggregate 必须是 object");
            }
            if (agg.hasNonNull("op")) {
                aggregate = new Aggregate(text(agg, "op"), text(agg, "field"));
            }
        }
        JsonNode limitNode = q.get("limit");
        if (limitNode != null && !limitNode.isNull() && !limitNode.isNumber()) {
            throw new IllegalArgumentException(
                    "unsupported_query_shape: limit 必须是数字");
        }
        return new QuerySpec(select, where, orderBy, intOrNull(limitNode),
                text(q, "cursor"), aggregate);
    }

    /** P2-14：子句存在时必须是数组——不静默放宽（null=未出现，合法）。 */
    private static JsonNode requireArray(JsonNode q, String field) {
        JsonNode v = q.get(field);
        if (v == null || v.isNull()) {
            return null;
        }
        if (!v.isArray()) {
            throw new IllegalArgumentException(
                    "unsupported_query_shape: " + field + " 必须是数组");
        }
        return v;
    }

    static String text(JsonNode body, String field) {
        JsonNode v = body.get(field);
        return v == null || v.isNull() ? null : v.asText();
    }

    static Integer intOrNull(JsonNode v) {
        return v == null || v.isNull() ? null : v.asInt();
    }

    static List<String> stringList(JsonNode body, String field) {
        JsonNode v = body.get(field);
        if (v == null || v.isNull()) {
            return null;
        }
        if (!v.isArray()) {
            // P2-14：非数组不静默当缺省（筛选退化为全查的隐患路径）
            throw new IllegalArgumentException(
                    "unsupported_query_shape: " + field + " 必须是数组");
        }
        List<String> out = new ArrayList<>();
        for (JsonNode item : v) {
            out.add(item.asText());
        }
        return out;
    }
}
