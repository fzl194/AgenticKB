"""Document Parse Operator layer (M4, SRS §4.6/§4.9/§4.10 / WP7+WP9).

质量门控的完整解析编排：ParsePlan（primary + 有序 fallback + 预算）→
Run 状态机推进 → attempt 审计 → 质量决策（PASS/WARN 提交快照、FALLBACK
换后端、FAIL 终止）→ SUPERSEDED（过期输入不发布）。
"""
from knowledge_mining.mining.parse_operator.service import DocumentParseService

__all__ = ["DocumentParseService"]
