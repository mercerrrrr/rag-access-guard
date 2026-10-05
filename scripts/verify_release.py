"""Read-only consistency checks for operator-captured release evidence."""

import argparse
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path
from re import fullmatch

from experiments.run_environment import git_output, git_state
from experiments.verify_reproduction import verify_runs
from pydantic import TypeAdapter
from scripts.release_boundary import validate_public_tree
from scripts.verify_guard_wheel import JsonValue, validate_smoke, validate_wheel

CI_JOBS = frozenset({"Python and API", "Web", "Browser security workflow"})


@dataclass(frozen=True, slots=True)
class ReleaseProof:
    """Each required observation must belong to the same release commit."""

    commit_sha: str
    remote_sha: str
    ci_sha: str | None
    ci_status: str
    wheel_install: str
    demo: str
    reproduction: str


def validate_release(proof: ReleaseProof) -> tuple[str, ...]:
    """Return the unmet release conditions without changing repository state."""
    errors: list[str] = []
    if fullmatch(r"[0-9a-f]{40}", proof.commit_sha) is None:
        errors.append("invalid commit identity")
    if proof.remote_sha != proof.commit_sha:
        errors.append("remote commit differs from release")
    if proof.ci_sha != proof.commit_sha or proof.ci_status != "success":
        errors.append("missing successful CI for commit")
    for name, status in (
        ("wheel installation", proof.wheel_install),
        ("manual demo", proof.demo),
        ("experiment reproduction", proof.reproduction),
    ):
        if status != "pass":
            errors.append(f"missing successful {name}")
    return tuple(errors)


@dataclass(frozen=True, slots=True)
class CiJob:
    """Observed GitHub job status; skipped is not a passing check."""

    name: str
    status: str
    conclusion: str


@dataclass(frozen=True, slots=True)
class CiRun:
    """Only the release-relevant fields of captured GitHub evidence."""

    headSha: str  # noqa: N815 -- GitHub API field.
    status: str
    conclusion: str
    jobs: tuple[CiJob, ...]


def validate_ci(raw: bytes, commit_sha: str) -> tuple[str, ...]:
    """Require completed successful checks for the exact release identity."""
    run = TypeAdapter(CiRun).validate_json(raw, strict=True)
    names = tuple(job.name for job in run.jobs)
    if (
        run.headSha != commit_sha
        or run.status != "completed"
        or run.conclusion != "success"
        or frozenset(names) != CI_JOBS
        or len(names) != len(CI_JOBS)
        or any(job.status != "completed" or job.conclusion != "success" for job in run.jobs)
    ):
        return ("CI does not prove all required successful jobs",)
    return ()


