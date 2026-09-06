"""Layer 3: Pipeline stage implementations (legacy).

Each stage implements protocols from contracts.protocols and uses
infrastructure from infra/. Stages are imported directly by the legacy
pipeline (jobs/run.py legacy 分支恢复历史 Run 时使用)。

旧 stage registry（register_stage/get_stage/list_stages + _auto_discover）
已随代码瘦身批次3 移除：全仓无调用方，/api/config/stages 返回硬编码列表
也不读取它；auto-discover 反而在 import 本包时连带加载全部旧 stage 模块。
"""
