"""ImageCaptioner 异步通道单测（MockTransport 假 llm_service）。

锁定：async 模式走 /api/v1/tasks（messages+model 直传）并回填图注；
任务超时/失败回退 native caption（永不阻断文档）；sync 模式仍走
/api/v1/execute。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from knowledge_mining.mining.contracts.models import ContentBlock, SectionNode
from knowledge_mining.mining.stages.image_caption import ImageCaptioner


def _image_block(tmp_path: Path, *, native: str = "") -> ContentBlock:
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG fake")
    return ContentBlock(
        block_type="image",
        text="",
        language="zh",
        level=2,
        line_start=1,
        line_end=2,
        structure={"image_path": str(img), "native_caption": native, "page": 7},
    )


def _make_captioner(tmp_path: Path, handler, *, call_mode="async") -> tuple[ImageCaptioner, ContentBlock]:
    captioner = ImageCaptioner(
        "http://fake",
        enabled=True,
        call_mode=call_mode,
        async_wait_timeout=0.2,
    )
    # 注入 MockTransport（构造缝：替换底层 task/llm 客户端的 httpx 传输）
    transport = httpx.MockTransport(handler)
    if call_mode == "async":
        captioner._task_client._transport = transport
        captioner._task_client.close()
    else:
        captioner._client._client = httpx.Client(transport=transport)
    return captioner, _image_block(tmp_path, native="原生图注")


def test_caption_uses_task_channel_and_fills_text(tmp_path):
    submits: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        if request.url.path == "/api/v1/tasks":
            submits.append(body)
            return httpx.Response(200, json={
                "success": True, "data": {"task_id": "task-1", "status": "queued"},
            })
        return httpx.Response(200, json={"success": True, "data": {"tasks": [{
            "task_id": "task-1", "status": "succeeded",
            "result": {"parse_status": "succeeded", "text_output": "架构示意图"},
        }]}})

    captioner, block = _make_captioner(tmp_path, handler)
    node = SectionNode(title="t", level=1, children=(), blocks=(block,))
    out = captioner.caption_tree(node)

    filled = out.blocks[0]
    assert filled.text == "架构示意图"
    assert filled.structure["caption_source"] == "vlm"
    assert filled.structure["vlm_model"] == "glm-4.5v"
    # 提交形状：messages 直传 + VLM model + segment 阶段
    assert submits[0]["model"] == "glm-4.5v"
    assert submits[0]["pipeline_stage"] == "segment"
    assert submits[0]["expected_output_type"] == "text"


def test_caption_timeout_falls_back_to_native_caption(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tasks":
            return httpx.Response(200, json={
                "success": True, "data": {"task_id": "task-1", "status": "queued"},
            })
        return httpx.Response(200, json={"success": True, "data": {"tasks": [{
            "task_id": "task-1", "status": "running",  # 永不到终态 → wait 超时
        }]}})

    captioner, block = _make_captioner(tmp_path, handler)
    node = SectionNode(title="t", level=1, children=(), blocks=(block,))
    out = captioner.caption_tree(node)

    filled = out.blocks[0]
    assert filled.text == "原生图注"
    assert filled.structure["caption_source"] == "fallback"


def test_caption_sync_mode_uses_execute(tmp_path):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"success": True, "data": {
            "status": "succeeded",
            "result": {"parse_status": "succeeded", "text_output": "同步图注"},
        }})

    captioner, block = _make_captioner(tmp_path, handler, call_mode="sync")
    node = SectionNode(title="t", level=1, children=(), blocks=(block,))
    out = captioner.caption_tree(node)

    assert out.blocks[0].text == "同步图注"
    assert calls == ["/api/v1/execute"]
