import zipfile
from pathlib import Path

import pytest
from scripts.demo_guard_host import verify_install
from scripts.verify_guard_wheel import validate_wheel


@pytest.mark.parametrize("fault", ["foreign", "dependency", "traversal", "missing"])
def test_invalid_wheel_is_rejected_before_execution(tmp_path: Path, fault: str) -> None:
    wheel = tmp_path / "synthetic.whl"
    name = "other-package" if fault == "foreign" else "rag-access-guard"
    metadata = f"Metadata-Version: 2.4\nName: {name}\nVersion: 0.1.0\n"
    if fault == "dependency":
        metadata += "Requires-Dist: fastapi\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("rag_access_guard-0.1.0.dist-info/METADATA", metadata)
        if fault != "missing":
            archive.writestr("rag_access_guard/__init__.py", "")
        if fault == "traversal":
            archive.writestr("rag_access_guard/../../outside.py", "")
    with pytest.raises(
        ValueError, match=r"invalid_wheel_metadata|unexpected_wheel_payload|missing_core_package"
    ):
        _ = validate_wheel(wheel)


def test_corrupt_wheel_rejected(tmp_path: Path) -> None:
    wheel = tmp_path / "corrupt.whl"
    _ = wheel.write_bytes(b"not a zip")
    with pytest.raises(zipfile.BadZipFile):
        _ = validate_wheel(wheel)


def test_source_checkout_cannot_pass_installed_origin_check(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match=r"infrastructure_imported|external_core_origin"):
        _ = verify_install(tmp_path / "not-installed.whl")
