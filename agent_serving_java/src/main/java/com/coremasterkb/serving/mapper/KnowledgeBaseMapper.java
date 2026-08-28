package com.coremasterkb.serving.mapper;

import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * Read-only access to the KB management tables ({@code knowledge_bases}, {@code kb_members}),
 * which mining owns and serving only ever reads — the same arrangement serving has with
 * {@code asset_*}.
 */
public interface KnowledgeBaseMapper {

    /**
     * Of the requested knowledge bases, return the ids the caller may read.
     *
     * <p>Mirrors {@code KbDB.is_visible} on the mining side: active KB, and one of owner /
     * {@code public} / member. {@code username} is the {@code X-KB-User} identity; a null or
     * unknown username is treated as anonymous and sees {@code public} KBs only — serving never
     * creates {@code kb_users} rows the way mining's auth dependency does.</p>
     *
     * @param domain   routed domain; a kb id belonging to another domain never matches
     * @param kbIds    non-empty list of requested knowledge base ids
     * @param username caller identity from {@code X-KB-User}, may be null
     */
    List<String> selectAccessibleKbIds(
            @Param("domain") String domain,
            @Param("kbIds") List<String> kbIds,
            @Param("username") String username);

    /**
     * 阶段 A：目标库各自绑定的库级默认检索范式（DISTINCT，排除 NULL）。
     * 返回恰好一个值 = 目标库库级绑定一致 → 范式跟随库走；多个值 = 绑定不一致，
     * 调用方降级到领域/官方默认。
     */
    List<String> selectDefaultParadigmIds(
            @Param("domain") String domain,
            @Param("kbIds") List<String> kbIds);
}
