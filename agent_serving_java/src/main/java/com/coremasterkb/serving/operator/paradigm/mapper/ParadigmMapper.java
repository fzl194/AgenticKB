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

    int updateDraft(@Param("id") String id, @Param("draftGraphJson") String draftGraphJson);

    int updatePublish(@Param("id") String id, @Param("version") int version, @Param("status") String status);

    int deleteById(@Param("id") String id);
}
