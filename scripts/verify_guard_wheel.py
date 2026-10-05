"""Install one exact wheel outside the checkout and verify its real standalone cycle."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from collections.abc import Callable
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path, PurePosixPath

type JsonValue = str | int | bool | list[JsonValue] | dict[str, JsonValue] | None

SMOKE_CHECKS = frozenset(
    {
        "prepare_allowed",
        "release_allowed",
        "read_allowed",
        "read_revoked",
        "release_revoked",
        "prepare_revoked",
        "unknown_denied",
        "other_principal_denied",
    }
)


def validate_smoke(report: JsonValue) -> None:
    """Require the complete public contract probe, without boolean coercion."""
    if (
        not isinstance(report, dict)
        or frozenset(report) != SMOKE_CHECKS
        or not all(value is True for value in report.values())
    ):
        message = "public_api_smoke_failed"
        raise ValueError(message)


def _decode(decoder: Callable[[str], JsonValue], text: str) -> JsonValue:
    return decoder(text)


def validate_wheel(wheel: Path) -> str:
    """Reject foreign payloads and runtime dependencies before executing installed code."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1 or len(names) != len(set(names)):
            message = "invalid_wheel_layout"
            raise ValueError(message)
        info = BytesParser().parsebytes(archive.read(metadata[0]))
        if info["Name"] != "rag-access-guard" or info.get_all("Requires-Dist"):
            message = "invalid_wheel_metadata"
            raise ValueError(message)
        dist_root = metadata[0].split("/", 1)[0]
        for name in names:
            parts = PurePosixPath(name).parts
            if (
                not parts
                or parts[0] not in {"rag_access_guard", dist_root}
                or ".." in parts
                or "\\" in name
                or PurePosixPath(name).is_absolute()
            ):
                message = "unexpected_wheel_payload"
                raise ValueError(message)
        if "rag_access_guard/__init__.py" not in names:
            message = "missing_core_package"
            raise ValueError(message)
    return sha256(wheel.read_bytes()).hexdigest()


def _run(command: list[str], directory: Path, environment: dict[str, str]) -> str:
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and owned artifact paths.
        command,
        cwd=directory,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if any(
        marker in result.stdout + result.stderr
        for marker in ("SYNTHETIC_ALPHA", "SYNTHETIC_POLICY_EXCEPTION")
    ):
        message = "protected_output_exposed"
        raise ValueError(message)
    return result.stdout


def verify(wheel: Path) -> dict[str, JsonValue]:
    """Create an isolated venv, install offline, and check the post-cycle module origins."""
    wheel = wheel.resolve(strict=True)
    digest = validate_wheel(wheel)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"} and not key.startswith("PIP_")
    }
    environment["PIP_CONFIG_FILE"] = os.devnull
    with tempfile.TemporaryDirectory(prefix="rag-guard-wheel-") as temporary:
        directory = Path(temporary).resolve()
        if directory.is_relative_to(Path(__file__).resolve().parents[1]):
            message = "temporary_directory_inside_checkout"
            raise ValueError(message)
        target = directory / "venv"
        venv.EnvBuilder(with_pip=True).create(target)
        copied = Path(shutil.copy2(wheel, directory / wheel.name))
        demo = Path(
            shutil.copy2(
                Path(__file__).with_name("demo_guard_host.py"), directory / "demo_guard_host.py"
            )
        )
        python = str(target / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        _ = _run(
            [python, "-I", "-m", "pip", "install", "--no-index", "--no-deps", str(copied)],
            directory,
            environment,
        )
        check = _run([python, "-I", "-m", "pip", "check"], directory, environment)
        probe = (
            "import pathlib,sys,sysconfig,importlib.metadata,rag_access_guard; "
            "site=pathlib.Path(sysconfig.get_paths()['purelib']).resolve(); "
            "assert pathlib.Path(rag_access_guard.__file__).resolve().is_relative_to(site); "
            "assert site.is_relative_to(pathlib.Path(sys.prefix).resolve()); "
            "assert not importlib.metadata.distribution('rag-access-guard').requires"
        )
        _ = _run([python, "-I", "-c", probe], directory, environment)
        output = _run(
            [python, "-I", str(demo), "--json", "--verify-install", "--wheel", str(copied)],
            directory,
            environment,
        )
        report = _decode(json.loads, output)
        if not isinstance(report, dict):
            message = "invalid_demo_report"
            raise TypeError(message)
        initial, final, release = (
            report.get("initial_read"),
            report.get("final_read"),
            report.get("late_release"),
        )
        installation = report.get("installation")
        if (
            not isinstance(initial, dict)
            or initial.get("state") != "available"
            or not isinstance(final, dict)
            or final.get("state") != "unavailable"
            or final.get("answer") is not None
            or final.get("sources") != []
            or not isinstance(release, dict)
            or release.get("allowed") is not False
            or release.get("reason") != "stale_revision"
            or (report.get("saved_count"), report.get("model_calls"), report.get("retry_count"))
            != (1, 2, 1)
            or not isinstance(installation, dict)
            or installation.get("prefix") != str(target)
            or installation.get("wheel_sha256") != digest
        ):
            message = "standalone_contract_failed"
            raise ValueError(message)
        smoke = Path(
            shutil.copy2(
                Path(__file__).resolve().parents[1] / "tests/release/installed_guard_smoke.py",
                directory / "installed_guard_smoke.py",
            )
        )
        smoke_report = _decode(json.loads, _run([python, "-I", str(smoke)], directory, environment))
        validate_smoke(smoke_report)
        return {
            "wheel_sha256": digest,
            "pip_check": check.strip(),
            "exit_code": 0,
            "demo": report,
            "public_api_smoke": smoke_report,
        }


class Arguments(argparse.Namespace):
    """Typed CLI inputs validated by argparse."""

    wheel: Path = Path()


def main() -> int:
    """Print a safe report only after installation and semantic checks succeed."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--wheel", required=True, type=Path)
    args = parser.parse_args(namespace=Arguments())
    try:
        _ = sys.stdout.write(json.dumps(verify(args.wheel), ensure_ascii=True) + "\n")
    except Exception:  # noqa: BLE001 -- failed child output may contain protected data.
        _ = sys.stderr.write('{"error":"wheel_verification_failed"}\n')
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
