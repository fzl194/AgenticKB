"""图片 VLM 图注（方案B：独立算子）的提示词契约。

单一事实来源：legacy 链 ``stages/image_caption`` 的默认提示词逐字沿用，
版本号 ``FIGCAP_PROMPT_VERSION`` 进缓存键——改提示词必换版本号，
否则命中旧缓存（见 keys.caption_cache_key）。
"""
from __future__ import annotations

FIGCAP_PROMPT_VERSION = "vlm-figure-caption-1"

FIGCAP_SYSTEM_PROMPT = "你是技术文档图注助手。"

FIGCAP_USER_PROMPT = (
    "请用一两句中文描述这张图在技术文档中的含义。"
    "关注图中的结构、标注、流程或关键对象；不要编造看不清的细节。"
    "只输出图注正文，不要前缀「图注：」。"
)


def build_user_prompt(native_caption: str | None) -> str:
    """用户提示词；有原生图注（caption_of 绑定文本）时附上供参考。"""
    native = (native_caption or "").strip()
    if not native:
        return FIGCAP_USER_PROMPT
    return f"{FIGCAP_USER_PROMPT}\n\n文档中的原始图注/表注（可参考）：{native}"


__all__ = [
    "FIGCAP_PROMPT_VERSION",
    "FIGCAP_SYSTEM_PROMPT",
    "FIGCAP_USER_PROMPT",
    "build_user_prompt",
]
