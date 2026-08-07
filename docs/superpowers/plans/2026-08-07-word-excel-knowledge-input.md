# Word and Excel Knowledge Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `.doc`, `.docx`, `.xls`, and `.xlsx` reliable knowledge-base mining inputs, using Markdown as the Excel intermediate representation and exposing preprocessing failures and warnings to API/UI callers without a database migration.

**Architecture:** Add focused Excel reader, structure, and Markdown-rendering modules under the ingestion package. Convert Excel workbooks to deterministic in-memory Markdown and reuse the current Markdown parser and mining pipeline. Persist status, warnings, and summaries in the existing `mining_run_documents.metadata_json` and `error_message` fields; do not add tables or columns.

**Tech Stack:** Python 3.11, FastAPI, `openpyxl`, `xlrd`, pytest, Vue 3, TypeScript, Vitest, Docker, LibreOffice Writer for legacy `.doc` conversion on Linux.

---

## File map

**Create**

- `knowledge_mining/mining/infra/excel_config.py` — read Excel processing limits from the existing mining YAML.
- `knowledge_mining/mining/ingestion/errors.py` — stable preprocessing exception and error metadata contract shared by Word and Excel.
- `knowledge_mining/mining/ingestion/excel_models.py` — immutable workbook/sheet/result/warning data models.
- `knowledge_mining/mining/ingestion/excel_reader.py` — `.xls` and `.xlsx` adapters producing normalized cell matrices.
- `knowledge_mining/mining/ingestion/excel_structure.py` — data-region and header inference independent of file format.
- `knowledge_mining/mining/ingestion/excel_preprocessing.py` — orchestration and deterministic Markdown rendering.
- `knowledge_mining/tests/test_excel_config.py` — Excel limit configuration tests.
- `knowledge_mining/tests/test_excel_reader.py` — reader, merged-cell, value, formula, and format tests.
- `knowledge_mining/tests/test_excel_structure.py` — region and header inference tests.
- `knowledge_mining/tests/test_excel_preprocessing.py` — Markdown, chunking, error, and ingestion integration tests.
- `kb-ui/src/components/kb/PreprocessNotice.vue` — run-document preprocessing status and warning display.
- `kb-ui/src/components/kb/__tests__/PreprocessNotice.spec.ts` — UI rendering tests.
- `docs/deployment/offline-document-dependencies.md` — wheelhouse, Docker image, and LibreOffice offline deployment guide.

**Modify**

- `pyproject.toml` — runtime Excel dependencies and test-only `.xls` writer.
- `knowledge_mining/requirements.txt` — keep the runtime dependency list aligned.
- `main_control_service/config/system/mining.yaml` — optional Excel safety limits.
- `knowledge_mining/mining/api/routes/uploads.py` — advertise `.xls/.xlsx` as supported upload formats.
- `knowledge_mining/mining/ingestion/__init__.py` — recognize Excel, invoke preprocessing, and preserve metadata/MIME.
- `knowledge_mining/mining/ingestion/doc_preprocessing.py` — stable converter-unavailable/conversion-failed errors.
- `knowledge_mining/mining/contracts/models.py` — update the `RawFileData.file_type` documentation.
- `knowledge_mining/mining/jobs/run.py` — copy structured preprocessing metadata to the run-document record.
- `knowledge_mining/mining/pipeline.py` — classify fatal preprocessing as document failure rather than generic skip.
- `knowledge_mining/mining/api/routes/runs.py` — expand preprocessing metadata in list/detail responses.
- `knowledge_mining/tests/test_doc_preprocessing.py` — stable Word error-code tests.
- `knowledge_mining/tests/test_pipeline_operators.py` — fatal/partial preprocessing status tests.
- `knowledge_mining/tests/test_api_runs.py` — API projection tests.
- `knowledge_mining/tests/test_mining_run_submission.py` — upload capability tests.
- `kb-ui/src/types/index.ts` — preprocessing response types.
- `kb-ui/src/views/kb/KbRunDocDetailView.vue` — display preprocessing status and warnings.
- `kb-ui/src/components/kb/KbFileManager.vue` — supported-file picker hint.
- `docker/Dockerfile` — install Excel Python packages and LibreOffice Writer into the exported runtime image.
- `knowledge_mining/README.md` — supported formats and runtime behavior.

No file under `databases/` is created or modified.

---

### Task 1: Add dependency and Excel-limit configuration contracts

**Files:**

- Create: `knowledge_mining/mining/infra/excel_config.py`
- Create: `knowledge_mining/tests/test_excel_config.py`
- Modify: `pyproject.toml`
- Modify: `knowledge_mining/requirements.txt`
- Modify: `main_control_service/config/system/mining.yaml`
- Modify: `knowledge_mining/mining/api/routes/uploads.py`
- Modify: `knowledge_mining/tests/test_mining_run_submission.py`

- [ ] **Step 1: Write failing configuration and advertised-extension tests**

```python
# knowledge_mining/tests/test_excel_config.py
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
        lambda: {"excel": {
            "max_sheets": 12,
            "max_nonempty_cells": 3456,
            "table_chunk_target_tokens": 256,
        }},
    )
    cfg = excel_config.ExcelConfig()
    assert (cfg.max_sheets, cfg.max_nonempty_cells) == (12, 3456)
    assert cfg.table_chunk_target_tokens == 256
```

Add this assertion to `test_upload_config_exposes_submission_engine` in `knowledge_mining/tests/test_mining_run_submission.py`:

```python
assert ".xls" in result["accepted_extensions"]
assert ".xlsx" in result["accepted_extensions"]
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_config.py knowledge_mining/tests/test_mining_run_submission.py::test_upload_config_exposes_submission_engine -q
```

Expected: collection fails because `excel_config` does not exist, or the upload assertion fails because the extensions are absent.

- [ ] **Step 3: Implement the configuration class and dependency declarations**

