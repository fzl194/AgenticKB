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

    public static QuerySpec parseSpec(JsonNode q) {
        if (q == null || q.isNull()) {
            return new QuerySpec(null, null, null, null, null, null);
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
        JsonNode whereNode = q.get("where");
        if (whereNode != null && whereNode.isArray()) {
            for (JsonNode w : whereNode) {
                where.add(new WhereClause(text(w, "field"), text(w, "op"), w.get("value")));
            }
        }
        List<OrderClause> orderBy = new ArrayList<>();
        JsonNode orderNode = q.get("order_by");
        if (orderNode != null && orderNode.isArray()) {
            for (JsonNode o : orderNode) {
                orderBy.add(new OrderClause(text(o, "field"), text(o, "direction")));
            }
        }
        Aggregate aggregate = null;
        JsonNode agg = q.get("aggregate");
        if (agg != null && agg.isObject() && agg.hasNonNull("op")) {
            aggregate = new Aggregate(text(agg, "op"), text(agg, "field"));
        }
        return new QuerySpec(select, where, orderBy, intOrNull(q.get("limit")),
                text(q, "cursor"), aggregate);
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
        if (v == null || !v.isArray()) {
            return null;
        }
        List<String> out = new ArrayList<>();
        for (JsonNode item : v) {
            out.add(item.asText());
        }
        return out;
    }
}
