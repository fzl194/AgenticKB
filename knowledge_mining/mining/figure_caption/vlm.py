"""VLM 图注真实实现：llm_service 异步任务通道 + 视觉消息。

与 legacy ImageCaptioner 同通道（LlmTaskClient.submit_chat，model 逐请求
传入——llm_service 按模型名路由 vision 条目，不动 active_model）。
提示词契约见 prompts.py（与 legacy 默认文本逐字一致）。
"""
from __future__ import annotations

import logging
from typing import Any

from knowledge_mining.mining.figure_caption.prompts import (
    FIGCAP_SYSTEM_PROMPT,
    build_user_prompt,
)

logger = logging.getLogger(__name__)


class VlmFigureCaptioner:
    """``FigureCaptioner`` 的生产实现（同步阻塞；失败 raise 由门面计数）."""

    def __init__(
        self,
        task_client: Any,
        *,
        knowledge_domain: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._client = task_client
        self._knowledge_domain = knowledge_domain
        #: llm_service 配置的默认视觉模型（GET /api/v1/models/vision-default
        #: 启动期解析）。门面在算子参数留空时用它，保证缓存键是具体模型名。
        self.default_model = default_model

    def caption(
        self,
        *,
        image_bytes: bytes,
        mime: str | None,
        native_caption: str | None,
        model: str,
    ) -> str | None:
        from llm_service.providers.multimodal import build_vision_user_message

        messages = [
            {"role": "system", "content": FIGCAP_SYSTEM_PROMPT},
            build_vision_user_message(
                build_user_prompt(native_caption), image_bytes=image_bytes,
            ),
        ]
        task_id = self._client.submit_chat(
            messages,
            expected_output_type="text",
            pipeline_stage="figure_caption",
            model=model,
            knowledge_domain=self._knowledge_domain,
        )
        entries = self._client.wait_for_tasks([task_id])
        entry = entries.get(task_id) or {"task_id": task_id, "status": "timeout"}
        status = str(entry.get("status"))
        if status != "succeeded":
            err = entry.get("error") or {}
            raise RuntimeError(
                f"caption task {task_id} ended as {status}: "
                f"{err.get('error_type')}: {err.get('error_message')}"
            )
        result = entry.get("result") or {}
        text = result.get("text_output")
        if isinstance(text, str) and text.strip():
            return text.strip()
        return None


__all__ = ["VlmFigureCaptioner"]