```python
# knowledge_mining/mining/infra/excel_config.py
from __future__ import annotations

from typing import Any

from knowledge_mining.mining.infra.control_plane import get_mining_service_config

_DEFAULTS = {
    "max_sheets": 200,
    "max_nonempty_cells": 1_000_000,
    "table_chunk_target_tokens": 420,
}


class ExcelConfig:
    def __init__(self, **fields: Any) -> None:
        raw = fields or (get_mining_service_config().get("excel") or {})
        self.max_sheets = int(raw.get("max_sheets", _DEFAULTS["max_sheets"]))
        self.max_nonempty_cells = int(
            raw.get("max_nonempty_cells", _DEFAULTS["max_nonempty_cells"])
        )
        self.table_chunk_target_tokens = int(
            raw.get("table_chunk_target_tokens", _DEFAULTS["table_chunk_target_tokens"])
        )
        if min(self.max_sheets, self.max_nonempty_cells, self.table_chunk_target_tokens) <= 0:
            raise ValueError("Excel limits must be positive integers")
```

Add to `main_control_service/config/system/mining.yaml`:

```yaml
excel:
  max_sheets: 200
  max_nonempty_cells: 1000000
  table_chunk_target_tokens: 420
```

Add runtime dependencies to both `pyproject.toml` and `knowledge_mining/requirements.txt`:

```text
openpyxl>=3.1,<4
xlrd>=2.0,<3
```

Add `xlwt>=1.3,<2` to the `dev` optional dependencies in `pyproject.toml`; it is used only to generate `.xls` fixtures during tests.

Add `.xls` and `.xlsx` to `_ACCEPTED_EXTENSIONS` in `knowledge_mining/mining/api/routes/uploads.py`.

- [ ] **Step 4: Run the focused tests and dependency consistency check**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_config.py knowledge_mining/tests/test_mining_run_submission.py::test_upload_config_exposes_submission_engine -q
python -c "import openpyxl, xlrd; print(openpyxl.__version__, xlrd.__version__)"
```

Expected: all tests pass and both versions print. If packages are absent locally, install from the approved package source before continuing; do not change application code to hide the missing dependency.

- [ ] **Step 5: Commit Task 1**

```powershell
git add pyproject.toml knowledge_mining/requirements.txt main_control_service/config/system/mining.yaml knowledge_mining/mining/infra/excel_config.py knowledge_mining/mining/api/routes/uploads.py knowledge_mining/tests/test_excel_config.py knowledge_mining/tests/test_mining_run_submission.py
git commit -m "build: add Excel ingestion dependencies and limits"
```

---

### Task 2: Build normalized `.xlsx` and `.xls` readers

**Files:**

- Create: `knowledge_mining/mining/ingestion/errors.py`
- Create: `knowledge_mining/mining/ingestion/excel_models.py`
- Create: `knowledge_mining/mining/ingestion/excel_reader.py`
- Create: `knowledge_mining/tests/test_excel_reader.py`

- [ ] **Step 1: Write failing reader tests for both formats**

```python
# knowledge_mining/tests/test_excel_reader.py
from datetime import datetime

from openpyxl import Workbook
import xlwt

from knowledge_mining.mining.ingestion.excel_reader import read_excel_workbook


def test_read_xlsx_preserves_sheet_state_merge_and_values(tmp_path):
    path = tmp_path / "设备.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "设备"
    ws.merge_cells("A1:B1")
    ws["A1"] = "基本信息"
    ws.append(["名称", "上线日期"])
    ws.append(["AMF01", datetime(2026, 8, 7)])
    hidden = wb.create_sheet("隐藏参数")
    hidden.sheet_state = "hidden"
    hidden.append(["参数", "值"])
    hidden.append(["超时", 30])
    wb.save(path)

    workbook = read_excel_workbook(path)

    assert [sheet.name for sheet in workbook.sheets] == ["设备", "隐藏参数"]
    assert workbook.sheets[1].hidden is True
    assert workbook.sheets[0].rows[0] == ("基本信息", "基本信息")
    assert workbook.sheets[0].rows[2][1] == "2026-08-07T00:00:00"


def test_read_xls_normalizes_values_and_merge(tmp_path):
    path = tmp_path / "legacy.xls"
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet1")
    ws.write_merge(0, 0, 0, 1, "基本信息")
    ws.write(1, 0, "名称")
    ws.write(1, 1, "数量")
    ws.write(2, 0, "SMF")
    ws.write(2, 1, 2)
    wb.save(str(path))

    workbook = read_excel_workbook(path)

    assert workbook.source_format == "xls"
    assert workbook.sheets[0].rows == (
        ("基本信息", "基本信息"),
        ("名称", "数量"),
        ("SMF", "2"),
    )
```

- [ ] **Step 2: Run tests and verify the modules are missing**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_reader.py -q
```

Expected: FAIL during import because the new reader modules do not exist.

- [ ] **Step 3: Add shared preprocessing errors and immutable models**

```python
# knowledge_mining/mining/ingestion/errors.py
from __future__ import annotations


class PreprocessingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message

    def as_metadata(self) -> dict[str, str]:
        return {
            "preprocess_status": "failed",
            "preprocess_error_code": self.code,
            "preprocess_error": self.safe_message,
        }
```

```python
# knowledge_mining/mining/ingestion/excel_models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExcelWarning:
    code: str
    message: str
    sheet_name: str | None = None
    cell_range: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            key: value for key, value in {
                "code": self.code,
                "message": self.message,
                "sheet_name": self.sheet_name,
                "cell_range": self.cell_range,
            }.items() if value is not None
        }


@dataclass(frozen=True)
class NormalizedSheet:
    name: str
    hidden: bool
    rows: tuple[tuple[str, ...], ...]
    formula_warnings: tuple[ExcelWarning, ...] = ()


@dataclass(frozen=True)
class NormalizedWorkbook:
    source_format: str
    sheets: tuple[NormalizedSheet, ...]
    warnings: tuple[ExcelWarning, ...] = ()


@dataclass(frozen=True)
class ExcelParseResult:
    markdown: str
    status: str
    source_format: str
    summary: dict[str, int]
    warnings: tuple[ExcelWarning, ...]
```

- [ ] **Step 4: Implement reader dispatch, scalar normalization, and merged-cell expansion**

