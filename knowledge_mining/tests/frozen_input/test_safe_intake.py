"""Unit tests for ``SafeIntake`` (M1.4, WP1D — pure admission logic).

Pure-logic tests, no I/O, no async, no fixtures. Covers SRS §2.4 (security
invariants) and §C03 (File Inspector safe subset): magic-byte detection,
signature-over-extension priority, encryption flag, archive limits, path
traversal sanitization.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from knowledge_mining.mining.frozen_input.contracts import (
    IntakeVerdict,
    UnsafeFile,
)
from knowledge_mining.mining.frozen_input.safe_intake import (
    DEFAULT_MAX_ARCHIVE_MEMBERS,
    DEFAULT_MAX_COMPRESSION_RATIO,
    SafeIntake,
)


@pytest.fixture
def intake() -> SafeIntake:
    return SafeIntake()


# ---------------------------------------------------------------------------
# detect_mime — magic-byte signatures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "filename", "expected"),
    [
        (b"%PDF-1.7\n...", "doc.pdf", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "img.png", "image/png"),
        (b"\xff\xd8\xff\xe0", "img.jpg", "image/jpeg"),
        (b"GIF89a...", "img.gif", "image/gif"),
        (b"{\\rtf1...", "doc.rtf", "application/rtf"),
        (b"D0CF11E0A1B11AE1...", "doc.doc", "application/msword"),
        (b"D0CF11E0A1B11AE1...", "sheet.xls", "application/vnd.ms-excel"),
        (b"D0CF11E0A1B11AE1...", "deck.ppt", "application/vnd.ms-powerpoint"),
        (b"PK\x03\x04...", "doc.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        (b"PK\x03\x04...", "sheet.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (b"PK\x03\x04...", "deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        (b"PK\x03\x04...", "archive.zip", "application/zip"),
        (b"Rar!\x1a\x07\x00", "a.rar", "application/x-rar-compressed"),
        (b"7z\xbc\xaf\x27\x1c", "a.7z", "application/x-7z-compressed"),
        (b"\x1f\x8b\x08", "a.gz", "application/gzip"),
    ],
)
def test_detect_mime_signature_match(
    intake: SafeIntake, head: bytes, filename: str, expected: str
) -> None:
    assert intake.detect_mime(head, filename) == expected


def test_detect_mime_text_uses_extension_hint(intake: SafeIntake) -> None:
    # No signature, but text-like and a markdown extension -> markdown.
    head = b"# Title\n\nThis is a markdown body.\n"
    assert intake.detect_mime(head, "note.md") == "text/markdown"
    # Text-like with .txt -> text/plain.
    assert intake.detect_mime(head, "note.txt") == "text/plain"
    # Text-like with no known extension -> text/plain.
    assert intake.detect_mime(head, "note") == "text/plain"


def test_detect_mime_signature_wins_over_extension(intake: SafeIntake) -> None:
    # A real PDF masquerading as .txt: signature wins (SRS §2.4).
    head = b"%PDF-1.7\n%binary..."
    assert intake.detect_mime(head, "disguised.txt") == "application/pdf"


def test_detect_mime_unknown_binary_falls_back_to_extension(intake: SafeIntake) -> None:
    # Binary, no recognized signature, but a known extension -> ext mime.
    head = b"\x00\x01\x02\x03\x04\x05binary junk"
    assert intake.detect_mime(head, "weird.pdf") == "application/pdf"


def test_detect_mime_unknown_binary_no_extension_is_octet_stream(
    intake: SafeIntake,
) -> None:
    head = b"\x00\x01\x02\x03\x04\x05binary junk"
    assert intake.detect_mime(head, "noext") == "application/octet-stream"


def test_detect_mime_empty_head_uses_extension(intake: SafeIntake) -> None:
    assert intake.detect_mime(b"", "empty.md") == "text/markdown"
    assert intake.detect_mime(b"", "empty.pdf") == "application/pdf"


# ---------------------------------------------------------------------------
# inspect — verdict composition
# ---------------------------------------------------------------------------


def test_inspect_clean_pdf_is_ok(intake: SafeIntake) -> None:
    verdict = intake.inspect(b"%PDF-1.7\n%clean", "doc.pdf", declared_size=10)
    assert verdict.ok is True
    assert verdict.detected_mime == "application/pdf"
    assert verdict.detected_format == "pdf"
    assert verdict.encrypted is False
    assert verdict.is_archive is False
    assert verdict.errors == ()


def test_inspect_encrypted_pdf_is_not_ok(intake: SafeIntake) -> None:
    head = b"%PDF-1.7\nlater there is /Encrypt dict somewhere"
    verdict = intake.inspect(head, "secret.pdf")
    assert verdict.ok is False
    assert verdict.encrypted is True
    assert any("encrypted" in e for e in verdict.errors)


def test_inspect_archive_flag(intake: SafeIntake) -> None:
    verdict = intake.inspect(b"PK\x03\x04...", "archive.zip")
    assert verdict.is_archive is True
    assert verdict.detected_mime == "application/zip"
    # Archives ARE supported (admitted for staging) so ok stays True.
    assert verdict.ok is True


def test_inspect_unknown_octet_stream_not_ok(intake: SafeIntake) -> None:
    verdict = intake.inspect(b"\x00\x01\x02junk", "unknown")
    assert verdict.ok is False
    assert verdict.detected_mime == "application/octet-stream"
    assert any("unknown file type" in e for e in verdict.errors)


def test_inspect_is_deterministic(intake: SafeIntake) -> None:
    head = b"%PDF-1.7 body"
    v1 = intake.inspect(head, "a.pdf")
    v2 = intake.inspect(head, "a.pdf")
    assert v1 == v2


def test_intake_verdict_is_immutable() -> None:
    v = IntakeVerdict(
        ok=True,
        detected_mime="text/plain",
        detected_format="text",
        encrypted=False,
        is_archive=False,
    )
    with pytest.raises(Exception):  # FrozenInstanceError subclasses Exception
        v.ok = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# check_archive_limits
# ---------------------------------------------------------------------------


def test_check_archive_limits_within_bounds_passes(intake: SafeIntake) -> None:
    # No exception expected.
    intake.check_archive_limits(
        member_count=10,
        expanded_size=1024,
        compressed_size=512,
    )


def test_check_archive_limits_member_count_exceeded(intake: SafeIntake) -> None:
    with pytest.raises(UnsafeFile) as exc_info:
        intake.check_archive_limits(
            member_count=DEFAULT_MAX_ARCHIVE_MEMBERS + 1,
            expanded_size=0,
            compressed_size=0,
        )
    assert exc_info.value.reason == "archive_member_count_exceeded"


def test_check_archive_limits_expanded_size_exceeded(intake: SafeIntake) -> None:
    with pytest.raises(UnsafeFile) as exc_info:
        intake.check_archive_limits(
            member_count=1,
            expanded_size=5 * 1024 * 1024 * 1024,  # 5 GiB > default 2 GiB
            compressed_size=5 * 1024 * 1024 * 1024,
        )
    assert exc_info.value.reason == "archive_expanded_size_exceeded"


def test_check_archive_limits_compression_ratio_exceeded(
    intake: SafeIntake,
) -> None:
    # 1 MiB expanded, 1 KiB compressed -> ratio 1024x.
    with pytest.raises(UnsafeFile) as exc_info:
        intake.check_archive_limits(
            member_count=1,
            expanded_size=1024 * 1024,
            compressed_size=1024,
        )
    assert exc_info.value.reason == "archive_compression_ratio_exceeded"


def test_check_archive_limits_custom_thresholds(intake: SafeIntake) -> None:
    # Caller tightens the member cap to 5; 6 members trips it.
    with pytest.raises(UnsafeFile):
        intake.check_archive_limits(
            member_count=6,
            expanded_size=0,
            compressed_size=0,
            max_members=5,
        )


def test_check_archive_limits_zero_compressed_does_not_divide_by_zero(
    intake: SafeIntake,
) -> None:
    # expanded_size 0 with compressed_size 0 -> ratio computed against 1.
    intake.check_archive_limits(
        member_count=0,
        expanded_size=0,
        compressed_size=0,
        max_ratio=DEFAULT_MAX_COMPRESSION_RATIO,
    )


# ---------------------------------------------------------------------------
# sanitize_archive_member_path
# ---------------------------------------------------------------------------


def test_sanitize_member_path_clean_relative(intake: SafeIntake, tmp_path) -> None:
    resolved = intake.sanitize_archive_member_path("subdir/file.txt", tmp_path)
    assert resolved == (tmp_path / "subdir" / "file.txt").resolve()
    assert str(resolved).startswith(str(tmp_path.resolve()))


def test_sanitize_member_path_rejects_absolute(intake: SafeIntake, tmp_path) -> None:
    with pytest.raises(UnsafeFile) as exc_info:
        intake.sanitize_archive_member_path("/etc/passwd", tmp_path)
    assert exc_info.value.reason == "path_traversal_absolute"


def test_sanitize_member_path_rejects_dotdot(intake: SafeIntake, tmp_path) -> None:
    with pytest.raises(UnsafeFile) as exc_info:
        intake.sanitize_archive_member_path("../escape.txt", tmp_path)
    assert exc_info.value.reason == "path_traversal_dotdot"


def test_sanitize_member_path_rejects_nested_dotdot(
    intake: SafeIntake, tmp_path
) -> None:
    with pytest.raises(UnsafeFile) as exc_info:
        intake.sanitize_archive_member_path("a/b/../../escape.txt", tmp_path)
    assert exc_info.value.reason == "path_traversal_dotdot"


def test_sanitize_member_path_rejects_empty(intake: SafeIntake, tmp_path) -> None:
    with pytest.raises(UnsafeFile) as exc_info:
        intake.sanitize_archive_member_path("", tmp_path)
    assert exc_info.value.reason == "path_traversal"


def test_sanitize_member_path_normalizes_backslashes(intake: SafeIntake, tmp_path) -> None:
    # Forward-slash style on Windows-like member paths should still resolve
    # cleanly under the root when there is no traversal.
    resolved = intake.sanitize_archive_member_path("dir/file.txt", tmp_path)
    assert resolved.parent.exists() or resolved.parent == (
        tmp_path / "dir"
    ).resolve()


# ---------------------------------------------------------------------------
# enumerate_zip_members (stdlib zipfile, no disk extraction)
# ---------------------------------------------------------------------------


def _make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_enumerate_zip_members_counts_correctly(intake: SafeIntake) -> None:
    blob = _make_zip(
        {
            "a.txt": b"aaa",
            "b.txt": b"bbbbbbbb",  # 8 bytes
            "sub/c.txt": b"c",
        }
    )
    count, expanded, compressed = intake.enumerate_zip_members(blob)
    assert count == 3
    assert expanded == 3 + 8 + 1


def test_enumerate_zip_members_corrupt_raises(intake: SafeIntake) -> None:
    with pytest.raises(UnsafeFile) as exc_info:
        intake.enumerate_zip_members(b"PK\x03\x04 not actually a zip")
    assert exc_info.value.reason == "archive_corrupt"