def read_verified(path: Path, digest: str) -> bytes:
    """Read an artifact only when its recorded checksum still matches."""
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != digest:
        message = "Artifact checksum mismatch"
        raise ValueError(message)
    return raw


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Operator-observed outcome linked to an immutable report and commit."""

    kind: str
    commit_sha: str
    status: str
    path: Path
    sha256: str


def validate_records(records: tuple[EvidenceRecord, ...], commit_sha: str) -> tuple[str, ...]:
    """Check report coverage and integrity without interpreting manual observations."""
    required = frozenset({"ci", "wheel_install", "demo", "reproduction", "clean_checks", "review"})
    if frozenset(record.kind for record in records) != required or len(records) != len(required):
        return ("missing or duplicated required reports",)
    errors: list[str] = []
    for record in records:
        if record.commit_sha != commit_sha or record.status != "pass":
            errors.append(f"unverified report: {record.kind}")
        try:
            _ = read_verified(record.path, record.sha256)
        except (OSError, ValueError):
            errors.append(f"missing or changed report: {record.kind}")
    return tuple(errors)


def validate_checkout(repository: Path, proof: ReleaseProof) -> tuple[str, ...]:
    """Require a clean checkout matching the captured local and remote identities."""
    state = git_state(repository)
    errors: list[str] = []
    if state.dirty or state.sha != proof.commit_sha:
        errors.append("checkout is dirty or differs from release")
    if git_output(repository, "rev-parse", "refs/remotes/origin/main") != proof.remote_sha:
        errors.append("remote-tracking reference differs from captured proof")
    paths = tuple(filter(None, git_output(repository, "ls-files", "-z").split("\0")))
    if validate_public_tree(paths):
        errors.append("public tree contains disallowed paths")
    return tuple(errors)


@dataclass(frozen=True, slots=True)
class BuildArtifact:
    """An operator-captured build artifact with its content digest."""

    kind: str
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """Private release evidence; statuses attest observations, not authenticity."""

    proof: ReleaseProof
    records: tuple[EvidenceRecord, ...]
    builds: tuple[BuildArtifact, ...]
    original_run: Path
    repeated_run: Path


def validate_manifest(manifest: ReleaseManifest, repository: Path) -> tuple[str, ...]:
    """Bind checkout, reports, wheel and experiment data without mutating them."""
    proof = manifest.proof
    errors = (
        *validate_release(proof),
        *validate_checkout(repository, proof),
        *validate_records(manifest.records, proof.commit_sha),
    )
    if errors:
        return errors
    try:
        records = {record.kind: record for record in manifest.records}
        ci = records["ci"]
        ci_errors = validate_ci(read_verified(ci.path, ci.sha256), proof.commit_sha)
        if ci_errors:
            return ci_errors
        build_errors = validate_builds(manifest.builds, records["wheel_install"])
        if build_errors:
            return build_errors
        _ = verify_runs(manifest.original_run, manifest.repeated_run, repository, proof.commit_sha)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return ("release artifacts are missing or inconsistent",)
    return ()


@dataclass(frozen=True, slots=True)
class WheelReport:
    """Identity and successful exit captured by the isolated installation command."""

    wheel_sha256: str
    exit_code: int
    public_api_smoke: dict[str, JsonValue]


def validate_builds(
    builds: tuple[BuildArtifact, ...], wheel_record: EvidenceRecord
) -> tuple[str, ...]:
    """Require both built packages and link the tested guard wheel to its bytes."""
    required = {"guard_wheel", "api_wheel"}
    if {build.kind for build in builds} != required or len(builds) != len(required):
        return ("missing or duplicated build artifacts",)
    for build in builds:
        _ = read_verified(build.path, build.sha256)
    guard = next(build for build in builds if build.kind == "guard_wheel")
    digest = validate_wheel(guard.path)
    api = next(build for build in builds if build.kind == "api_wheel")
    with zipfile.ZipFile(api.path) as archive:
        names = archive.namelist()
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or "rag_access_guard_api/__init__.py" not in names:
            message = "Invalid API wheel layout"
            raise ValueError(message)
        info = BytesParser().parsebytes(archive.read(metadata[0]))
        if info["Name"] != "rag-access-guard-api":
            message = "Invalid API wheel identity"
            raise ValueError(message)
    report = TypeAdapter(WheelReport).validate_json(
        read_verified(wheel_record.path, wheel_record.sha256), strict=True
    )
    validate_smoke(report.public_api_smoke)
    if report.exit_code != 0 or report.wheel_sha256 != digest:
        return ("installed wheel differs from release artifact",)
    return ()


class Arguments(argparse.Namespace):
    """Explicit paths keep evidence separate from the public checkout."""

    manifest: Path = Path()
    repository: Path = Path()


def main(arguments: list[str] | None = None) -> int:
    """Check supplied evidence read-only; never create a tag or publish a release."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--manifest", required=True, type=Path)
    _ = parser.add_argument("--repository", required=True, type=Path)
    options = parser.parse_args(arguments, namespace=Arguments())
    try:
        manifest = TypeAdapter(ReleaseManifest).validate_json(
            options.manifest.read_bytes(), strict=True
        )
        errors = validate_manifest(manifest, options.repository.resolve(strict=True))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        _ = sys.stderr.write("Release rejected: missing or invalid evidence\n")
        return 2
    if errors:
        _ = sys.stderr.write("Release rejected: " + "; ".join(errors) + "\n")
        return 1
    _ = sys.stdout.write(f"Release evidence consistent: {manifest.proof.commit_sha}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
