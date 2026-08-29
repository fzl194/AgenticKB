package com.coremasterkb.serving.infrastructure;

import java.util.*;

/**
 * Serving LLM template definitions, ported from Python SERVING_TEMPLATES.
 *
 * <p>Each template is a {@code Map<String, Object>} with keys:
 * template_key, template_version, purpose, system_prompt (with {output_schema} and {example}
 * placeholders), user_prompt_template, output_schema_json, _example_json.
 */
public final class ServingTemplates {

    private ServingTemplates() {}

    // 批次8 R0：serving-query-understanding / serving-hyde-expansion /
    // serving-multi-query-expansion 模板随 query_understanding / hyde / multi_query 退役删除
    //（25号 §4/§11.1），仅保留 serving-reranker（model_rerank 在用）。

    // ---- Reranker output schema (JSON string) ----
    private static final String RERANKER_SCHEMA = """
            {
              "type": "object",
              "properties": {
                "ranking": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "properties": {
                      "index": {"type":"integer"},
                      "score": {"type":"number","minimum":0.0,"maximum":1.0}
                    },
                    "required": ["index","score"]
                  }
                }
              },
              "required": ["ranking"]
            }""";

    // ---- Reranker example (JSON string) ----
    private static final String RERANKER_EXAMPLE = """
            {
              "ranking": [
                {"index": 0, "score": 0.95},
                {"index": 2, "score": 0.78},
                {"index": 1, "score": 0.45}
              ]
            }""";

    // ---- Template: serving-reranker ----
    private static final Map<String, Object> RERANKER = Map.ofEntries(
            Map.entry("template_key", "serving-reranker"),
            Map.entry("template_version", "2"),
            Map.entry("purpose", "对检索结果进行 LLM 相关性重排序"),
            Map.entry("system_prompt",
                    "你是一个文档相关性评估系统。你的任务是根据查询对候选文档进行相关性排序。\n"
                    + "对于每个候选文档，给出一个0-1之间的相关性分数。\n"
                    + "按相关性从高到低排列。\n\n"
                    + "## JSON Schema 结构定义\n"
                    + "{output_schema}\n\n"
                    + "## 输出要求\n"
                    + "输出严格的 JSON 格式，不要添加任何其他文本。下面是一个输出示例（仅供参考格式，请根据实际内容生成）：\n"
                    + "{example}"),
            Map.entry("user_prompt_template",
                    "查询：$query\n\n"
                    + "候选文档：\n$candidates\n\n"
                    + "请对以上 $count 个候选文档按相关性排序。"),
            Map.entry("output_schema_json", RERANKER_SCHEMA),
            Map.entry("_example_json", RERANKER_EXAMPLE)
    );

    public static final List<Map<String, Object>> ALL = List.of(RERANKER);
}
