"""图注缓存键（纯函数）。

键 = sha256(领域 + 源文件哈希 + 解析器指纹 + figure 元素 id + 模型 + 提示词版本)。
任何成分变化都必然换键：同图重跑（快照复用/换模型/改提示词/解析器升级）
不会误命中旧缓存。方案A（derived_annotations）与方案B共用此契约。
"""
from __future__ import annotations

import hashlib


def caption_cache_key(
    *,
    domain: str,
    source_raw_hash: str,
    parser_fingerprint: str,
    figure_element_id: str,
    model: str,
    prompt_version: str,
) -> str:
    payload = "|".join(
        (
            domain,
            source_raw_hash,
            parser_fingerprint,
            figure_element_id,
            model,
            prompt_version,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["caption_cache_key"]
