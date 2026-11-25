"""图片 VLM 图注（方案B：独立算子）的提示词契约。

单一事实来源；版本号 ``FIGCAP_PROMPT_VERSION`` 进缓存键——改提示词
必换版本号，否则命中旧缓存（见 keys.caption_cache_key）。

v2（2026-11-25 用户实测反馈）：v1 只要"一两句话"，图里的数字/标签/
系列名大量丢失（柱状图只剩峰谷两个数）。v2 明确要求转述图中全部
可见文字与数值，图注要能独立支撑检索——长度上限 150 字防刷屏。
"""
from __future__ import annotations

FIGCAP_PROMPT_VERSION = "vlm-figure-caption-2"

FIGCAP_SYSTEM_PROMPT = "你是技术文档图注助手，擅长把图表内容完整转述为可检索的文字。"

FIGCAP_USER_PROMPT = (
    "请用中文详细描述这张图，目标是让没看到图的人仅凭这段文字就能检索并理解它。"
    "必须做到：\n"
    "1. 转述图中出现的全部可见文字：标题、坐标轴/图例的类别名、"
    "节点/连线上的标签、单元格里的词；\n"
    "2. 转述图中出现的全部数值及其归属（哪个月/哪个系列/哪个对象是多少），"
    "数值照抄不要换算或取整；\n"
    "3. 说明图的结构（流程/柱状/折线/表格/架构图等）和各部分的指向关系；\n"
    "4. 图里有原生标注/图例文字时一并纳入。\n"
    "禁止：编造看不清或不存在的内容；输出与图无关的评论。\n"
    "长度 3-6 句、不超过 150 字；只输出图注正文，不要前缀「图注：」。"
)


def build_user_prompt(native_caption: str | None) -> str:
    """用户提示词；有原生图注（caption_of 绑定文本）时附上供参考。"""
    native = (native_caption or "").strip()
    if not native:
        return FIGCAP_USER_PROMPT
    return f"{FIGCAP_USER_PROMPT}\n\n文档中的原始图注/表注（可参考，内容若与图不符以图为准）：{native}"


__all__ = [
    "FIGCAP_PROMPT_VERSION",
    "FIGCAP_SYSTEM_PROMPT",
    "FIGCAP_USER_PROMPT",
    "build_user_prompt",
]
