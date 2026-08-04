package com.coremasterkb.serving.mapper.param;

/**
 * One contiguous span of segments to fetch: {@code segment_index} between {@code fromIndex} and
 * {@code toIndex} inside a single snapshot.
 *
 * <p>Several targets often land in the same document, and their windows may or may not overlap, so
 * the mapper takes a list of these and OR-s them into one query rather than issuing one round trip
 * per target.</p>
 */
public record SegmentWindow(String snapshotId, int fromIndex, int toIndex) {}
