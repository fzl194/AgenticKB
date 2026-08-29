package com.coremasterkb.serving.operator.operators.output;

/**
 * v2 canonical target_ref 的确定性解析（批次8 R5）。格式真相源：mining
 * {@code retrieval_projection/projector.py} / {@code summary.py}：
 *
 * <ul>
 *   <li>{@code {doc}#seg:{segment_index}}</li>
 *   <li>{@code {doc}#section:{title1/title2/...}}</li>
 *   <li>{@code {doc}#document}</li>
 *   <li>{@code {doc}#table:{table_ref}}</li>
 *   <li>{@code {doc}#table_row:{table_ref}:{row_index}}</li>
 * </ul>
 *
 * <p>纯函数、容错：不合式输入返回 {@code null} 载荷，调用方按候选留痕跳过，绝不抛出。</p>
 */
public final class TargetRefFormat {

    static final String SEGMENT = "segment";
    static final String SECTION = "section";
    static final String DOCUMENT = "document";
    static final String TABLE = "table";
    static final String TABLE_ROW = "table_row";

    private TargetRefFormat() {}

    /** 解析结果：targetType ∈ SEGMENT/SECTION/DOCUMENT/TABLE/TABLE_ROW；payload 为类型化余部。 */
    public record Parsed(String documentRef, String targetType, String payload) {}

    public static Parsed parse(String targetRef) {
        if (targetRef == null || targetRef.isEmpty()) {
            return null;
        }
        int hash = targetRef.indexOf('#');
        if (hash <= 0 || hash == targetRef.length() - 1) {
            return null;
        }
        String documentRef = targetRef.substring(0, hash);
        String rest = targetRef.substring(hash + 1);
        if (rest.startsWith("seg:")) {
            return new Parsed(documentRef, SEGMENT, rest.substring(4));
        }
        if (rest.startsWith("section:")) {
            return new Parsed(documentRef, SECTION, rest.substring(8));
        }
        if ("document".equals(rest)) {
            return new Parsed(documentRef, DOCUMENT, "");
        }
        if (rest.startsWith("table_row:")) {
            return new Parsed(documentRef, TABLE_ROW, rest.substring(10));
        }
        if (rest.startsWith("table:")) {
            return new Parsed(documentRef, TABLE, rest.substring(6));
        }
        return null;
    }

    /** segment ordinal（payload 即数字串）；非数字 → null。 */
    static Integer segmentOrdinal(Parsed parsed) {
        if (parsed == null || !SEGMENT.equals(parsed.targetType())) {
            return null;
        }
        try {
            return Integer.parseInt(parsed.payload().trim());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /** table_row payload = {tableRef}:{rowIndex}；tableRef 自身可含 ':'，故从尾部切分。 */
    public static String tableRefOf(Parsed parsed) {
        if (parsed == null) {
            return null;
        }
        String payload = parsed.payload();
        if (TABLE.equals(parsed.targetType())) {
            return payload;
        }
        if (!TABLE_ROW.equals(parsed.targetType())) {
            return null;
        }
        int cut = payload.lastIndexOf(':');
        return cut > 0 ? payload.substring(0, cut) : payload;
    }

    /** table_row 行号（尾部整数字段）；缺失/非数字 → null。 */
    static Integer rowIndexOf(Parsed parsed) {
        if (parsed == null || !TABLE_ROW.equals(parsed.targetType())) {
            return null;
        }
        int cut = parsed.payload().lastIndexOf(':');
        if (cut < 0 || cut == parsed.payload().length() - 1) {
            return null;
        }
        try {
            return Integer.parseInt(parsed.payload().substring(cut + 1).trim());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /** 章节 path（section target 的标题面包屑，'/' 连接）。 */
    public static String sectionPathOf(Parsed parsed) {
        return parsed != null && SECTION.equals(parsed.targetType()) ? parsed.payload() : null;
    }

    /** 表格资产 ref：{doc}#table:{tableRef}。 */
    static String tableAssetRef(String documentRef, String tableRef) {
        return documentRef + "#table:" + tableRef;
    }
}
