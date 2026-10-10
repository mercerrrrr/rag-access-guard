import os
import subprocess
import sys
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from shutil import which

import pytest
from tests.helpers.demo_corpus_factory import (
    MARKER,
    corpus_fixture,
    mutate_document,
    mutate_manifest,
)

from rag_access_guard_api.schemas.demo_corpus import CorpusCounts, CorpusValidationReport
from rag_access_guard_api.services.demo_corpus import (
    CorpusValidationError,
    read_bounded,
    validate_corpus,
    validate_report_path,
    write_report,
)


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    return corpus_fixture(tmp_path / "corpus")


@pytest.mark.parametrize(
    ("fault", "code"),
    [
        ("hash", "upload_hash_mismatch"),
        ("missing", "file_unavailable"),
        ("directory", "file_unavailable"),
        ("empty", "size_limit"),
        ("oversize", "size_limit"),
        ("mime", "format_mismatch"),
        ("encoding", "parse_failed"),
        ("suffix", "format_mismatch"),
    ],
)
def test_local_file_defects_fail(manifest: Path, fault: str, code: str) -> None:
    # Given one defective first file, before expensive binary parsers.
    file = manifest.parent / "item-0.txt"
    if fault == "missing":
        file.unlink()
    elif fault == "directory":
        file.unlink()
        file.mkdir()
    elif fault in {"mime", "suffix"}:
        name = "item-0.pdf" if fault == "mime" else "item-0.bin"
        mutate_manifest(manifest, ("document", "relative_path", name))
        _ = file.rename(manifest.parent / name)
    else:
        data = (
            b"wrong"
            if fault == "hash"
            else b""
            if fault == "empty"
            else b"x" * 10_485_761
            if fault == "oversize"
            else b"\xff"
        )
        _ = file.write_bytes(data)
        if fault == "encoding":
            mutate_manifest(manifest, ("document", "upload_sha256", sha256(data).hexdigest()))
    # When / Then
    with pytest.raises(CorpusValidationError, match=code):
        _ = validate_corpus(manifest)


