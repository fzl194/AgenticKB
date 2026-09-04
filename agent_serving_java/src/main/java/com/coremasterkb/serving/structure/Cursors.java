package com.coremasterkb.serving.structure;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Map;

/**
 * 结构工具族的稳定 cursor（批次8 R7 / A0-3 拆分为两种显式语义）。
 *
 * <p>两种游标不得混用（曾共用单一 decode，导致「行偏移」与「排他 ordinal」语义打架）：</p>
 * <ul>
 *   <li><b>after</b>（get_document 分页，A0-3 修订）：base64url("a:{afterOrdinal}")，
 *       记录上一页最后一条<b>实际返回行</b>的 ordinal，SQL 以 {@code ordinal > after} 推进
 *       ——编号稀疏/跳跃时不漏不重；空 cursor 起点 {@link #START}（-1），第一页含
 *       segment 0（此前起点 0 + {@code ordinal > 0} 漏首段）。历史 {@code o:} 值兼容
 *       （其值本就被当排他下界消费；0 与空等价）。</li>
 *   <li><b>offset</b>（structure_navigate 分页，保持批次8 原语义）：
 *       base64url("o:{offset}")，行偏移（{@code LIMIT/OFFSET}）；空 cursor = 0。</li>
 * </ul>
 *
 * <p>cursor 只接受本服务签发的格式；非法输入按 typed error 拒绝（Agent 原样回传，
 * 不自行构造）。</p>
 */
final class Cursors {

    /** after 游标的空 cursor 起点（排他下界，使 segment 0 入选第一页）。 */
    static final int START = -1;

    private Cursors() {}

    // ---- after：排他 ordinal（get_document） ----

    /** 记录「上一页最后一条实际 ordinal」的游标。 */
    static String encodeAfter(int afterOrdinal) {
        return encode("a:" + afterOrdinal);
    }

    /**
     * @throws StructureToolException 非法游标（稳定错误：请原样传回上次响应的
     *         cursor，或不传从头开始）——不静默从头、不返回错页。
     */
    static int decodeAfter(String cursor) {
        if (cursor == null || cursor.isBlank()) {
            return START;
        }
        String raw = decodeRaw(cursor);
        if (raw.startsWith("a:")) {
            return nonNegative(raw.substring(2));
        }
        if (raw.startsWith("o:")) {
            // 历史 offset 语义：值此前就被当作 ordinal 排他下界消费；0 与空等价
            //（旧实现里两者都漏 segment 0——此处统一修正为起点）。
            int value = nonNegative(raw.substring(2));
            return value == 0 ? START : value;
        }
        throw badCursor();
    }

    // ---- offset：行偏移（structure_navigate，原语义） ----

    static String encodeOffset(int offset) {
        return encode("o:" + Math.max(offset, 0));
    }

    /** @throws StructureToolException 非法游标（空/blank → 0）。 */
    static int decodeOffset(String cursor) {
        if (cursor == null || cursor.isBlank()) {
            return 0;
        }
        String raw = decodeRaw(cursor);
        if (raw.startsWith("o:")) {
            return nonNegative(raw.substring(2));
        }
        throw badCursor();
    }

    // ---- shared ----

    private static String encode(String raw) {
        return Base64.getUrlEncoder().withoutPadding()
                .encodeToString(raw.getBytes(StandardCharsets.UTF_8));
    }

    private static String decodeRaw(String cursor) {
        try {
            return new String(Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            throw badCursor();
        }
    }

    private static int nonNegative(String digits) {
        int value;
        try {
            value = Integer.parseInt(digits.trim());
        } catch (NumberFormatException e) {
            throw badCursor();
        }
        if (value < 0) {
            throw badCursor();
        }
        return value;
    }

    private static StructureToolException badCursor() {
        return StructureToolException.unsupportedOperation(
                "cursor 非法——请原样传回上次响应的 cursor，或不传从头开始", Map.of());
    }
}
