import json
import os
import socket
import subprocess
import sys
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

import anyio
import psutil
import pytest
from anyio.to_thread import run_sync
from pydantic import ValidationError

from rag_access_guard_api.adapters import ollama
from rag_access_guard_api.adapters.inference_runtime import (
    InferenceRuntime,
    InferenceUnavailableError,
    bind_runtime,
)
from rag_access_guard_api.adapters.llm import get_llm_adapter, initialize_llm
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.adapters.ollama_job import WindowsJob
from rag_access_guard_api.adapters.ollama_supervisor import (
    OllamaSupervisor,
    Ownership,
    ProcessIdentity,
    SupervisorConfig,
)
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.model_manifest import TOKENIZER_REVISION


@pytest.mark.anyio
@pytest.mark.parametrize("profile_fault", [False, True])
@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
async def test_owned_outer_cancellation_exits_runners_and_rechecks_profile(
    owned_case: "OwnedCase", monkeypatch: pytest.MonkeyPatch, *, profile_fault: bool
) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    await run_sync(wait_file, owned_case.directory / "grandchild")
    await run_sync(wait_file, owned_case.directory / "listening")
    old = supervisor.owned_processes()
    runtime = InferenceRuntime()
    recovery_entered, recovery_release = Event(), Event()
    original_stop = supervisor.stop

    def held_stop() -> None:
        recovery_entered.set()
        if not recovery_release.wait(10):
            raise TimeoutError
        original_stop()

    monkeypatch.setattr(supervisor, "stop", held_stop)
    observations: list[str] = []

    def observe_recovery() -> None:
        if recovery_entered.wait(10):
            observations.append(runtime.snapshot().generation)
            try:
                _ = runtime.try_acquire_generation(uuid4())
            except InferenceUnavailableError:
                observations.append("rejected")
        recovery_release.set()

    try:
        async with anyio.create_task_group() as watchers:
            _ = watchers.start_soon(run_sync, observe_recovery)
            adapter = OllamaAdapter(
                base_url=supervisor.config.host, runtime=runtime, supervisor=supervisor
            )
            async with anyio.create_task_group() as requests:

                async def generate() -> None:
                    _ = await adapter.generate(user_input="CONTROLLED", system_supplied_context="C")

                _ = requests.start_soon(generate)
                await run_sync(wait_file, owned_case.directory / "accepted")
                await assert_live_claim_rejects_other_controller(supervisor, old)
                if profile_fault:
                    (owned_case.directory / "profile-fault").touch()
                requests.cancel_scope.cancel()
        assert observations == ["unavailable", "rejected"]
        assert all(not member.alive() for member in old)
        assert runtime.snapshot().generation == ("unavailable" if profile_fault else "ready")
        trace = (owned_case.directory / "trace.jsonl").read_text(encoding="utf-8")
        assert trace.count('"event": "/api/show"') == 2
        assert trace.count('"event": "/api/chat"') == 1
    finally:
        recovery_release.set()
        monkeypatch.setattr(supervisor, "stop", original_stop)
        await runtime.aclose()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
