# -*- coding: utf-8 -*-
"""知识一张网切片接入（47 号实施设计）.

分层：config（凭据）→ client（接口）→ probe/toc_scan（摸底/树）→
restore（β 规则文件还原）→ fetch（批次拉取）→ parse_adapters/onenet_jsonl
（IR 适配器）→ import_service（导入编排）→ refs_service（KB 引用）→
resync（重同步）→ routes（管理面 API）。
"""
