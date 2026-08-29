package com.coremasterkb.serving.structure;

import com.coremasterkb.serving.application.KbAccessService;
import com.coremasterkb.serving.entity.AssetBuildDocumentSnapshot;
import com.coremasterkb.serving.evidence.EvidenceRefCodec;
import com.coremasterkb.serving.evidence.EvidenceRefResolver;
import com.coremasterkb.serving.mapper.AssetBuildDocumentSnapshotMapper;
import com.coremasterkb.serving.operator.mapper.StructureToolMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * 批次8 R7 ref 反查契约（25 号 §7.2/§10.1）：
 * 前缀/格式非法 → invalid_ref；活动快照命中 → ResolvedRef；历史快照命中 → expired_ref；
 * 无命中/无权 → out_of_scope（不泄漏归属——无权与不存在同响应）。
 */
@DisplayName("StructureRefService")
class StructureRefServiceTest {

    private static final String SNAP = "snap-1";
    private static final String KB = "kb-1";
    private static final String DOMAIN = "odn";
    private static final String USER = "alice";

    private EvidenceRefCodec codec;
    private KbAccessService kbAccessService;
    private AssetBuildDocumentSnapshotMapper buildSnapshotMapper;
    private StructureToolMapper toolMapper;
    private StructureRefService service;

    @BeforeEach
    void setUp() {
        codec = EvidenceRefCodec.forSecret("test-secret");
        kbAccessService = mock(KbAccessService.class);
        buildSnapshotMapper = mock(AssetBuildDocumentSnapshotMapper.class);
        toolMapper = mock(StructureToolMapper.class);
        service = new StructureRefService(codec, kbAccessService, buildSnapshotMapper, toolMapper);

        when(kbAccessService.authorize(anyString(), anyList(), anyString()))
                .thenReturn(List.of(KB));
        when(buildSnapshotMapper.selectLatestKbSnapshots(anyString(), anyList()))
                .thenReturn(List.of(snapshot(SNAP)));
    }

    // ---- happy path ------------------------------------------------------------

    @Test
    @DisplayName("st_ ref：活动快照候选枚举命中 → (snapshot, STRUCTURE, internalRef)")
    void resolvesStructureRefFromActiveSnapshots() {
        String internal = "doc:/spec#section:一/概述";
        when(toolMapper.selectStructureRefCandidates(List.of(SNAP), StructureRefService.ACTIVE_CANDIDATE_CAP))
                .thenReturn(List.of(
                        new StructureToolMapper.RefRow(SNAP, "doc:/spec"),
                        new StructureToolMapper.RefRow(SNAP, internal)));

        EvidenceRefResolver.ResolvedRef out =
                service.resolve(codec.encodeStructure(SNAP, internal), DOMAIN, List.of(KB), USER);

        assertThat(out.snapshotId()).isEqualTo(SNAP);
        assertThat(out.kind()).isEqualTo(EvidenceRefResolver.RefKind.STRUCTURE);
        assertThat(out.internalRef()).isEqualTo(internal);
    }

    @Test
    @DisplayName("ev_ ref：canonical 候选命中")
    void resolvesEvidenceRef() {
        String canonical = "doc:/spec#table_row:tbl:1:3";
        when(toolMapper.selectCanonicalRefCandidates(List.of(SNAP), StructureRefService.ACTIVE_CANDIDATE_CAP))
                .thenReturn(List.of(new StructureToolMapper.RefRow(SNAP, canonical)));

        EvidenceRefResolver.ResolvedRef out =
                service.resolve(codec.encodeEvidence(SNAP, canonical), DOMAIN, List.of(KB), USER);

        assertThat(out.kind()).isEqualTo(EvidenceRefResolver.RefKind.EVIDENCE);
        assertThat(out.internalRef()).isEqualTo(canonical);
    }

    @Test
    @DisplayName("同密钥不同 snapshot 不串扰：候选含其他 snapshot 的同 internalRef")
    void snapshotBindingPreventsCrossSnapshotCollision() {
        String internal = "doc:/spec#seg:1";
        // ref 绑定 snap-1；候选里 snap-2 有同 internalRef（编码不同 → 不命中 snap-2 行）
        String ref = codec.encodeStructure(SNAP, internal);
        when(toolMapper.selectStructureRefCandidates(List.of(SNAP), StructureRefService.ACTIVE_CANDIDATE_CAP))
                .thenReturn(List.of(
                        new StructureToolMapper.RefRow(SNAP, "doc:/spec"),
                        new StructureToolMapper.RefRow(SNAP, internal)));

        EvidenceRefResolver.ResolvedRef out = service.resolve(ref, DOMAIN, List.of(KB), USER);
        assertThat(out.snapshotId()).isEqualTo(SNAP);
        // 反证：snap-2 编码 ≠ snap-1 编码
        assertThat(codec.encodeStructure("snap-2", internal)).isNotEqualTo(ref);
    }

