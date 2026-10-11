import os
import subprocess
from pathlib import Path
from typing import TypedDict, Unpack

import anyio
import pytest
from anyio.to_thread import run_sync

from rag_access_guard_api.demo import private_write
from rag_access_guard_api.server import create_event_loop


class AclProcessOptions(TypedDict):
    env: dict[str, str]
    capture_output: bool
    check: bool
    timeout: int
    creationflags: int


@pytest.fixture
def module_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[bool]]:
    module_path = tmp_path / "incompatible-modules"
    module_path.mkdir()
    security_module = module_path / "Microsoft.PowerShell.Security"
    security_module.mkdir()
    _ = (security_module / "Microsoft.PowerShell.Security.psd1").write_text(
        """@{ModuleVersion='1.0.0'
RootModule='incompatible.psm1'
FunctionsToExport=@('Get-Acl','Set-Acl')}"""
    )
    _ = (security_module / "incompatible.psm1").write_text("throw 'incompatible-native-module'")
    monkeypatch.setenv("PSModulePath", str(module_path))
    monkeypatch.setenv("RAG_DEMO_TEST_MARKER", "retained-marker")
    monkeypatch.setenv("RAG_DEMO_SET_ACL", "caller-marker")
    monkeypatch.setenv("RAG_DEMO_PRIVATE_PATH", "caller-path")
    copied = os.environ.copy()
    assert copied["PSMODULEPATH"] == str(module_path)
    assert "PSModulePath" not in copied
    executable = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(  # noqa: S603 -- fixed qualified native availability control; no shell
        [
            str(executable.resolve(strict=True)),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop'; Get-Command Get-Acl,Set-Acl | Out-Null",
        ],
        env=copied,
        capture_output=True,
        check=False,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert b"CommandNotFoundException" in result.stderr
    native_run = subprocess.run
    observations: list[bool] = []

    def native_acl(
        arguments: list[str], **options: Unpack[AclProcessOptions]
    ) -> subprocess.CompletedProcess[bytes]:
        observations.append(options["env"].get("RAG_DEMO_TEST_MARKER") == "retained-marker")
        return native_run(arguments, **options)

    monkeypatch.setattr(subprocess, "run", native_acl)
    return module_path, observations


@pytest.mark.skipif(os.name != "nt", reason="Real Windows ACL and module-search boundary")
@pytest.mark.parametrize("worker", [False, True], ids=["synchronous", "anyio-worker"])
def test_owner_only_ignores_incompatible_parent_module_path(
    module_override: tuple[Path, list[bool]], *, worker: bool
) -> None:
    # Given: an independently failing module override and unchanged caller markers.
    module_path, observations = module_override
    target = module_path.parent / "private"

    async def initialize_from_worker() -> Path:
        return await run_sync(private_write.private_directory, target)

    # When: the same native ACL consumer initializes from either real caller context.
    if worker:
        result = anyio.run(
            initialize_from_worker, backend_options={"loop_factory": create_event_loop}
        )
    else:
        result = private_write.private_directory(target)
    # Then: strict native verification succeeds while the caller environment is preserved.
    assert result == target
    private_write.owner_only(target)
    assert observations
    assert all(observations)
    assert os.environ["PSMODULEPATH"] == str(module_path)
    assert os.environ["RAG_DEMO_TEST_MARKER"] == "retained-marker"
    assert os.environ["RAG_DEMO_SET_ACL"] == "caller-marker"
    assert os.environ["RAG_DEMO_PRIVATE_PATH"] == "caller-path"
