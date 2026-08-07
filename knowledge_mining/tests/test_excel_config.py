from knowledge_mining.mining.infra import excel_config


def test_excel_config_uses_defaults(monkeypatch):
    monkeypatch.setattr(excel_config, "get_mining_service_config", lambda: {})

    cfg = excel_config.ExcelConfig()

    assert cfg.max_sheets == 200
    assert cfg.max_nonempty_cells == 1_000_000
    assert cfg.table_chunk_target_tokens == 420


def test_excel_config_reads_control_plane(monkeypatch):
    monkeypatch.setattr(
        excel_config,
        "get_mining_service_config",
        lambda: {
            "excel": {
                "max_sheets": 12,
                "max_nonempty_cells": 3456,
                "table_chunk_target_tokens": 256,
            }
        },
    )

    cfg = excel_config.ExcelConfig()

    assert (cfg.max_sheets, cfg.max_nonempty_cells) == (12, 3456)
    assert cfg.table_chunk_target_tokens == 256
