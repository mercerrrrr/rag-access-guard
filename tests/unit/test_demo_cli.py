import json
import subprocess
import sys
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy.engine import make_url
from tests.helpers.demo_seed import seed_config

from rag_access_guard_api.demo.private_write import exclusive_private_write, private_directory
from rag_access_guard_api.services.demo_corpus import REPOSITORY_ROOT


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed native interpreter, no shell or credential argv
        [sys.executable, "scripts/seed_demo.py", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_help_has_only_nondestructive_explicit_setup_flags() -> None:
    # Given/When: an operator requests help without any environment/database initialization.
    result = run_cli("--help")
    # Then: only the documented safe input and mutation switches are exposed.
    assert result.returncode == 0
    assert "--initialize-owned-database" in result.stdout
    assert "--accept-content-update" in result.stdout
    assert "--password" not in result.stdout
    assert "--reset" not in result.stdout


def test_invalid_argument_values_are_not_echoed() -> None:
    # Given/When: unrecognized input contains a sentinel that must never reach output.
    result = run_cli("--password", "DUMMY-NOT-A-CREDENTIAL")
    # Then: the boundary emits only a safe error and a native failing exit.
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": "invalid_arguments"}
    assert "DUMMY-NOT-A-CREDENTIAL" not in result.stdout + result.stderr


def test_unsafe_target_config_has_no_password_or_private_path_output(tmp_path: Path) -> None:
    # Given: a protected private configuration naming a forbidden production database.
    target = make_url(
        "postgresql+psycopg://postgres:DUMMY-DSN-SENTINEL@127.0.0.1:55466/user_database"
    )
    config = seed_config(tmp_path, target)
    config = config.model_copy(
        update={
            "database_url": SecretStr(target.render_as_string(hide_password=False)),
            "runtime": config.runtime.model_copy(update={"repository_root": REPOSITORY_ROOT}),
        }
    )
    directory = private_directory(tmp_path / "configuration")
    path = directory / "config.json"
    raw = config.model_dump(mode="json")
    raw["database_url"] = target.render_as_string(hide_password=False)
    exclusive_private_write(path, json.dumps(raw).encode())
    # When: the public subprocess boundary is invoked for apply.
    result = run_cli("--config", str(path), "--apply")
    # Then: it refuses before connecting, with only a safe machine-readable defect.
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": "unsafe_target"}
    assert "DUMMY-DSN-SENTINEL" not in result.stdout + result.stderr
    assert str(path) not in result.stdout + result.stderr
    assert not config.credentials_path.exists()
