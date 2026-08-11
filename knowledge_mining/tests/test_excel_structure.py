from knowledge_mining.mining.ingestion.excel_structure import (
    detect_regions,
    infer_headers,
)


def test_detect_regions_splits_on_blank_row_and_column():
    rows = (
        ("名称", "值", "", "参数", "配置"),
        ("AMF", "1", "", "超时", "30"),
        ("", "", "", "", ""),
        ("区域", "状态", "", "", ""),
        ("华东", "正常", "", "", ""),
    )

    regions = detect_regions(rows)

    assert [(region.a1_range, region.rows) for region in regions] == [
        ("A1:B2", (("名称", "值"), ("AMF", "1"))),
        ("D1:E2", (("参数", "配置"), ("超时", "30"))),
        ("A4:B5", (("区域", "状态"), ("华东", "正常"))),
    ]


def test_infer_headers_combines_multilevel_headers():
    rows = (
        ("设备", "设备", "运行"),
        ("名称", "厂家", "状态"),
        ("AMF01", "华为", "正常"),
        ("SMF01", "中兴", "正常"),
    )

    headers, data = infer_headers(rows, max_header_rows=3)

    assert headers == ("设备/名称", "设备/厂家", "运行/状态")
    assert data[0] == ("AMF01", "华为", "正常")


def test_infer_headers_does_not_consume_unreliable_first_row():
    rows = (("AMF01", "华为"), ("SMF01", "中兴"))

    headers, data = infer_headers(rows, max_header_rows=3)

    assert headers == ("列A", "列B")
    assert data == rows
