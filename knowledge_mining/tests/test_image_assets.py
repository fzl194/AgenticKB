"""Tests for shared image asset helpers and parse-context image_dir injection."""
from __future__ import annotations

import base64

from knowledge_mining.mining.contracts.models import RawFileData
from knowledge_mining.mining.infra.image_assets import (
    IMAGE_CAPABLE_FILE_TYPES,
    materialize_data_uri,
    materialize_image_bytes,
    resolve_local_image_src,
)
from knowledge_mining.mining.pipeline import PipelineConfig, _parse_context


def test_materialize_image_bytes(tmp_path):
    meta = materialize_image_bytes(b"abc", tmp_path, stem="fig", ext=".png")
    assert meta["image_sha256"]
    assert meta["image_path"].endswith(".png")
    assert (tmp_path / __import__("pathlib").Path(meta["image_path"]).name).read_bytes() == b"abc"


def test_materialize_data_uri(tmp_path):
    uri = "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode()
    meta = materialize_data_uri(uri, tmp_path, stem="d")
    assert meta is not None
    assert meta["image_sha256"]


def test_resolve_relative_local(tmp_path):
    img = tmp_path / "images" / "a.png"
    img.parent.mkdir()
    img.write_bytes(b"pngdata")
    out = tmp_path / "dump"
    meta = resolve_local_image_src(
        "images/a.png", base_dir=tmp_path, image_dir=out, stem="a",
    )
    assert "image_path" in meta
    assert meta["image_sha256"]
    assert __import__("pathlib").Path(meta["image_path"]).is_file()


def test_resolve_remote_skipped_by_default(tmp_path):
    meta = resolve_local_image_src(
        "https://example.com/a.png", base_dir=tmp_path, image_dir=tmp_path / "d",
    )
    assert meta["caption_source"] == "remote_skipped"


def test_parse_context_injects_image_dir_for_markdown():
    raw = RawFileData(
        file_path="/tmp/doc.md",
        relative_path="doc.md",
        file_name="doc.md",
        file_type="markdown",
        content="# hi",
        raw_content_hash="a" * 64,
        normalized_content_hash="b" * 64,
    )
    cfg = PipelineConfig(domain="test", run_id="run-img-1")
    ctx = _parse_context(raw, cfg)
    assert "image_dir" in ctx
    assert "run-img-1" in ctx["image_dir"]


def test_parse_context_skips_txt():
    raw = RawFileData(
        file_path="/tmp/a.txt",
        relative_path="a.txt",
        file_name="a.txt",
        file_type="txt",
        content="hello",
        raw_content_hash="a" * 64,
        normalized_content_hash="b" * 64,
    )
    cfg = PipelineConfig(domain="test", run_id="run-img-2")
    ctx = _parse_context(raw, cfg)
    assert "image_dir" not in ctx
    assert "txt" not in IMAGE_CAPABLE_FILE_TYPES