async def test_abandoned_late_start_cannot_clear_recovery_quarantine(
    owned_case: "OwnedCase", monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    await run_sync(wait_file, owned_case.directory / "listening")
    runtime = InferenceRuntime()
    entered, release, finished = Event(), Event(), Event()
    original_start = supervisor.start

    def delayed_start() -> None:
        entered.set()
        try:
            if not release.wait(10):
                raise TimeoutError
            original_start()
        finally:
            finished.set()

    try:
        monkeypatch.setattr(supervisor, "start", delayed_start)
        monkeypatch.setattr(ollama, "INFERENCE_RECOVERY_SECONDS", 0.3)
        adapter = OllamaAdapter(
            base_url=supervisor.config.host, runtime=runtime, supervisor=supervisor
        )
        started = monotonic()
        with anyio.fail_after(3):
            async with anyio.create_task_group() as requests:

                async def generate() -> None:
                    _ = await adapter.generate(user_input="CONTROLLED", system_supplied_context="C")

                _ = requests.start_soon(generate)
                await run_sync(wait_file, owned_case.directory / "accepted")
                requests.cancel_scope.cancel()
        assert monotonic() - started < 3
        assert entered.is_set()
        assert not finished.is_set()
        assert runtime.snapshot().generation == "unavailable"
        release.set()
        assert await run_sync(finished.wait, 10)
        assert runtime.snapshot().generation == "unavailable"
        with pytest.raises(GenerationUnavailable):
            _ = await adapter.generate(user_input="SECOND", system_supplied_context="C")
        trace = (owned_case.directory / "trace.jsonl").read_text(encoding="utf-8")
        assert trace.count('"event": "/api/chat"') == 1
    finally:
        release.set()
        _ = await run_sync(finished.wait, 10)
        monkeypatch.setattr(supervisor, "start", original_start)
        await runtime.aclose()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
async def test_stop_refusal_keeps_runtime_quarantined(
    owned_case: "OwnedCase", monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    await run_sync(wait_file, owned_case.directory / "listening")
    runtime = InferenceRuntime()

    def refuse() -> None:
        raise OSError

    try:
        with monkeypatch.context() as changes:
            changes.setattr(supervisor, "stop", refuse)
            adapter = OllamaAdapter(
                base_url=supervisor.config.host, runtime=runtime, supervisor=supervisor
            )
            async with anyio.create_task_group() as requests:

                async def generate() -> None:
                    _ = await adapter.generate(user_input="CONTROLLED", system_supplied_context="C")

                _ = requests.start_soon(generate)
                await run_sync(wait_file, owned_case.directory / "accepted")
                requests.cancel_scope.cancel()
            assert runtime.snapshot().generation == "unavailable"
            with pytest.raises(GenerationUnavailable):
                _ = await adapter.generate(user_input="SECOND", system_supplied_context="C")
        assert supervisor.owned_processes()
    finally:
        await runtime.aclose()


@dataclass(frozen=True)
class OwnedCase:
    supervisor: OllamaSupervisor
    directory: Path


async def assert_live_claim_rejects_other_controller(
    supervisor: OllamaSupervisor, old: tuple[ProcessIdentity, ...]
) -> None:
    state = Ownership.model_validate_json(supervisor.config.state_path.read_bytes())
    assert state.quarantined
    fresh = OllamaSupervisor(supervisor.config)
    assert fresh.status() == "unavailable"
    with pytest.raises(InferenceUnavailableError):
        fresh.start()
    second_runtime = InferenceRuntime()
    try:
        second = OllamaAdapter(
            base_url=supervisor.config.host, runtime=second_runtime, supervisor=fresh
        )
        with pytest.raises(GenerationUnavailable):
            _ = await second.generate(user_input="SECOND", system_supplied_context="C")
        with pytest.raises(InferenceUnavailableError):
            _ = fresh.begin_inference()
        assert supervisor.config.state_path.read_bytes() == state.model_dump_json().encode()
        assert all(member.alive() for member in old)
    finally:
        await second_runtime.aclose()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
async def test_factory_retains_owner_and_close_reaps_its_spawned_child(
    owned_case: OwnedCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    await run_sync(wait_file, owned_case.directory / "listening")
    config_path = owned_case.directory / "config.json"
    _ = config_path.write_text(supervisor.config.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("RAG_ACCESS_GUARD_LLM_ADAPTER", "ollama")
    monkeypatch.setenv("RAG_ACCESS_GUARD_OLLAMA_BASE_URL", supervisor.config.host)
    monkeypatch.setenv("RAG_ACCESS_GUARD_OLLAMA_SUPERVISOR_CONFIG_PATH", str(config_path))
    runtime = InferenceRuntime()
    owned = supervisor.owned_processes()
    (owned_case.directory / "release").touch()
    try:
        assert runtime.own_supervisor(supervisor) is supervisor
        with bind_runtime(runtime):
            await initialize_llm(Settings())
            adapter = get_llm_adapter()
            assert (
                await adapter.generate(user_input="CONTROLLED", system_supplied_context="C")
                == "CONTROLLED_ANSWER"
            )
        state = Ownership.model_validate_json(supervisor.config.state_path.read_bytes())
        assert not state.quarantined
        assert supervisor.status() == "ready"
        assert runtime.own_supervisor(OllamaSupervisor(supervisor.config)) is supervisor
    finally:
        await runtime.aclose()
    assert all(not member.alive() for member in owned)
    assert supervisor.status() == "stopped"


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
async def test_claim_write_failure_sends_no_inference_payload(
    owned_case: OwnedCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    await run_sync(wait_file, owned_case.directory / "listening")
    runtime = InferenceRuntime()

    def refuse(state: Ownership) -> None:
        del state
        raise OSError

    try:
        with monkeypatch.context() as changes:
            changes.setattr(supervisor, "_write_state", refuse)
            adapter = OllamaAdapter(
                base_url=supervisor.config.host, runtime=runtime, supervisor=supervisor
            )
            with pytest.raises(GenerationUnavailable):
                _ = await adapter.generate(user_input="CONTROLLED", system_supplied_context="C")
            assert runtime.snapshot().generation == "unavailable"
            assert supervisor.status() == "unavailable"
            assert not (owned_case.directory / "accepted").exists()
    finally:
        await runtime.aclose()


@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
def test_cli_exit_preserves_reopenable_owned_instance(owned_case: OwnedCase) -> None:
    repository = Path(__file__).parents[2]
    helper = repository / "tests" / "helpers" / "controlled_ollama.py"
    bootstrap = "\n".join(
        (
            "import runpy,sys",
            f"sys.argv = [{str(helper)!r}, {str(owned_case.directory)!r}]",
            f"runpy.run_path({str(helper)!r}, run_name='__main__')",
        )
    )
    _ = (owned_case.directory / "serve").write_text(bootstrap, encoding="utf-8")
    config_path = owned_case.directory / "config.json"
    _ = config_path.write_text(owned_case.supervisor.config.model_dump_json(), encoding="utf-8")
    environment = dict(os.environ)
    environment["RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH"] = str(
        repository / ".cache" / "qwen3" / TOKENIZER_REVISION / "tokenizer.json"
    )

    def command(action: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 -- actual fixed CLI, private config, no shell.
            [
                sys.executable,
                str(repository / "scripts" / "serve_demo_ollama.py"),
                action,
                "--config",
                str(config_path),
            ],
            cwd=owned_case.directory,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=25,
        )

    started = command("start")
    assert started.returncode == 0, started.stderr
    assert started.stdout.strip() == "ready"
    wait_file(owned_case.directory / "grandchild")
    owned = owned_case.supervisor.owned_processes()
    assert len(owned) >= 3
    assert command("status").stdout.strip() == "ready"
    assert command("start").stdout.strip() == "ready"
    assert owned_case.supervisor.owned_processes() == owned
    stopped = command("stop")
    assert stopped.returncode == 0
    assert stopped.stdout.strip() == "stopped"
    assert all(not member.alive() for member in owned)


def wait_file(path: Path) -> None:
    deadline = monotonic() + 10
    interval = Event()
    while not path.exists():
        if monotonic() >= deadline:
            raise TimeoutError
        _ = interval.wait(0.01)


@pytest.fixture
def owned_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[OwnedCase]:
    config = SupervisorConfig(
        instance_id=uuid4(),
        executable=Path(sys.executable),
        host="http://127.0.0.1:11435",
        state_path=tmp_path / "state.json",
        model_store_path=tmp_path / "models",
    )
    command = (
        sys.executable,
        str(Path(__file__).parents[1] / "helpers" / "controlled_ollama.py"),
        str(tmp_path),
    )
    supervisor = OllamaSupervisor(config, command=command)
    original_start = supervisor.start
    jobs: list[WindowsJob] = []

    def start_and_hold() -> None:
        original_start()
        state = Ownership.model_validate_json(config.state_path.read_bytes())
        jobs.append(WindowsJob(state.job_name))

    monkeypatch.setattr(supervisor, "start", start_and_hold)
    try:
        yield OwnedCase(supervisor, tmp_path)
    finally:
        try:
            supervisor.stop()
        finally:
            for job in jobs:
                job.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
def test_exiting_member_during_enumeration_is_not_unknown_live_work(
    owned_case: OwnedCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    wait_file(owned_case.directory / "grandchild")
    wait_file(owned_case.directory / "listening")
    expected = supervisor.owned_processes()
    original_pids = WindowsJob.pids
    original_read = ProcessIdentity.read

    def listed(job: WindowsJob) -> tuple[int, ...]:
        return (*original_pids(job), 123456789)

    def read(cls: type[ProcessIdentity], pid: int) -> ProcessIdentity:
        del cls
        if pid == 123456789:
            raise psutil.NoSuchProcess(pid)
        return original_read(pid)

    with monkeypatch.context() as changes:
        changes.setattr(WindowsJob, "pids", listed)
        changes.setattr(ProcessIdentity, "read", classmethod(read))
        assert supervisor.owned_processes() == expected


@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
def test_live_nonmember_identity_uncertainty_refuses_stop(
    owned_case: OwnedCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    original_pids = WindowsJob.pids
    root = Ownership.model_validate_json(supervisor.config.state_path.read_bytes()).root

    def listed(job: WindowsJob) -> tuple[int, ...]:
        return (*original_pids(job), os.getpid())

    with monkeypatch.context() as changes:
        changes.setattr(WindowsJob, "pids", listed)
        with pytest.raises(InferenceUnavailableError):
            _ = supervisor.owned_processes()
        with pytest.raises(InferenceUnavailableError):
            supervisor.stop()
        assert root.alive()
        assert supervisor.status() == "unavailable"


@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
def test_owned_process_and_runner_exit_before_repeated_start(owned_case: OwnedCase) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    wait_file(owned_case.directory / "grandchild")
    wait_file(owned_case.directory / "listening")
    first = supervisor.owned_processes()
    assert len(first) >= 3
    assert supervisor.status() == "ready"
    trace = (owned_case.directory / "trace.jsonl").read_text(encoding="utf-8")
    assert all(f'"event": "{role}"' in trace for role in ("root", "runner", "grandchild"))
    supervisor.stop()
    assert all(not member.alive() for member in first)
    assert supervisor.status() == "stopped"
    supervisor.start()
    assert supervisor.owned_processes()


@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
def test_identity_reuse_refuses_termination(owned_case: OwnedCase) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    state_path = supervisor.config.state_path
    original = state_path.read_bytes()
    state = Ownership.model_validate_json(original)
    changed = state.model_copy(update={"root": state.root.model_copy(update={"created": -1.0})})
    _ = state_path.write_text(changed.model_dump_json(), encoding="utf-8")
    try:
        with pytest.raises(InferenceUnavailableError):
            supervisor.stop()
        assert supervisor.status() == "unavailable"
        assert state.root.alive()
    finally:
        _ = state_path.write_bytes(original)


@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
def test_dead_root_keeps_contained_runner_exit_proof(owned_case: OwnedCase) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    wait_file(owned_case.directory / "grandchild")
    members = supervisor.owned_processes()
    state = Ownership.model_validate_json(supervisor.config.state_path.read_bytes())
    with supervisor.hold_containment():
        process = psutil.Process(state.root.pid)
        assert state.root.alive()
        process.kill()
        _ = process.wait(timeout=5)
        assert supervisor.status() == "unavailable"
        assert supervisor.owned_processes()
        supervisor.stop()
    assert all(not member.alive() for member in members)


@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
def test_foreign_endpoint_is_not_adopted(owned_case: OwnedCase) -> None:
    with socket.socket() as endpoint:
        endpoint.bind(("127.0.0.1", 11435))
        endpoint.listen()
        with pytest.raises(InferenceUnavailableError):
            owned_case.supervisor.start()
        assert not owned_case.supervisor.config.state_path.exists()


def test_private_supervisor_config_rejects_unknown_fields(tmp_path: Path) -> None:
    payload = {
        "instance_id": str(uuid4()),
        "executable": sys.executable,
        "host": "http://127.0.0.1:11435",
        "state_path": str(tmp_path / "state.json"),
        "model_store_path": str(tmp_path / "models"),
        "extra": True,
    }
    with pytest.raises(ValidationError):
        _ = SupervisorConfig.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("field", ["executable", "state_path", "model_store_path"])
def test_private_supervisor_config_rejects_relative_paths(field: str, tmp_path: Path) -> None:
    payload = {
        "instance_id": str(uuid4()),
        "executable": sys.executable,
        "host": "http://127.0.0.1:11435",
        "state_path": str(tmp_path / "state.json"),
        "model_store_path": str(tmp_path / "models"),
    }
    payload[field] = "relative"
    with pytest.raises(ValidationError):
        _ = SupervisorConfig.model_validate(payload)
