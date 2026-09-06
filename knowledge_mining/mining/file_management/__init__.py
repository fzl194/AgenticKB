"""File Management package — persistence layer for documents & storage objects.

Hexagonal layering (ADR-0003 D-022):
- ``contracts/file_management.py`` — Repository Protocols + frozen records.
- ``repositories_memory.py`` — in-memory fake repos (tests + local dev).
- ``repositories_pg.py`` — PostgreSQL repos (psycopg pool, 008 DDL aligned).

旧 M1.2/M1.3 上传会话编排（service.py UploadSessionService）、文件管理
HTTP 面（router.py）与 FileManagementService（file_service.py）已随代码
瘦身批次3 移除——生产上传/文档管理走 kb/routes/documents.py →
DocumentService。新解析链（new_chain_services/parse-result 读取）继续使用
本包的 StorageObject / DocumentCurrentContent 仓储。

References:
- SRS §4.3 / §4.3A (document current content), §C01 (error codes).
"""
