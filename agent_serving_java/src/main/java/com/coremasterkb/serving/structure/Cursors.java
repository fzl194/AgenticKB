package com.coremasterkb.serving.structure;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Map;

/**
 * 结构工具族的稳定 cursor（批次8 R7）：base64url("o:{offset}")。
 *
 * <p>快照不可变 → (ordinal, ref)/(row_index) 顺序稳定 → offset 语义稳定。cursor 只接受
 * 本服务签发的前缀格式，非法输入按 typed error 拒绝（Agent 原样回传，不自行构造）。</p>
 */
final class Cursors {

    private Cursors() {}

    static String encode(int offset) {
        return Base64.getUrlEncoder().withoutPadding()
                .encodeToString(("o:" + offset).getBytes(StandardCharsets.UTF_8));
    }

    static int decode(String cursor) {
        if (cursor == null || cursor.isBlank()) {
            return 0;
        }
        try {
            String raw = new String(Base64.getUrlDecoder().decode(cursor), StandardCharsets.UTF_8);
            if (!raw.startsWith("o:")) {
                throw new IllegalArgumentException("bad prefix");
            }
            return Math.max(Integer.parseInt(raw.substring(2)), 0);
        } catch (Exception e) {
            throw StructureToolException.unsupportedOperation(
                    "cursor 非法——请原样传回上次响应的 cursor，或不传从头开始", Map.of());
        }
    }
}