Implement `read_excel_workbook(path: Path) -> NormalizedWorkbook` in `excel_reader.py` with these exact contracts:

```python
def read_excel_workbook(path: Path) -> NormalizedWorkbook:
    suffix = path.suffix.lower()
    try:
        if suffix == ".xlsx":
            return _read_xlsx(path)
        if suffix == ".xls":
            return _read_xls(path)
        raise PreprocessingError("excel_unsupported_format", f"Unsupported Excel format: {suffix}")
    except PreprocessingError:
        raise
    except ImportError as exc:
        raise PreprocessingError(
            "excel_dependency_missing", "Excel parser dependency is not installed"
        ) from exc
    except Exception as exc:
        message = str(exc).lower()
        code = "excel_password_protected" if "password" in message or "encrypted" in message else "excel_corrupt_file"
        raise PreprocessingError(code, f"Unable to read Excel workbook: {path.name}") from exc
```

Use `openpyxl.load_workbook(path, data_only=True, read_only=False, keep_links=False)` for cached values and a second `data_only=False` load for missing formula-cache detection. Use `xlrd.open_workbook(path, formatting_info=True, on_demand=True)` for `.xls`. Expand every merged range from its top-left value in the normalized matrix. Convert datetimes to ISO text, booleans to `true`/`false`, numbers with stable `format(value, ".15g")`, percentages according to the cell number format, and Excel error cells to readable markers plus warnings.

Catch failures while materializing an individual sheet, append an
`excel_sheet_parse_failed` item to `NormalizedWorkbook.warnings`, and continue
with the remaining sheets. Only workbook-open failures raise immediately.

- [ ] **Step 5: Run reader tests**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_reader.py -q
```

Expected: all reader tests pass.

- [ ] **Step 6: Commit Task 2**

```powershell
git add knowledge_mining/mining/ingestion/errors.py knowledge_mining/mining/ingestion/excel_models.py knowledge_mining/mining/ingestion/excel_reader.py knowledge_mining/tests/test_excel_reader.py
git commit -m "feat(mining): read xls and xlsx workbooks"
```

---

### Task 3: Detect table regions and deterministic headers

**Files:**

- Create: `knowledge_mining/mining/ingestion/excel_structure.py`
- Create: `knowledge_mining/tests/test_excel_structure.py`

- [ ] **Step 1: Write failing region and header tests**

```python
# knowledge_mining/tests/test_excel_structure.py
from knowledge_mining.mining.ingestion.excel_structure import detect_regions, infer_headers


