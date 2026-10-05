import json
import zipfile
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from experiments.summary_types import Summary
from pydantic import TypeAdapter
from scripts import verify_release as verifier
from scripts.verify_guard_wheel import JsonValue
from tests.release.test_release_checkout import checkout
from tests.release.test_release_contract import complete_proof
from tests.release.test_release_records import evidence
from tests.release.test_release_reproduction import summary


def manifest_fixture(repository: Path, output: Path) -> verifier.ReleaseManifest:
    sha = checkout(repository)
    records = tuple(replace(record, commit_sha=sha) for record in evidence(output))
    ci = json.dumps(
        {
            "headSha": sha,
            "status": "completed",
            "conclusion": "success",
            "jobs": [
                {"name": name, "status": "completed", "conclusion": "success"}
                for name in verifier.CI_JOBS
            ],
        }
    ).encode()
    builds: list[verifier.BuildArtifact] = []
    for kind, package in (
        ("guard_wheel", "rag_access_guard"),
        ("api_wheel", "rag_access_guard_api"),
    ):
        path = output / f"{package}-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"{package}/__init__.py", "")
            archive.writestr(
                f"{package}-0.1.0.dist-info/METADATA",
                f"Metadata-Version: 2.1\nName: {package.replace('_', '-')}\nVersion: 0.1.0\n",
            )
        builds.append(verifier.BuildArtifact(kind, path, sha256(path.read_bytes()).hexdigest()))
    wheel = json.dumps(
        {
            "wheel_sha256": builds[0].sha256,
            "exit_code": 0,
            "public_api_smoke": dict.fromkeys(
                (
                    "prepare_allowed",
                    "release_allowed",
                    "read_allowed",
                    "read_revoked",
                    "release_revoked",
                    "prepare_revoked",
                    "unknown_denied",
                    "other_principal_denied",
                ),
                True,
            ),
        }
    ).encode()
    updated: list[verifier.EvidenceRecord] = []
    for record in records:
        if record.kind in {"ci", "wheel_install"}:
            raw = ci if record.kind == "ci" else wheel
            _ = record.path.write_bytes(raw)
            updated.append(replace(record, sha256=sha256(raw).hexdigest()))
        else:
            updated.append(record)
    return verifier.ReleaseManifest(
        replace(complete_proof(), commit_sha=sha, remote_sha=sha, ci_sha=sha),
        tuple(updated),
        tuple(builds),
        output / "original",
        output / "repeated",
    )


@pytest.mark.parametrize("damage", ["missing_build", "build_hash", "wheel_report", "ci", "records"])
def test_manifest_refuses_unbound_evidence(tmp_path: Path, damage: str) -> None:
    repository, output = tmp_path / "repository", tmp_path / "evidence"
    repository.mkdir()
    output.mkdir()
    manifest = manifest_fixture(repository, output)
    match damage:
        case "missing_build":
            manifest = replace(manifest, builds=manifest.builds[:-1])
        case "build_hash":
            manifest = replace(
                manifest, builds=(replace(manifest.builds[0], sha256="0" * 64), manifest.builds[1])
            )
        case "wheel_report" | "ci":
            kind = "wheel_install" if damage == "wheel_report" else "ci"
            record = next(item for item in manifest.records if item.kind == kind)
            raw = b'{"wheel_sha256":"wrong","exit_code":0}' if kind == "wheel_install" else b"{}"
            _ = record.path.write_bytes(raw)
            manifest = replace(
                manifest,
                records=tuple(
                    replace(item, sha256=sha256(raw).hexdigest()) if item.kind == kind else item
                    for item in manifest.records
                ),
            )
        case "records":
            manifest = replace(manifest, records=manifest.records[:-1])
        case _:
            pytest.fail("Unknown damage")
    assert verifier.validate_manifest(manifest, repository)


def test_release_command_refuses_missing_manifest(tmp_path: Path) -> None:
    assert (
        verifier.main(["--manifest", str(tmp_path / "missing.json"), "--repository", str(tmp_path)])
        == 2
    )


@pytest.mark.parametrize("damage", ["absent", "missing", "false", "integer", "extra"])
def test_build_report_requires_complete_strict_smoke(tmp_path: Path, damage: str) -> None:
    repository, output = tmp_path / "repository", tmp_path / "evidence"
    repository.mkdir()
    output.mkdir()
    manifest = manifest_fixture(repository, output)
    record = next(item for item in manifest.records if item.kind == "wheel_install")
    report = TypeAdapter(dict[str, JsonValue]).validate_json(record.path.read_bytes())
    smoke = report["public_api_smoke"]
    assert isinstance(smoke, dict)
    if damage == "absent":
        del report["public_api_smoke"]
    elif damage == "missing":
        del smoke["read_revoked"]
    elif damage == "extra":
        smoke["foreign_check"] = True
    else:
        smoke["read_revoked"] = False if damage == "false" else 1
    raw = json.dumps(report).encode()
    _ = record.path.write_bytes(raw)
    updated = replace(record, sha256=sha256(raw).hexdigest())
    try:
        errors = verifier.validate_builds(manifest.builds, updated)
    except ValueError:
        return
    assert errors


def test_release_command_accepts_complete_linked_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, output = tmp_path / "repository", tmp_path / "evidence"
    repository.mkdir()
    output.mkdir()
    manifest = manifest_fixture(repository, output)
    calls: list[str] = []

    def verify_runs(original: Path, repeated: Path, repo: Path, sha: str) -> Summary:
        assert (original, repeated, repo, sha) == (
            manifest.original_run,
            manifest.repeated_run,
            repository,
            manifest.proof.commit_sha,
        )
        calls.append(sha)
        return summary(output)

    monkeypatch.setattr(verifier, "verify_runs", verify_runs)
    path = output / "release.json"
    _ = path.write_bytes(TypeAdapter(verifier.ReleaseManifest).dump_json(manifest))
    assert verifier.main(["--manifest", str(path), "--repository", str(repository)]) == 0
    assert calls == [manifest.proof.commit_sha]


@pytest.mark.parametrize("damage", ["not_zip", "wrong_package"])
def test_build_evidence_requires_an_api_wheel(tmp_path: Path, damage: str) -> None:
    repository, output = tmp_path / "repository", tmp_path / "evidence"
    repository.mkdir()
    output.mkdir()
    manifest = manifest_fixture(repository, output)
    api = manifest.builds[1]
    if damage == "not_zip":
        _ = api.path.write_bytes(b"not a wheel")
    else:
        _ = api.path.write_bytes(manifest.builds[0].path.read_bytes())
    builds = (manifest.builds[0], replace(api, sha256=sha256(api.path.read_bytes()).hexdigest()))
    wheel_record = next(record for record in manifest.records if record.kind == "wheel_install")
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        _ = verifier.validate_builds(builds, wheel_record)