    // ---- typed errors ------------------------------------------------------------

    @Test
    @DisplayName("非 ev_/doc_/st_ 前缀 → invalid_ref(400)")
    void rejectsUnknownPrefix() {
        assertThatThrownBy(() -> service.resolve("xx_aaaaaaaaaaaa", DOMAIN, List.of(KB), USER))
                .isInstanceOf(StructureToolException.class)
                .satisfies(e -> {
                    StructureToolException ste = (StructureToolException) e;
                    assertThat(ste.code()).isEqualTo("invalid_ref");
                    assertThat(ste.status().value()).isEqualTo(400);
                });
    }

    @Test
    @DisplayName("短哈希长度非法 → invalid_ref")
    void rejectsBadHashLength() {
        assertThatThrownBy(() -> service.resolve("st_short", DOMAIN, List.of(KB), USER))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> ((StructureToolException) e).code())
                .isEqualTo("invalid_ref");
    }

    @Test
    @DisplayName("活动未命中 + 历史快照命中 → expired_ref(410)")
    void expiredWhenOnlyHistoricalSnapshotMatches() {
        String internal = "doc:/spec#seg:9";
        when(toolMapper.selectStructureRefCandidates(List.of(SNAP), StructureRefService.ACTIVE_CANDIDATE_CAP))
                .thenReturn(List.of()); // 活动快照无此 ref
        when(toolMapper.selectKbSnapshotRefs(DOMAIN, List.of(KB), StructureRefService.HISTORICAL_CANDIDATE_CAP))
                .thenReturn(List.of(new StructureToolMapper.RefRow("snap-old", "snap-old")));
        when(toolMapper.selectStructureRefCandidates(List.of("snap-old"),
                StructureRefService.HISTORICAL_CANDIDATE_CAP))
                .thenReturn(List.of(new StructureToolMapper.RefRow("snap-old", internal)));

        assertThatThrownBy(() -> service.resolve(
                        codec.encodeStructure("snap-old", internal), DOMAIN, List.of(KB), USER))
                .isInstanceOf(StructureToolException.class)
                .satisfies(e -> {
                    StructureToolException ste = (StructureToolException) e;
                    assertThat(ste.code()).isEqualTo("expired_ref");
                    assertThat(ste.status().value()).isEqualTo(410);
                });
    }

    @Test
    @DisplayName("全部未命中 → out_of_scope(404)，不泄漏真实归属")
    void outOfScopeWhenNothingMatches() {
        when(toolMapper.selectStructureRefCandidates(anyList(), anyInt()))
                .thenReturn(List.of(new StructureToolMapper.RefRow(SNAP, "doc:/spec")));
        when(toolMapper.selectKbSnapshotRefs(anyString(), anyList(), anyInt()))
                .thenReturn(List.of());

        assertThatThrownBy(() -> service.resolve(
                        codec.encodeStructure(SNAP, "doc:/secret#seg:1"), DOMAIN, List.of(KB), USER))
                .isInstanceOf(StructureToolException.class)
                .satisfies(e -> {
                    StructureToolException ste = (StructureToolException) e;
                    assertThat(ste.code()).isEqualTo("out_of_scope");
                    assertThat(ste.status().value()).isEqualTo(404);
                    assertThat(ste.getMessage()).doesNotContain("kb-1");
                });
    }

    @Test
    @DisplayName("库授权失败（不存在/无权）→ out_of_scope，与未命中同响应")
    void kbDeniedMapsToOutOfScope() {
        when(kbAccessService.authorize(anyString(), anyList(), anyString()))
                .thenThrow(new IllegalArgumentException("kb_not_found"));

        assertThatThrownBy(() -> service.resolve("st_aaaaaaaaaaaa", DOMAIN, List.of(KB), USER))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> ((StructureToolException) e).code())
                .isEqualTo("out_of_scope");
    }

    @Test
    @DisplayName("kb_ids 缺失 → out_of_scope（内部端点必须带库范围）")
    void missingKbIdsRejected() {
        assertThatThrownBy(() -> service.resolve("st_aaaaaaaaaaaa", DOMAIN, List.of(), USER))
                .isInstanceOf(StructureToolException.class)
                .extracting(e -> ((StructureToolException) e).code())
                .isEqualTo("out_of_scope");
    }

    // ---- helpers ------------------------------------------------------------------------

    private static AssetBuildDocumentSnapshot snapshot(String id) {
        AssetBuildDocumentSnapshot s = new AssetBuildDocumentSnapshot();
        s.setDocumentSnapshotId(id);
        return s;
    }
}