def test_detect_regions_splits_on_blank_row_and_column():
    rows = (
        ("名称", "值", "", "参数", "配置"),
        ("AMF", "1", "", "超时", "30"),
        ("", "", "", "", ""),
        ("区域", "状态", "", "", ""),
        ("华东", "正常", "", "", ""),
    )
    regions = detect_regions(rows)
    assert [(r.a1_range, r.rows) for r in regions] == [
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
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_structure.py -q
```

Expected: FAIL because `excel_structure` does not exist.

- [ ] **Step 3: Implement region splitting and header inference**

Create an immutable `ExcelRegion` with `top`, `left`, `bottom`, `right`, `rows`, and computed `a1_range`. Implement `detect_regions` by trimming the outer empty rectangle and recursively splitting at fully empty rows first, then fully empty columns. Sort final regions by `(top, left)`.

Implement `infer_headers` deterministically:

1. Never consume a header when fewer than three rows exist unless merged-header provenance explicitly requires it.
2. Consider at most three leading rows.
3. A candidate header row must be at least 50% nonempty text and contain no date/boolean/error marker.
4. Stop at the first following row with a numeric/date/boolean value.
5. Require either a merged-header signal, unique field labels, or a following row whose value-shape differs from the candidate.
6. Join nonempty multirow labels with `/`, de-duplicate equal adjacent labels, and generate `列A`, `列B` when confidence is insufficient.

Use this format-neutral implementation as the starting point; keep the public
signatures stable if helper names are refined during implementation:

```python
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ExcelRegion:
    top: int
    left: int
    bottom: int
    right: int
    rows: tuple[tuple[str, ...], ...]

    @property
    def a1_range(self) -> str:
        return f"{_column_name(self.left)}{self.top + 1}:{_column_name(self.right - 1)}{self.bottom}"


def _column_name(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _present(value: str) -> bool:
    return bool(value.strip())


def _bands(start: int, end: int, separators: set[int]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    cursor = start
    for pos in sorted(separators):
        if cursor < pos:
            result.append((cursor, pos))
        cursor = pos + 1
    if cursor < end:
        result.append((cursor, end))
    return result


def detect_regions(rows: tuple[tuple[str, ...], ...]) -> tuple[ExcelRegion, ...]:
    if not rows:
        return ()
    width = max((len(row) for row in rows), default=0)
    matrix = tuple(tuple(row[col] if col < len(row) else "" for col in range(width)) for row in rows)
    occupied = [(r, c) for r, row in enumerate(matrix) for c, value in enumerate(row) if _present(value)]
    if not occupied:
        return ()
    top = min(item[0] for item in occupied)
    bottom = max(item[0] for item in occupied) + 1
    left = min(item[1] for item in occupied)
    right = max(item[1] for item in occupied) + 1
    found: list[ExcelRegion] = []

    def split(t: int, l: int, b: int, r: int) -> None:
        empty_rows = {
            row for row in range(t, b)
            if not any(_present(matrix[row][col]) for col in range(l, r))
        }
        if empty_rows:
            for band_top, band_bottom in _bands(t, b, empty_rows):
                split(band_top, l, band_bottom, r)
            return
        empty_cols = {
            col for col in range(l, r)
            if not any(_present(matrix[row][col]) for row in range(t, b))
        }
        if empty_cols:
            for band_left, band_right in _bands(l, r, empty_cols):
                split(t, band_left, b, band_right)
            return
        region_rows = tuple(tuple(matrix[row][col] for col in range(l, r)) for row in range(t, b))
        found.append(ExcelRegion(t, l, b, r, region_rows))

    split(top, left, bottom, right)
    return tuple(sorted(found, key=lambda item: (item.top, item.left)))


_SCALAR_RE = re.compile(r"^(?:[-+]?\d+(?:\.\d+)?%?|true|false|\d{4}-\d{2}-\d{2})$", re.I)


def _looks_like_header(row: tuple[str, ...]) -> bool:
    values = [value.strip() for value in row if value.strip()]
    return bool(values) and len(values) * 2 >= len(row) and not any(_SCALAR_RE.fullmatch(value) for value in values)


def _generated_headers(width: int) -> tuple[str, ...]:
    return tuple(f"列{_column_name(index)}" for index in range(width))


def infer_headers(
    rows: tuple[tuple[str, ...], ...], *, max_header_rows: int = 3
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    width = max((len(row) for row in rows), default=0)
    if len(rows) < 3 or not _looks_like_header(rows[0]):
        return _generated_headers(width), rows
    depth = 1
    while depth < min(max_header_rows, len(rows) - 1):
        previous = [value for value in rows[depth - 1] if value]
        needs_child_header = len(previous) != len(set(previous)) or any(not value for value in rows[depth - 1])
        if not needs_child_header or not _looks_like_header(rows[depth]):
            break
        depth += 1
    headers: list[str] = []
    for col in range(width):
        parts: list[str] = []
        for row in rows[:depth]:
            value = row[col].strip() if col < len(row) else ""
            if value and (not parts or parts[-1] != value):
                parts.append(value)
        headers.append("/".join(parts) if parts else f"列{_column_name(col)}")
    seen: dict[str, int] = {}
    unique: list[str] = []
    for header in headers:
        seen[header] = seen.get(header, 0) + 1
        unique.append(header if seen[header] == 1 else f"{header}_{seen[header]}")
    return tuple(unique), rows[depth:]
```

The implementation must contain no LLM call, random value, current timestamp, or locale-dependent ordering.

- [ ] **Step 4: Run structure tests**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_structure.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add knowledge_mining/mining/ingestion/excel_structure.py knowledge_mining/tests/test_excel_structure.py
git commit -m "feat(mining): infer Excel table regions and headers"
```

---

### Task 4: Render deterministic Markdown and enforce workbook limits

**Files:**

- Create: `knowledge_mining/mining/ingestion/excel_preprocessing.py`
- Create: `knowledge_mining/tests/test_excel_preprocessing.py`

- [ ] **Step 1: Write failing Markdown and limit tests**

```python
# knowledge_mining/tests/test_excel_preprocessing.py
from openpyxl import Workbook
import pytest

from knowledge_mining.mining.infra.excel_config import ExcelConfig
from knowledge_mining.mining.ingestion.errors import PreprocessingError
from knowledge_mining.mining.ingestion.excel_preprocessing import excel_to_markdown


def test_xlsx_becomes_sheet_scoped_markdown(tmp_path):
    path = tmp_path / "设备台账.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "核心网设备"
    ws.append(["设备名称", "厂家", "状态"])
    ws.append(["AMF01", "华为", "运行中"])
    wb.save(path)

    result = excel_to_markdown(path, ExcelConfig(
        max_sheets=10, max_nonempty_cells=100, table_chunk_target_tokens=420
    ))

    assert result.status == "success"
    assert "# 设备台账.xlsx" in result.markdown
    assert "## 工作表：核心网设备" in result.markdown
    assert "表格 A1:C2" in result.markdown
    assert "| 设备名称 | 厂家 | 状态 |" in result.markdown
    assert result.summary["table_region_count"] == 1


def test_hidden_sheet_is_marked_and_empty_sheet_is_counted(tmp_path):
    path = tmp_path / "book.xlsx"
    wb = Workbook()
    wb.active.append(["A", "B"])
    hidden = wb.create_sheet("隐藏")
    hidden.sheet_state = "hidden"
    hidden.append(["参数", "值"])
    wb.create_sheet("空表")
    wb.save(path)

    result = excel_to_markdown(path)

    assert "## 工作表：隐藏（隐藏）" in result.markdown
    assert result.summary["skipped_empty_sheet_count"] == 1


def test_workbook_limit_fails_without_silent_truncation(tmp_path):
    path = tmp_path / "large.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["a", "b"])
    ws.append(["1", "2"])
    wb.save(path)

    with pytest.raises(PreprocessingError) as exc:
        excel_to_markdown(path, ExcelConfig(
            max_sheets=10, max_nonempty_cells=2, table_chunk_target_tokens=420
        ))
    assert exc.value.code == "excel_limits_exceeded"
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_preprocessing.py -q
```

Expected: FAIL because `excel_to_markdown` does not exist.

- [ ] **Step 3: Implement orchestration, escaping, and token-aware row chunks**

Expose:

```python
def excel_to_markdown(
    path: Path,
    config: ExcelConfig | None = None,
) -> ExcelParseResult:
    cfg = config or ExcelConfig()
    workbook = read_excel_workbook(path)
    if len(workbook.sheets) > cfg.max_sheets:
        raise PreprocessingError("excel_limits_exceeded", "Excel workbook has too many sheets")

    warnings = list(workbook.warnings)
    sections = [f"# {_escape_heading(path.name)}"]
    parsed_sheet_count = 0
    skipped_empty_sheet_count = 0
    table_region_count = 0
    nonempty_cell_count = 0

    for sheet in workbook.sheets:
        warnings.extend(sheet.formula_warnings)
        sheet_cells = sum(bool(value) for row in sheet.rows for value in row)
        nonempty_cell_count += sheet_cells
        if nonempty_cell_count > cfg.max_nonempty_cells:
            raise PreprocessingError("excel_limits_exceeded", "Excel workbook has too many nonempty cells")
        if sheet_cells == 0:
            skipped_empty_sheet_count += 1
            continue
        parsed_sheet_count += 1
        suffix = "（隐藏）" if sheet.hidden else ""
        sections.append(f"\n## 工作表：{_escape_heading(sheet.name)}{suffix}")
        for region in detect_regions(sheet.rows):
            table_region_count += 1
            sections.extend(_render_region(region, cfg.table_chunk_target_tokens))

    if parsed_sheet_count == 0:
        raise PreprocessingError("excel_no_usable_content", "Excel workbook has no usable cells")

    summary = {
        "sheet_count": len(workbook.sheets),
        "parsed_sheet_count": parsed_sheet_count,
        "skipped_empty_sheet_count": skipped_empty_sheet_count,
        "table_region_count": table_region_count,
        "nonempty_cell_count": nonempty_cell_count,
    }
    return ExcelParseResult(
        markdown="\n".join(sections).strip() + "\n",
        status="partial" if warnings else "success",
        source_format=workbook.source_format,
        summary=summary,
        warnings=tuple(warnings),
    )
```

Implement `_escape_cell`, `_escape_heading`, `_render_region`, and `_chunk_rows` in the same module. `_chunk_rows` must measure the header plus candidate rows with the existing `token_count` utility, target the configured token count, repeat headers in every chunk, and never silently drop a row. Render a one-column region as bullet/paragraph content instead of a one-column Markdown table.

- [ ] **Step 4: Add deterministic repeat and chunk-boundary assertions**

Extend `test_excel_preprocessing.py` with a 100-row workbook and assert:

```python
first = excel_to_markdown(path, config)
second = excel_to_markdown(path, config)
assert first.markdown == second.markdown
assert first.markdown.count("| 设备名称 | 状态 |") > 1
for row_no in range(100):
    assert first.markdown.count(f"设备-{row_no}") == 1
```

- [ ] **Step 5: Run preprocessing tests**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_reader.py knowledge_mining/tests/test_excel_structure.py knowledge_mining/tests/test_excel_preprocessing.py -q
```

Expected: all Excel unit tests pass.

- [ ] **Step 6: Commit Task 4**

```powershell
git add knowledge_mining/mining/ingestion/excel_preprocessing.py knowledge_mining/tests/test_excel_preprocessing.py
git commit -m "feat(mining): convert Excel workbooks to Markdown"
```

---

### Task 5: Integrate Excel with discovery, hashes, MIME, ZIP, and the Markdown pipeline

**Files:**

- Modify: `knowledge_mining/mining/ingestion/__init__.py`
- Modify: `knowledge_mining/mining/contracts/models.py`
- Modify: `knowledge_mining/tests/test_excel_preprocessing.py`
- Modify: `knowledge_mining/tests/kb/test_documents.py`

- [ ] **Step 1: Write failing ingestion integration tests**

Append:

```python
def test_ingest_xlsx_routes_markdown_and_preserves_source_metadata(tmp_path):
    path = tmp_path / "inventory.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["name", "status"])
    ws.append(["AMF", "active"])
    wb.save(path)

    from knowledge_mining.mining.ingestion import get_mime_type, ingest_directory
    docs, summary = ingest_directory(tmp_path)

    assert len(docs) == 1
    assert docs[0].file_type == "markdown"
    assert docs[0].source_uri == str(path)
    assert docs[0].metadata_json["source_format"] == "xlsx"
    assert docs[0].metadata_json["preprocess_status"] == "success"
    assert docs[0].metadata_json["excel_summary"]["table_region_count"] == 1
    assert get_mime_type("xlsx") == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert summary["preprocessed_excels"] == 1
```

Add a ZIP integration test in `knowledge_mining/tests/kb/test_documents.py` that uploads a ZIP containing `nested/inventory.xlsx`, verifies the extracted document identity exists, and then calls `ingest_directory` on the KB directory to verify it is discovered.

- [ ] **Step 2: Run tests and verify Excel is skipped**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_preprocessing.py::test_ingest_xlsx_routes_markdown_and_preserves_source_metadata knowledge_mining/tests/kb/test_documents.py -q
```

Expected: the ingestion assertion fails because `.xlsx` is not yet in `_EXTENSION_MAP`.

- [ ] **Step 3: Wire Excel into `ingest_directory`**

Add `.xls` and `.xlsx` to `_EXTENSION_MAP`, add `EXCEL_EXTENSIONS`, add both MIME values, and add `preprocessed_excels` to the summary. In the main branch order, handle Excel before plain-text fallback:

```python
elif ext in EXCEL_EXTENSIONS:
    try:
        excel = excel_to_markdown(file_path)
        content = excel.markdown
        file_type = "markdown"
        summary["parsed_documents"] += 1
        summary["preprocessed_excels"] += 1
        metadata_json.update({
            "source_format": excel.source_format,
            "preprocess_status": excel.status,
            "preprocess_warnings": [item.as_dict() for item in excel.warnings],
            "excel_summary": excel.summary,
        })
    except PreprocessingError as exc:
        content = ""
        summary["unparsed_documents"] += 1
        metadata_json.update({"source_format": ext.lstrip("."), **exc.as_metadata()})
```

Keep `source_uri`, `relative_path`, and `file_name` pointed at the original Excel file. Compute `raw_content_hash` from original bytes and `normalized_content_hash` from generated Markdown when successful.

- [ ] **Step 4: Run ingestion and existing multiformat regression tests**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_preprocessing.py knowledge_mining/tests/test_multiformat_and_splitting.py knowledge_mining/tests/test_doc_preprocessing.py knowledge_mining/tests/kb/test_documents.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit Task 5**

```powershell
git add knowledge_mining/mining/ingestion/__init__.py knowledge_mining/mining/contracts/models.py knowledge_mining/tests/test_excel_preprocessing.py knowledge_mining/tests/kb/test_documents.py
git commit -m "feat(mining): ingest Excel as Markdown knowledge input"
```

---

### Task 6: Make Word/Excel preprocessing failures first-class run-document failures

**Files:**

- Modify: `knowledge_mining/mining/ingestion/doc_preprocessing.py`
- Modify: `knowledge_mining/mining/ingestion/__init__.py`
- Modify: `knowledge_mining/mining/jobs/run.py`
- Modify: `knowledge_mining/mining/pipeline.py`
- Modify: `knowledge_mining/tests/test_doc_preprocessing.py`
- Modify: `knowledge_mining/tests/test_pipeline_operators.py`
- Modify: `knowledge_mining/tests/test_mining_run_submission.py`

- [ ] **Step 1: Write failing stable-error and failure-status tests**

Add to `test_doc_preprocessing.py`:

```python
def test_doc_to_docx_missing_converter_has_stable_error(tmp_path, monkeypatch):
    src = tmp_path / "legacy.doc"
    src.write_bytes(b"legacy")
    monkeypatch.setattr(dp, "_convert_with_soffice", lambda *args: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(dp, "_convert_with_word_com", lambda *args: (_ for _ in ()).throw(RuntimeError("COM unavailable")))

    with pytest.raises(PreprocessingError) as exc:
        dp.doc_to_docx(src)
    assert exc.value.code == "doc_converter_unavailable"
```

Add to `TestDbWriteStageSkip` in `test_pipeline_operators.py`:

```python
def test_preprocess_failure_is_failed_not_skipped(self):
    from unittest.mock import MagicMock
    from knowledge_mining.mining.contracts.models import RawFileData
    from knowledge_mining.mining.pipeline import db_write_stage, DocumentContext, PipelineConfig

    tracker = MagicMock()
    cfg = PipelineConfig(domain="test-domain", tracker=tracker, runtime_db=MagicMock())
    raw = RawFileData(
        file_path="bad.xlsx", relative_path="bad.xlsx", file_name="bad.xlsx",
        file_type="markdown", content="", raw_content_hash="raw", normalized_content_hash="raw",
        metadata_json={
            "preprocess_status": "failed",
            "preprocess_error_code": "excel_corrupt_file",
            "preprocess_error": "Unable to read Excel workbook: bad.xlsx",
        },
    )
    result = db_write_stage(DocumentContext(raw_file=raw, run_document_id="rd1"), cfg)
    tracker.fail_document.assert_called_once_with("rd1", "Unable to read Excel workbook: bad.xlsx")
    tracker.skip_document.assert_not_called()
    assert result.error == "Unable to read Excel workbook: bad.xlsx"
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_doc_preprocessing.py knowledge_mining/tests/test_pipeline_operators.py::TestDbWriteStageSkip -q
```

Expected: stable Word exception and failed-not-skipped assertions fail.

- [ ] **Step 3: Raise typed Word preprocessing errors**

Import `PreprocessingError` in `doc_preprocessing.py`. Distinguish these terminal cases:

```python
raise PreprocessingError(
    "doc_converter_unavailable",
    f"No .doc converter is available for {src.name}; install LibreOffice or save as .docx",
)
```

If a discovered converter runs but returns an error or no `.docx`, raise:

```python
raise PreprocessingError(
    "doc_conversion_failed",
    f"Failed to convert legacy Word document: {src.name}",
)
```

In `ingestion/__init__.py`, preserve `exc.as_metadata()` for Word failures just as for Excel failures.

- [ ] **Step 4: Copy structured preprocessing metadata into run documents**

In `jobs/run.py`, replace the one-field `_pre_err` copy with:

```python
for key in (
    "preprocess_status",
    "preprocess_error_code",
    "preprocess_error",
    "preprocess_warnings",
    "excel_summary",
):
    if key in (doc.metadata_json or {}):
        run_document_metadata[key] = doc.metadata_json[key]
```

Do not add database columns.

Add a small `_log_preprocess_diagnostics` helper and call it immediately after
registering preprocessing diagnostics. Emit a structured warning without cell
contents:

```python
def _log_preprocess_diagnostics(
    *, run_id: str, run_document_id: str, document_key: str, metadata: dict[str, Any]
) -> None:
    if metadata.get("preprocess_status") not in {"partial", "failed"}:
        return
    logger.warning(
        "document_preprocess status=%s code=%s run_id=%s run_document_id=%s document_key=%s warning_count=%s",
        metadata.get("preprocess_status"),
        metadata.get("preprocess_error_code"),
        run_id,
        run_document_id,
        document_key,
        len(metadata.get("preprocess_warnings") or []),
    )
```

Add this test to `test_mining_run_submission.py`:

```python
def test_preprocess_log_has_identifiers_but_not_cell_content(caplog):
    from knowledge_mining.mining.jobs import run as run_job

    run_job._log_preprocess_diagnostics(
        run_id="run-1",
        run_document_id="rd-1",
        document_key="doc:/bad.xlsx",
        metadata={
            "preprocess_status": "failed",
            "preprocess_error_code": "excel_corrupt_file",
            "preprocess_warnings": [],
            "sentinel_cell_content": "SECRET-CELL-VALUE",
        },
    )
    text = caplog.text
    assert "run-1" in text and "rd-1" in text
    assert "excel_corrupt_file" in text
    assert "SECRET-CELL-VALUE" not in text
```

- [ ] **Step 5: Fail fatal preprocessing in `db_write_stage`**

When `ctx.tree is None`, classify the reason. If it is `preprocess_failed`, call `tracker.fail_document`, commit, and return `ctx.with_updates(error=detail or reason)`. Preserve existing skip behavior for `unsupported_type`, `empty_file`, `no_segments`, and `parse_no_tree`.

Update `_classify_parse_skip` so it reads the structured fields first:

```python
if meta.get("preprocess_status") == "failed":
    return "preprocess_failed", str(meta.get("preprocess_error") or "preprocessing failed")
```

- [ ] **Step 6: Run Word, pipeline, and run-status regression tests**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_doc_preprocessing.py knowledge_mining/tests/test_pipeline_operators.py::TestDbWriteStageSkip knowledge_mining/tests/test_mining_run_submission.py::test_preprocess_log_has_identifiers_but_not_cell_content knowledge_mining/tests/test_run_status.py knowledge_mining/tests/test_pipeline_ordering.py -q
```

Expected: all pass; no database migration is required.

- [ ] **Step 7: Commit Task 6**

```powershell
git add knowledge_mining/mining/ingestion/doc_preprocessing.py knowledge_mining/mining/ingestion/__init__.py knowledge_mining/mining/jobs/run.py knowledge_mining/mining/pipeline.py knowledge_mining/tests/test_doc_preprocessing.py knowledge_mining/tests/test_pipeline_operators.py knowledge_mining/tests/test_mining_run_submission.py
git commit -m "fix(mining): expose document preprocessing failures"
```

---

### Task 7: Project preprocessing metadata through run APIs

**Files:**

- Modify: `knowledge_mining/mining/api/routes/runs.py`
- Modify: `knowledge_mining/tests/test_api_runs.py`

- [ ] **Step 1: Write failing metadata projection tests**

Add a focused helper test:

```python
def test_expand_preprocess_metadata_is_backward_compatible():
    result = runs._expand_preprocess_metadata({
        "preprocess_status": "partial",
        "preprocess_error_code": None,
        "preprocess_warnings": [{
            "code": "excel_formula_cache_missing",
            "message": "公式没有已保存的计算结果",
            "sheet_name": "汇总",
            "cell_range": "F18",
        }],
        "excel_summary": {"sheet_count": 2, "table_region_count": 3},
    })
    assert result["preprocess_status"] == "partial"
    assert result["error_code"] is None
    assert result["warnings"][0]["sheet_name"] == "汇总"
    assert result["excel_summary"]["table_region_count"] == 3

    legacy = runs._expand_preprocess_metadata({"preprocess_error": "legacy failure"})
    assert legacy["preprocess_status"] == "failed"
    assert legacy["error_detail"] == "legacy failure"
```

- [ ] **Step 2: Run and verify helper absence**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_api_runs.py -q
```

Expected: FAIL because `_expand_preprocess_metadata` does not exist.

- [ ] **Step 3: Implement one projection helper and reuse it in list/detail**

```python
def _expand_preprocess_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    legacy_error = meta.get("preprocess_error")
    status = meta.get("preprocess_status")
    if status is None and legacy_error:
        status = "failed"
    warnings = meta.get("preprocess_warnings") or []
    if not isinstance(warnings, list):
        warnings = []
    summary = meta.get("excel_summary")
    if not isinstance(summary, dict):
        summary = None
    return {
        "preprocess_status": status,
        "error_code": meta.get("preprocess_error_code"),
        "error_detail": legacy_error,
        "warnings": warnings,
        "excel_summary": summary,
    }
```

Call this helper in both `list_run_documents` and `get_run_document`. Keep existing `skip_reason`, `skip_reason_detail`, and `file_size` fields so old UI clients remain compatible.

- [ ] **Step 4: Run API tests**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_api_runs.py knowledge_mining/tests/test_mining_run_trace.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit Task 7**

```powershell
git add knowledge_mining/mining/api/routes/runs.py knowledge_mining/tests/test_api_runs.py
git commit -m "feat(api): expose preprocessing diagnostics"
```

---

### Task 8: Display supported formats and preprocessing diagnostics in the UI

**Files:**

- Create: `kb-ui/src/components/kb/PreprocessNotice.vue`
- Create: `kb-ui/src/components/kb/__tests__/PreprocessNotice.spec.ts`
- Modify: `kb-ui/src/types/index.ts`
- Modify: `kb-ui/src/views/kb/KbRunDocDetailView.vue`
- Modify: `kb-ui/src/components/kb/KbFileManager.vue`

- [ ] **Step 1: Write failing component tests**

```typescript
// kb-ui/src/components/kb/__tests__/PreprocessNotice.spec.ts
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PreprocessNotice from '../PreprocessNotice.vue'

describe('PreprocessNotice', () => {
  it('shows partial Excel warnings and source locations', () => {
    const wrapper = mount(PreprocessNotice, { props: {
      status: 'partial',
      warnings: [{
        code: 'excel_formula_cache_missing',
        message: '公式没有已保存的计算结果',
        sheet_name: '汇总',
        cell_range: 'F18',
      }],
      summary: { sheet_count: 2, parsed_sheet_count: 2, table_region_count: 4 },
    } })
    expect(wrapper.text()).toContain('部分解析成功')
    expect(wrapper.text()).toContain('汇总')
    expect(wrapper.text()).toContain('F18')
    expect(wrapper.text()).toContain('4')
  })

  it('shows an actionable fatal error', () => {
    const wrapper = mount(PreprocessNotice, { props: {
      status: 'failed',
      errorCode: 'doc_converter_unavailable',
      errorDetail: 'No .doc converter is available',
    } })
    expect(wrapper.text()).toContain('解析失败')
    expect(wrapper.text()).toContain('LibreOffice')
  })
})
```

- [ ] **Step 2: Run and verify component absence**

Run:

```powershell
npm --prefix kb-ui test -- src/components/kb/__tests__/PreprocessNotice.spec.ts
```

Expected: FAIL because `PreprocessNotice.vue` does not exist.

- [ ] **Step 3: Add TypeScript contracts**

Add:

```typescript
export interface PreprocessWarning {
  code: string
  message: string
  sheet_name?: string | null
  cell_range?: string | null
}

export interface ExcelPreprocessSummary {
  sheet_count?: number
  parsed_sheet_count?: number
  skipped_empty_sheet_count?: number
  table_region_count?: number
  nonempty_cell_count?: number
}
```

Extend `MiningRunDocument`:

```typescript
preprocess_status?: 'success' | 'partial' | 'failed' | null
error_code?: string | null
error_detail?: string | null
warnings?: PreprocessWarning[]
excel_summary?: ExcelPreprocessSummary | null
```

- [ ] **Step 4: Implement the focused notice component**

`PreprocessNotice.vue` must:

- render nothing when no preprocessing fields exist;
- render success, warning, and failure visual states;
- map `doc_converter_unavailable` to an instruction mentioning offline LibreOffice installation or conversion to `.docx`;
- display warning code, message, worksheet, and cell range;
- display sheet/region/cell counts when present;
- escape text through normal Vue interpolation, never `v-html`.

- [ ] **Step 5: Integrate into document detail and upload picker**

Render immediately after the existing error banner:

```vue
<PreprocessNotice
  :status="miningStore.currentDocument.preprocess_status"
  :error-code="miningStore.currentDocument.error_code"
  :error-detail="miningStore.currentDocument.error_detail"
  :warnings="miningStore.currentDocument.warnings"
  :summary="miningStore.currentDocument.excel_summary"
/>
```

Add the component import. Add this `accept` value to the KB `el-upload` control:

```vue
accept=".md,.markdown,.txt,.html,.htm,.pdf,.doc,.docx,.xls,.xlsx,.zip,.chm,.hdx"
```

Add a concise nearby supported-format hint including Word and Excel.

- [ ] **Step 6: Run UI tests and production type/build checks**

Run:

```powershell
npm --prefix kb-ui test -- src/components/kb/__tests__/PreprocessNotice.spec.ts
npm --prefix kb-ui run build
```

Expected: tests pass and the Vue TypeScript production build completes.

- [ ] **Step 7: Commit Task 8**

```powershell
git add kb-ui/src/components/kb/PreprocessNotice.vue kb-ui/src/components/kb/__tests__/PreprocessNotice.spec.ts kb-ui/src/types/index.ts kb-ui/src/views/kb/KbRunDocDetailView.vue kb-ui/src/components/kb/KbFileManager.vue
git commit -m "feat(kb-ui): show document preprocessing diagnostics"
```

---

### Task 9: Package offline dependencies and run end-to-end verification

**Files:**

- Modify: `docker/Dockerfile`
- Create: `docs/deployment/offline-document-dependencies.md`
- Modify: `knowledge_mining/README.md`

- [ ] **Step 1: Update the runtime image**

Add `libreoffice-writer` to the runtime `apt-get install` list. Add the exact Python constraints from Task 1 to the runtime pip install list:

```dockerfile
"openpyxl>=3.1,<4" \
"xlrd>=2.0,<3"
```

Do not install `xlwt` in the runtime image. The exported `cmkb.tar` must contain LibreOffice and Python Excel readers so the target server does not download them.

- [ ] **Step 2: Write the offline deployment guide**

The guide must include executable preparation and installation commands:

```bash
# On a connected Linux build machine matching production Python and architecture
python -m pip download --only-binary=:all: \
  --dest wheelhouse \
  "openpyxl>=3.1,<4" "xlrd>=2.0,<3"

# On an offline host, if Python services are installed outside Docker
python -m pip install --no-index --find-links wheelhouse \
  "openpyxl>=3.1,<4" "xlrd>=2.0,<3"

# Preferred deployment: build and export on the connected build machine
bash deploy-build.sh

# Offline server
docker load -i cmkb.tar
bash deploy-server.sh
```

Document that LibreOffice RPM/DEB dependencies must match the Linux distribution when not using the exported Docker image. Include capability checks:

```bash
python -c "import openpyxl, xlrd; print(openpyxl.__version__, xlrd.__version__)"
command -v soffice || command -v libreoffice
```

State explicitly that no runtime component downloads dependencies or contacts a document-conversion cloud service.

- [ ] **Step 3: Update supported-format documentation**

In `knowledge_mining/README.md`, document `.doc/.docx/.xls/.xlsx`, Markdown intermediate behavior, formula cached-value behavior, unsupported images/charts/macros/password protection, and API-visible partial/failure diagnostics.

- [ ] **Step 4: Run the complete backend verification suite for affected areas**

Run:

```powershell
python -m pytest knowledge_mining/tests/test_excel_config.py knowledge_mining/tests/test_excel_reader.py knowledge_mining/tests/test_excel_structure.py knowledge_mining/tests/test_excel_preprocessing.py knowledge_mining/tests/test_doc_preprocessing.py knowledge_mining/tests/test_multiformat_and_splitting.py knowledge_mining/tests/test_pipeline_operators.py knowledge_mining/tests/test_api_runs.py knowledge_mining/tests/test_run_status.py knowledge_mining/tests/kb/test_documents.py knowledge_mining/tests/kb/test_mining.py -q
```

Expected: all selected backend tests pass with no PostgreSQL-only test unexpectedly enabled.

- [ ] **Step 5: Run UI verification**

Run:

```powershell
npm --prefix kb-ui test
npm --prefix kb-ui run build
```

Expected: all Vitest tests pass and production build succeeds.

- [ ] **Step 6: Verify no database changes and inspect the final diff**

Run:

```powershell
git diff --name-only HEAD~8
git diff --check
git status --short
```

Expected:

- no path under `databases/` appears;
- `git diff --check` reports no whitespace errors;
- only the user's pre-existing staged/untracked files remain outside the feature commits.

- [ ] **Step 7: Build the runtime image when Docker is available**

Run:

```powershell
docker build -f docker/Dockerfile -t coremasterkb-app:excel-input .
docker run --rm coremasterkb-app:excel-input python -c "import openpyxl, xlrd; print('excel readers ok')"
docker run --rm coremasterkb-app:excel-input sh -lc "command -v soffice || command -v libreoffice"
```

Expected: image build succeeds, Python prints `excel readers ok`, and the final command prints a LibreOffice executable path. If Docker is unavailable, record this as the only unverified deployment check; do not claim image verification passed.

- [ ] **Step 8: Commit Task 9**

```powershell
git add docker/Dockerfile docs/deployment/offline-document-dependencies.md knowledge_mining/README.md
git commit -m "docs: package offline Word and Excel dependencies"
```

---

## Final acceptance checklist

- [ ] `.doc`, `.docx`, `.xls`, and `.xlsx` can be selected, uploaded, and discovered for KB mining.
- [ ] `.xls/.xlsx` become deterministic in-memory Markdown and reuse the existing Markdown pipeline.
- [ ] Every table chunk repeats file, sheet, range, and header context.
- [ ] Hidden sheets are marked; empty sheets are counted; charts/images/macros are ignored.
- [ ] Password-protected/corrupt/over-limit workbooks fail with stable API-visible codes.
- [ ] Partial workbook results retain usable content and expose sheet/cell warnings.
- [ ] Legacy `.doc` reports missing LibreOffice as `doc_converter_unavailable`.
- [ ] One bad document does not stop other documents in the run.
- [ ] Offline Linux installation works from wheelhouse or the exported Docker image.
- [ ] No database schema, migration, table, or column changes are present.
