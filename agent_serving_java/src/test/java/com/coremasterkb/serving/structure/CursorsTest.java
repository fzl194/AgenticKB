package com.coremasterkb.serving.structure;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * A0-3（34 号 §P0）：get_document 分页游标语义。
 *
 * <p>cursor = 「上一页最后一条的实际 ordinal」（排他下界）。空 cursor 起点为 -1
 * ——SQL 是 {@code ordinal > afterIndex}，起点 0 会漏掉 segment_index=0。
 * 历史 {@code o:} 前缀（旧 offset 语义，但其值本就被当 afterIndex 消费）继续兼容；
 * {@code o:0} 与空等价（旧语义下两者都漏首段——修正为同一起点）。</p>
 */
@DisplayName("A0-3 Cursors：排他 ordinal 游标")
class CursorsTest {

    @Test
    @DisplayName("空/blank cursor → -1（含 segment 0 的起点）")
    void blankCursorStartsBeforeFirstSegment() {
        assertThat(Cursors.decodeAfter(null)).isEqualTo(-1);
        assertThat(Cursors.decodeAfter("")).isEqualTo(-1);
        assertThat(Cursors.decodeAfter("   ")).isEqualTo(-1);
    }

    @Test
    @DisplayName("a: 游标往返：记录上一页最后一条实际 ordinal")
    void afterCursorRoundTrip() {
        String cursor = Cursors.encodeAfter(5);
        assertThat(Cursors.decodeAfter(cursor)).isEqualTo(5);
        assertThat(Cursors.encodeAfter(0)).isNotEqualTo(Cursors.encodeAfter(5));
    }

    @Test
    @DisplayName("历史 o: 游标兼容：值按排他下界解释（不漏不重）")
    void legacyOffsetCursorStillDecodes() {
        String legacy = java.util.Base64.getUrlEncoder().withoutPadding()
                .encodeToString("o:100".getBytes(java.nio.charset.StandardCharsets.UTF_8));
        assertThat(Cursors.decodeAfter(legacy)).isEqualTo(100);
    }

    @Test
    @DisplayName("历史 o:0 与空 cursor 等价（旧值 0 恰是漏首段的 bug 本身）")
    void legacyZeroCursorTreatedAsStart() {
        String legacyZero = java.util.Base64.getUrlEncoder().withoutPadding()
                .encodeToString("o:0".getBytes(java.nio.charset.StandardCharsets.UTF_8));
        assertThat(Cursors.decodeAfter(legacyZero)).isEqualTo(-1);
    }

    @Test
    @DisplayName("非法 cursor → 稳定 typed error（不静默从头、不返回错页）")
    void invalidCursorRejected() {
        assertThatThrownBy(() -> Cursors.decodeAfter("not-a-cursor"))
                .isInstanceOf(StructureToolException.class);
        assertThatThrownBy(() -> Cursors.decodeAfter(
                java.util.Base64.getUrlEncoder().withoutPadding()
                        .encodeToString("x:5".getBytes(java.nio.charset.StandardCharsets.UTF_8))))
                .isInstanceOf(StructureToolException.class)
                .hasMessageContaining("cursor");
        assertThatThrownBy(() -> Cursors.decodeAfter(
                java.util.Base64.getUrlEncoder().withoutPadding()
                        .encodeToString("a:abc".getBytes(java.nio.charset.StandardCharsets.UTF_8))))
                .isInstanceOf(StructureToolException.class);
    }

    @Test
    @DisplayName("负 ordinal 拒绝（cursor 值域非负）")
    void negativeValueRejected() {
        assertThatThrownBy(() -> Cursors.decodeAfter(
                java.util.Base64.getUrlEncoder().withoutPadding()
                        .encodeToString("a:-3".getBytes(java.nio.charset.StandardCharsets.UTF_8))))
                .isInstanceOf(StructureToolException.class);
    }
}
