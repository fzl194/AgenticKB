package com.coremasterkb.serving.operator.paradigm.mapper;

import com.coremasterkb.serving.operator.paradigm.ParadigmEntity;
import org.apache.ibatis.annotations.Param;

import java.util.List;

/**
 * CRUD for {@code operator_paradigm}. Runs on the default (routing) SqlSessionFactory; callers
 * invoke these with no {@code DomainContext} set, so the routing DataSource falls back to the
 * non-routed {@code defaultDataSource} (the shared/control DB).
 */
public interface ParadigmMapper {

    int insert(ParadigmEntity entity);

    ParadigmEntity selectById(@Param("id") String id);

    ParadigmEntity selectByName(@Param("name") String name);

    List<ParadigmEntity> selectAll();

    /** Published (currently active) paradigms only: {@code status='active'} with a published version. */
    List<ParadigmEntity> selectPublished();

    /**
     * The domain's live default paradigm, or null. Constrained to at most one row by
     * {@code uq_paradigm_domain_default}, whose predicate this query mirrors.
     */
    ParadigmEntity selectDefaultByDomain(@Param("domain") String domain);

    int updateDraft(@Param("id") String id, @Param("draftGraphJson") String draftGraphJson);

    int updatePublish(@Param("id") String id, @Param("version") int version, @Param("status") String status);

    /**
     * Clear the current default for a domain. MUST be called before {@link #updateBinding} sets a
     * new default, in the same transaction — the partial unique index makes set-then-clear fail.
     */
    int clearDefaultForDomain(@Param("domain") String domain);

    /** Set (or with a null domain, clear) a paradigm's binding. */
    int updateBinding(@Param("id") String id,
                      @Param("domain") String domain,
                      @Param("isDefault") boolean isDefault);

    int deleteById(@Param("id") String id);
}