def test_symlink_outside_is_rejected(manifest: Path, tmp_path: Path) -> None:
    # Given a file alias with otherwise correct bytes.
    file = manifest.parent / "item-0.txt"
    outside = tmp_path / "outside.txt"
    _ = file.rename(outside)
    try:
        file.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Symlink errno={error.errno}; winerror={getattr(error, 'winerror', None)}")
    # When / Then
    with pytest.raises(CorpusValidationError, match="path_escape"):
        _ = validate_corpus(manifest)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_junction_outside_is_rejected(manifest: Path, tmp_path: Path) -> None:
    # Given two owned temp directories; a junction requires no symlink privilege.
    outside = tmp_path / "outside"
    outside.mkdir()
    _ = (outside / "file.txt").write_bytes((manifest.parent / "item-0.txt").read_bytes())
    link = manifest.parent / "alias"
    powershell = which("powershell.exe")
    assert powershell is not None
    environment = dict(os.environ, RAG_TEST_LINK=str(link), RAG_TEST_TARGET=str(outside))
    created = subprocess.run(  # noqa: S603 -- fixed command and owned temporary targets
        [
            powershell,
            "-NoProfile",
            "-Command",
            "New-Item -ItemType Junction $env:RAG_TEST_LINK -Target $env:RAG_TEST_TARGET",
        ],
        env=environment,
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert created.returncode == 0
    mutate_manifest(manifest, ("document", "relative_path", "alias/file.txt"))
    # When / Then: rmdir removes only the owned junction, never recursively follows it.
    try:
        with pytest.raises(CorpusValidationError, match="path_escape"):
            _ = validate_corpus(manifest)
    finally:
        link.rmdir()


@pytest.mark.parametrize(
    "text",
    [
        "Ordinary first line\n" + MARKER + "\n" + MARKER,
        MARKER + "\nStep one.",
        MARKER + "\nStep one.\n" + MARKER.replace("Example", "Other"),
        MARKER + " \nStep one.\n" + MARKER,
    ],
)
def test_synthetic_first_full_line_must_recur(manifest: Path, text: str) -> None:
    # Given actual TXT with valid hashes but an invalid machine-consumed marker.
    data = text.encode()
    _ = (manifest.parent / "item-18.txt").write_bytes(data)
    mutate_document(manifest, 18, ("upload_sha256", sha256(data).hexdigest()))
    mutate_document(manifest, 18, ("origin.source_sha256", sha256(data).hexdigest()))
    # When / Then
    with pytest.raises(CorpusValidationError, match="synthetic_marker"):
        _ = validate_corpus(manifest)


@pytest.mark.parametrize("index", [9, 12, 18, 22])
def test_identity_hash_mismatch_is_rejected(manifest: Path, index: int) -> None:
    # Given PDF identity or synthetic bytes of every supported format.
    mutate_document(manifest, index, ("origin.source_sha256", "0" * 64))
    # When / Then
    with pytest.raises(CorpusValidationError, match="source_hash_mismatch"):
        _ = validate_corpus(manifest)


def test_bounded_reader_rejects_growth_without_unbounded_read() -> None:
    # Given a stream larger than the earlier stat could have reported.
    with BytesIO(b"x" * (10_485_760 + 100)) as stream:
        # When / Then: removing the explicit read bound consumes the extra 99 bytes.
        with pytest.raises(CorpusValidationError, match="size_limit"):
            _ = read_bounded(stream)
        assert stream.tell() == 10_485_761


def test_report_race_cannot_overwrite_competing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a competitor creating the same target after the output boundary check.
    path = tmp_path / "race-report.json"
    report = CorpusValidationReport(
        dataset_id="neutral",
        manifest_sha256="0" * 64,
        documents=(),
        counts=CorpusCounts(
            documents=24, official_public=12, synthetic_demo=12, txt=13, docx=6, pdf=5
        ),
    )

    def competing_target(target: Path) -> Path:
        _ = target.write_text("preserve", encoding="utf-8")
        return target

    monkeypatch.setattr(
        "rag_access_guard_api.services.demo_corpus.validate_report_path", competing_target
    )
    # When / Then: opening with w instead of x would silently overwrite the competitor.
    with pytest.raises(CorpusValidationError, match="report_unavailable"):
        write_report(path, report)
    assert path.read_text(encoding="utf-8") == "preserve"


def test_offline_success_uses_real_parsers_without_url_or_database(
    manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given URLs and an opaque external transformation, no fetch or ORM is permitted.
    mutate_document(manifest, 0, ("origin.transformation_revision", "unknown-transform-v7"))

    def forbidden() -> None:
        pytest.fail("Offline inspection attempted a network or database operation")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("sqlalchemy.create_engine", forbidden)
    # When
    report = validate_corpus(manifest)
    # Then independent profile counts and honest unavailable-original status.
    assert report.status == "accepted"
    assert report.defects == ()
    assert report.counts.model_dump() == {
        "documents": 24,
        "official_public": 12,
        "synthetic_demo": 12,
        "txt": 13,
        "docx": 6,
        "pdf": 5,
    }
    assert report.documents[0].source_check == "not_checked_external_original"
    assert report.documents[9].source_check == "matched_upload"
    assert report.documents[12].synthetic_marker_checked is True
    assert "relative_path" not in report.model_dump_json()
    assert "Local text" not in report.model_dump_json()
    output = manifest.parent.parent / "success-report.json"
    write_report(output, report)
    with pytest.raises(CorpusValidationError, match="report_exists"):
        write_report(output, report)


@pytest.mark.parametrize("target", ["existing", "repository", "git-checkout", "missing-parent"])
def test_report_output_is_private_and_exclusive(tmp_path: Path, target: str) -> None:
    # Given explicit output targets; no clobber or implicit mkdir is acceptable.
    path = tmp_path / "report.json"
    if target == "existing":
        _ = path.write_text("preserve", encoding="utf-8")
    elif target == "repository":
        path = Path(__file__).resolve().parents[2] / "forbidden-report.json"
    elif target == "git-checkout":
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        _ = (checkout / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        path = checkout / "report.json"
    else:
        path = tmp_path / "absent" / "report.json"
    # When / Then
    with pytest.raises(CorpusValidationError):
        _ = validate_report_path(path)


@pytest.mark.parametrize("valid", [True, False])
def test_cli_observable_success_and_failure(manifest: Path, tmp_path: Path, *, valid: bool) -> None:
    # Given
    if not valid:
        mutate_manifest(manifest, ("document", "upload_sha256", "0" * 64))
    output = tmp_path / "cli-report.json"
    # When
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and owned paths
        [
            sys.executable,
            "scripts/validate_demo_corpus.py",
            "--manifest",
            str(manifest),
            "--report",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Then
    assert result.returncode == (0 if valid else 1)
    assert output.exists() is valid
    assert not result.stderr or result.stderr.strip() == "corpus_rejected: upload_hash_mismatch"
    if valid:
        report = CorpusValidationReport.model_validate_json(output.read_bytes())
        assert report.status == "accepted"
