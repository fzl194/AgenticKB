# -*- coding: utf-8 -*-
"""知识一张网接入配置（47 号 §四-1）.

凭据待遇与 llm_service.yaml 一致：服务端 yaml / 环境变量，不进 git、不进前端。
默认文件路径 ``main_control_service/config/system/onenet.yaml``（真实凭据），
仓库内只提交 ``onenet.example.yaml`` 占位。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: 实测默认端点（kone_connector 内网摸底 2026-09-07）。
DEFAULT_TOKEN_URL = "http://oauth2.huawei.com/ApiCommonQuery/appToken/getRestAppDynamicToken"
DEFAULT_SEARCH_URL = (
    "http://apigw-cn-south02.huawei.com/api/search/source_type?source_type=0"
)

#: 默认配置文件（与 llm_service.yaml 同目录待遇；该文件不进 git）。
DEFAULT_CONFIG_PATH = Path("main_control_service/config/system/onenet.yaml")

_CHUNK_WIDTH_MAX = 10000  # 接口硬上限：单查询 from+size ≤ 10000


@dataclass(frozen=True)
class OnenetConfig:
    """一张网客户端配置（不可变；凭据只在服务端进程内出现）.

    字段留空默认值是为了让 ``__post_init__`` 统一抛 ``missing_credentials``
    （dataclass 必填约束会先抛 TypeError，丢失语义）。
    """

    app_id: str = ""
    static_token: str = ""
    token_url: str = DEFAULT_TOKEN_URL
    search_url: str = DEFAULT_SEARCH_URL
    source_type: int = 0
    timeout: int = 300
    #: TLS 校验（安全审查 M-1）：内网实测端点为明文 http（校验无意义），
    #: 默认关闭；端点切 https 时置 true 并配 CA。显式配置优于隐式 verify=False。
    verify_tls: bool = False
    #: part_id 分段宽度（段内一次拉满；接口上限 10000）
    chunk_width: int = 10000
    #: 段内翻页每页条数
    page_size: int = 1000
    #: 相邻请求之间的温和限速（秒）
    throttle_seconds: float = 0.3

    def __repr__(self) -> str:  # 凭据不落日志
        return (
            f"OnenetConfig(app_id={self.app_id!r}, static_token=***, "
            f"token_url={self.token_url!r}, search_url={self.search_url!r}, "
            f"source_type={self.source_type}, chunk_width={self.chunk_width})"
        )

    __str__ = __repr__

    def __post_init__(self) -> None:
        missing = [
            name for name in ("app_id", "static_token")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(
                f"missing_credentials: onenet config requires {missing}"
            )
        if self.chunk_width < 1 or self.chunk_width > _CHUNK_WIDTH_MAX:
            raise ValueError(
                f"chunk_width must be within [1, {_CHUNK_WIDTH_MAX}]"
            )
        if self.page_size < 1 or self.page_size > _CHUNK_WIDTH_MAX:
            raise ValueError(
                f"page_size must be within [1, {_CHUNK_WIDTH_MAX}]"
            )

    @classmethod
    def from_file(cls, path: str | Path) -> "OnenetConfig":
        """从 yaml 构造；env 变量（ONENET_APP_ID/ONENET_STATIC_TOKEN）覆盖文件值."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"onenet config not found: {p}")
        data: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"onenet config must be a mapping: {p}")
        fields: dict[str, Any] = {
            k: data[k] for k in (
                "app_id", "static_token", "token_url", "search_url",
                "source_type", "timeout", "chunk_width", "page_size",
                "throttle_seconds",
            ) if data.get(k) is not None
        }
        env_app = os.environ.get("ONENET_APP_ID")
        env_tok = os.environ.get("ONENET_STATIC_TOKEN")
        if env_app:
            fields["app_id"] = env_app
        if env_tok:
            fields["static_token"] = env_tok
        return cls(**fields)

    @classmethod
    def from_env(cls) -> "OnenetConfig | None":
        """仅凭环境变量构造；缺任一凭据返回 None（调用方按未配置处理）."""
        app_id = os.environ.get("ONENET_APP_ID")
        static_token = os.environ.get("ONENET_STATIC_TOKEN")
        if not app_id or not static_token:
            return None
        return cls(app_id=app_id, static_token=static_token)


def resolve_config() -> OnenetConfig | None:
    """解析顺序：env → 默认 yaml 路径；都不可用返回 None（功能未配置）."""
    from_env = OnenetConfig.from_env()
    if from_env is not None:
        return from_env
    if DEFAULT_CONFIG_PATH.exists():
        try:
            return OnenetConfig.from_file(DEFAULT_CONFIG_PATH)
        except (ValueError, FileNotFoundError):
            return None
    return None


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_SEARCH_URL",
    "DEFAULT_TOKEN_URL",
    "OnenetConfig",
    "resolve_config",
]
