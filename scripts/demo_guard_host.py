"""Single-process integration example with canonical storage and no network dependencies."""

import argparse
import asyncio
import importlib.metadata
import json
import sys
import sysconfig
import zipfile
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Literal, final
from uuid import UUID, uuid4

from rag_access_guard import (
    CandidateChunk,
    Guard,
    PolicySnapshot,
    PreparedContext,
    PrepareDenied,
    PriorTurn,
    ReleaseDecision,
    SourceRef,
)

DEMO_PRINCIPAL = UUID(int=1)
DEMO_THREAD = UUID(int=2)
DEMO_DOCUMENT = UUID(int=3)
DEMO_SOURCE = SourceRef(
    document_id=DEMO_DOCUMENT, document_version_id=UUID(int=4), chunk_id=UUID(int=5)
)
OTHER_PRINCIPAL = UUID(int=6)
OTHER_THREAD = UUID(int=7)
OTHER_SOURCE = SourceRef(
    document_id=UUID(int=8), document_version_id=UUID(int=9), chunk_id=UUID(int=10)
)
OLD_SOURCE = SourceRef(
    document_id=DEMO_DOCUMENT, document_version_id=UUID(int=11), chunk_id=UUID(int=12)
)
DEMO_QUESTION = "Какие условия действуют?"
DEMO_MARKER = "SYNTHETIC_ALPHA"
UNAVAILABLE = "Ответ недоступен: права на один из источников изменились."


@dataclass(frozen=True)
class Utf8ByteDemoCounter:
    """Count bytes for this synthetic example, not tokens of a language model."""

    identity: str = field(default="demo-utf8-byte-v1:rag-access-guard/context/v2", init=False)

    def count(self, text: str) -> int:
        """Return the exact UTF-8 byte length."""
        return len(text.encode("utf-8"))


@dataclass(frozen=True)
class CanonicalRow:
    """An immutable, ready version with one synthetic chunk."""

    source: SourceRef
    original: bytes
    original_sha256: str
    extracted_text: str
    text: str
    content_sha256: str
    token_count: int
    ordinal: int = 0
    parser_revision: str = "synthetic-utf8-v1"
    chunker_revision: str = "synthetic-one-chunk-v1"
    model_revision: str = "no-embedding-model"
    config_revision: str = "synthetic-config-v1"

    def valid(self, ref: SourceRef) -> bool:
        """Resolve all foreign keys and independently verify stored bytes and text."""
        return (
            ref == self.source
            and sha256(self.original).hexdigest() == self.original_sha256
            and self.original == self.extracted_text.encode("utf-8")
            and self.text == self.extracted_text
            and sha256(self.text.encode("utf-8")).hexdigest() == self.content_sha256
            and self.token_count == Utf8ByteDemoCounter().count(self.text)
        )

    def candidate(self) -> CandidateChunk:
        """Copy the canonical chunk into the public boundary type."""
        return CandidateChunk(
            source_ref=self.source,
            text=self.text,
            content_sha256=self.content_sha256,
            token_count=self.token_count,
        )


@dataclass(frozen=True)
class ReadView:
    """Safe projection of the last stored answer."""

    state: Literal["available", "unavailable"]
    user_input: str
    answer: str | None
    sources: tuple[SourceRef, ...]
    message: str | None


@dataclass(frozen=True)
class DemoReport:
    """In-memory evidence; the CLI must not serialize the initial answer."""

    initial_read: ReadView
    final_read: ReadView
    late_release: ReleaseDecision
    saved_count: int
    model_calls: int
    retry_count: int


@final
class MemoryPolicyReader:
    """Read canonical storage only inside the task-owned host critical section."""

    def __init__(
        self, host: "DemoHost", principal: UUID, scope: object, thread: UUID | None
    ) -> None:
        """Bind the adapter to a host-authenticated scope, not caller credentials."""
        self._host = host
        self._principal = principal
        self._scope = scope
        self._task = asyncio.current_task()
        self._thread = thread

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        """Produce an immutable partition and independent canonical hash witness."""
        self._host.require_scope(self._scope, self._task)
        active = (
            principal_id == self._principal
            and self._host.session_valid(principal_id)
            and (self._thread is None or self._host.owns(principal_id, self._thread))
        )
        allowed: list[SourceRef] = []
        denied: list[SourceRef] = []
        hashes: list[tuple[SourceRef, str]] = []
        valid = True
        for ref in dict.fromkeys(source_refs):
            row = self._host.canonical(ref)
            if row is None or not row.valid(ref):
                valid = False
                denied.append(ref)
            elif active and self._host.permitted(principal_id, ref):
                allowed.append(ref)
                hashes.append((ref, row.content_sha256))
            else:
                denied.append(ref)
        return PolicySnapshot(
            principal_id=principal_id,
            revision=self._host.revision,
            allowed_refs=tuple(allowed),
            denied_refs=tuple(denied),
            provenance_valid=valid,
            principal_active=active,
            thread_owned=None if thread_id is None else self._host.owns(principal_id, thread_id),
            canonical_chunk_hashes=tuple(hashes),
        )


@final
class DemoHost:
    """Own authentication, storage and linearization outside the independent guard."""

    def __init__(self, rows: tuple[CanonicalRow, ...]) -> None:
        """Start a separate synthetic installation without grants."""
        self.guard: Guard = Guard(Utf8ByteDemoCounter(), max_context_tokens=5000, max_prior_turns=4)
        self._rows = {row.source.chunk_id: row for row in rows}
        self._active = {
            DEMO_DOCUMENT: DEMO_SOURCE.document_version_id,
            OTHER_SOURCE.document_id: OTHER_SOURCE.document_version_id,
        }
        self._owners = {DEMO_THREAD: DEMO_PRINCIPAL, OTHER_THREAD: OTHER_PRINCIPAL}
        self._grants: set[tuple[UUID, UUID]] = set()
        self._members: set[UUID] = set()
        self._role_grants: set[UUID] = set()
        self._session_principal = DEMO_PRINCIPAL
        self._session_active = True
        self._session_deadline = monotonic() + 1800
        self._principal_active = True
        self._lock = asyncio.Lock()
        self._scope: object | None = None
        self._prepared: list[tuple[UUID, UUID, PreparedContext]] = []
        self._turns: tuple[tuple[UUID, PriorTurn], ...] = ()
        self._audit: tuple[tuple[int, str], ...] = ()
        self._revision = 0
        self.model_calls = 0

    @property
    def revision(self) -> int:
        """Return the current security revision."""
        return self._revision

    @property
    def saved_count(self) -> int:
        """Count committed answers without exposing their contents."""
        return len(self._turns)

    def require_scope(self, scope: object, task: asyncio.Task[object] | None) -> None:
        """Reject escaped adapters and adapters borrowed by another task."""
        if self._scope is not scope or task is not asyncio.current_task():
            message = "inactive_policy_scope"
            raise RuntimeError(message)

    def canonical(self, ref: SourceRef) -> CanonicalRow | None:
        """Resolve an immutable row; its full tuple still requires validation."""
        return self._rows.get(ref.chunk_id)

    def session_valid(self, principal: UUID) -> bool:
        """Check the bound session independently of a supplied principal identifier."""
        return (
            principal == self._session_principal
            and self._session_active
            and self._principal_active
            and monotonic() < self._session_deadline
        )

    def owns(self, principal: UUID, thread: UUID) -> bool:
        """Resolve the canonical thread owner."""
        return self._owners.get(thread) == principal

    def permitted(self, principal: UUID, ref: SourceRef) -> bool:
        """Apply active-version and union-grant rules, with no admin bypass."""
        return self._active.get(ref.document_id) == ref.document_version_id and (
            (principal, ref.document_id) in self._grants
            or (principal in self._members and ref.document_id in self._role_grants)
        )

    @asynccontextmanager
    async def policy_scope(
        self, principal_id: UUID, *, thread_id: UUID | None = None
    ) -> AsyncGenerator[MemoryPolicyReader]:
        """Serialize the entire host operation, including save after authorization."""
        async with self._lock:
            scope = object()
            self._scope = scope
            try:
                yield MemoryPolicyReader(self, principal_id, scope, thread_id)
            finally:
                self._scope = None

    def _changed(self, event: str) -> int:
        self._revision += 1
        self._audit += ((self._revision, event),)
        return self._revision

    async def grant(self, principal_id: UUID, document_id: UUID) -> int:
        """Commit a direct grant and its metadata-only revision event."""
        async with self._lock:
            self._grants.add((principal_id, document_id))
            return self._changed("grant")

    async def revoke(self, principal_id: UUID, document_id: UUID) -> int:
        """Commit a revocation under the same lock used by release and reads."""
        async with self._lock:
            self._grants.discard((principal_id, document_id))
            return self._changed("revoke")

    async def set_role(self, principal: UUID, document: UUID, *, member: bool) -> int:
        """Commit synthetic role membership without disturbing a direct grant."""
        async with self._lock:
            self._role_grants.add(document)
            if member:
                self._members.add(principal)
            else:
                self._members.discard(principal)
            return self._changed("membership")

    async def set_session(self, *, active: bool = True, expired: bool = False) -> int:
        """Change the host-authenticated session under the policy lock."""
        async with self._lock:
            self._session_active = active
            self._session_deadline = monotonic() + (-1 if expired else 1800)
            return self._changed("session")

    async def set_version(self, document: UUID, version: UUID | None) -> int:
        """Activate another immutable version, or make a document inactive."""
        async with self._lock:
            if version is None:
                _ = self._active.pop(document, None)
            else:
                self._active[document] = version
            return self._changed("version")

    async def prepare(self, principal_id: UUID, thread_id: UUID) -> PreparedContext | PrepareDenied:
        """Build candidates and complete history solely from host-owned storage."""
        async with self.policy_scope(principal_id, thread_id=thread_id) as reader:
            if not self.session_valid(principal_id) or not self.owns(principal_id, thread_id):
                return PrepareDenied(reason="denied", policy_revision=self.revision)
            rows = tuple(self._rows.values())
            if any(not row.valid(row.source) for row in rows):
                return PrepareDenied(reason="invalid_provenance", policy_revision=self.revision)
            history = tuple(turn for owner, turn in self._turns if owner == thread_id)
            prepared = await self.guard.prepare_context(
                principal_id, tuple(row.candidate() for row in rows), history, reader
            )
            if isinstance(prepared, PreparedContext) and prepared.source_refs:
                self._prepared.append((principal_id, thread_id, prepared))
            return prepared

    async def release(
        self, principal_id: UUID, thread_id: UUID, prepared: PreparedContext, model_output: str
    ) -> ReleaseDecision:
        """Authorize and atomically publish one answer with its complete provenance."""
        async with self.policy_scope(principal_id, thread_id=thread_id) as reader:
            binding = next(
                (
                    index
                    for index, item in enumerate(self._prepared)
                    if item[0] == principal_id and item[1] == thread_id and item[2] is prepared
                ),
                None,
            )
            if binding is None:
                return ReleaseDecision(
                    allowed=False, reason="invalid_provenance", policy_revision=None
                )
            _ = self._prepared.pop(binding)
            decision = await self.guard.authorize_release(principal_id, thread_id, prepared, reader)
            if decision.allowed:
                turn = PriorTurn(
                    turn_id=uuid4(),
                    user_input=DEMO_QUESTION,
                    answer=model_output,
                    source_refs=prepared.source_refs,
                    provenance_complete=True,
                )
                turns = (*self._turns, (thread_id, turn))
                audit = (*self._audit, (self.revision, "release"))
                self._turns, self._audit = turns, audit
            return decision

    async def read_saved(self, principal_id: UUID, thread_id: UUID) -> ReadView:
        """Reauthorize stored closure without exposing questions from foreign threads."""
        async with self.policy_scope(principal_id, thread_id=thread_id) as reader:
            if not self.session_valid(principal_id) or not self.owns(principal_id, thread_id):
                return ReadView("unavailable", "", None, (), UNAVAILABLE)
            turns = tuple(turn for owner, turn in self._turns if owner == thread_id)
            if not turns:
                return ReadView("unavailable", "", None, (), UNAVAILABLE)
            turn = turns[-1]
            decision = await self.guard.authorize_read(principal_id, turn.source_refs, reader)
            if not decision.allowed:
                return ReadView("unavailable", turn.user_input, None, (), UNAVAILABLE)
            return ReadView("available", turn.user_input, turn.answer, turn.source_refs, None)

    def model(self, user_input: str, prepared: PreparedContext) -> str:
        """Simulate generation only outside the host lock with authorized context."""
        if (
            self._lock.locked()
            or not prepared.source_refs
            or user_input != DEMO_QUESTION
            or not any(item[2] is prepared for item in self._prepared)
        ):
            message = "invalid_model_call"
            raise RuntimeError(message)
        self.model_calls += 1
        return prepared.model_context


def create_demo_host() -> DemoHost:
    """Construct two documents, an old version and separate principal/thread owners."""
    rows: list[CanonicalRow] = []
    for ref, text in (
        (DEMO_SOURCE, DEMO_MARKER),
        (OTHER_SOURCE, "SYNTHETIC_BETA_PRIVATE"),
        (OLD_SOURCE, "SYNTHETIC_OLD"),
    ):
        raw = text.encode("utf-8")
        rows.append(
            CanonicalRow(
                ref, raw, sha256(raw).hexdigest(), text, text, sha256(raw).hexdigest(), len(raw)
            )
        )
    return DemoHost(tuple(rows))


async def run_demo_cycle() -> DemoReport:
    """Show fresh reads and release denial after one serialized revocation."""
    host = create_demo_host()
    _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
    prepared = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
    if not isinstance(prepared, PreparedContext) or not prepared.source_refs:
        message = "prepare_failed"
        raise RuntimeError(message)
    decision = await host.release(
        DEMO_PRINCIPAL, DEMO_THREAD, prepared, host.model(DEMO_QUESTION, prepared)
    )
    initial = await host.read_saved(DEMO_PRINCIPAL, DEMO_THREAD)
    if not decision.allowed:
        message = "initial_release_failed"
        raise RuntimeError(message)
    pending = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
    if not isinstance(pending, PreparedContext) or not pending.source_refs:
        message = "pending_prepare_failed"
        raise RuntimeError(message)
    output = host.model(DEMO_QUESTION, pending)
    _ = await host.revoke(DEMO_PRINCIPAL, DEMO_DOCUMENT)
    final = await host.read_saved(DEMO_PRINCIPAL, DEMO_THREAD)
    late = await host.release(DEMO_PRINCIPAL, DEMO_THREAD, pending, output)
    retry = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
    if not isinstance(retry, PreparedContext) or retry.source_refs:
        message = "retry_not_neutral"
        raise RuntimeError(message)
    return DemoReport(initial, final, late, host.saved_count, host.model_calls, 1)


type JsonValue = str | int | bool | list[JsonValue] | dict[str, JsonValue] | None


def _decode(decoder: Callable[[str], JsonValue], value: str) -> JsonValue:
    return decoder(value)


def _module_origins(site: Path) -> dict[str, JsonValue]:
    forbidden = {"rag_access_guard_api", "fastapi", "sqlalchemy", "ollama", "torch", "transformers"}
    origins: dict[str, JsonValue] = {}
    for name, module in tuple(sys.modules.items()):
        if name.split(".", 1)[0] in forbidden:
            message = "infrastructure_imported"
            raise RuntimeError(message)
        if name == "rag_access_guard" or name.startswith("rag_access_guard."):
            path = Path(module.__file__ or "").resolve()
            if not path.is_relative_to(site):
                message = "external_core_origin"
                raise RuntimeError(message)
            origins[name] = str(path)
    installed = {
        dist.metadata["Name"].lower().replace("-", "_")
        for dist in importlib.metadata.distributions()
    }
    if forbidden & installed:
        message = "infrastructure_installed"
        raise RuntimeError(message)
    return origins


def _site_paths(site: Path, prefix: Path) -> None:
    for pth in site.glob("*.pth"):
        for line in pth.read_text(encoding="utf-8").splitlines():
            entry = line.strip()
            if (
                entry
                and not entry.startswith("#")
                and (
                    entry.startswith("import ")
                    or not (site / entry).resolve().is_relative_to(prefix)
                )
            ):
                message = "unverified_site_path"
                raise RuntimeError(message)


def _installed_bytes(wheel: Path, site: Path, dist: importlib.metadata.Distribution) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = {name for name in archive.namelist() if not name.endswith("/")}
        for name in names:
            if name.endswith("/RECORD"):
                continue
            target = (site / name).resolve()
            data = archive.read(name)
            if (
                not target.is_relative_to(site)
                or not target.is_file()
                or target.stat().st_size != len(data)
                or sha256(target.read_bytes()).digest() != sha256(data).digest()
            ):
                message = "installed_file_mismatch"
                raise RuntimeError(message)
        _installed_extras(site, dist, names)


def _installed_extras(site: Path, dist: importlib.metadata.Distribution, names: set[str]) -> None:
    for path in (site / "rag_access_guard").rglob("*"):
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and path.relative_to(site).as_posix() not in names
        ):
            message = "unexpected_package_file"
            raise RuntimeError(message)
    for path in dist.files or ():
        name = str(path).replace("\\", "/")
        if (
            name not in names
            and not ("/__pycache__/" in name and name.endswith(".pyc"))
            and name.rsplit("/", 1)[-1] not in {"INSTALLER", "REQUESTED", "direct_url.json"}
        ):
            message = "unexpected_installed_record"
            raise RuntimeError(message)


def verify_install(wheel: Path) -> dict[str, JsonValue]:
    """Bind every loaded core module and installed byte to the exact non-editable wheel."""
    site = Path(sysconfig.get_paths()["purelib"]).resolve()
    prefix = Path(sys.prefix).resolve()
    if not site.is_relative_to(prefix) or sys.prefix == sys.base_prefix:
        message = "invalid_install_prefix"
        raise RuntimeError(message)
    config = (prefix / "pyvenv.cfg").read_text(encoding="utf-8").lower()
    if "include-system-site-packages = false" not in config:
        message = "system_site_enabled"
        raise RuntimeError(message)
    origins = _module_origins(site)
    _site_paths(site, prefix)
    dist = importlib.metadata.distribution("rag-access-guard")
    if dist.metadata["Name"] != "rag-access-guard" or dist.requires:
        message = "invalid_distribution_metadata"
        raise RuntimeError(message)
    digest = sha256(wheel.read_bytes()).hexdigest()
    direct = _decode(json.loads, dist.read_text("direct_url.json") or "null")
    expected: JsonValue = {
        "url": wheel.resolve().as_uri(),
        "archive_info": {"hash": f"sha256={digest}", "hashes": {"sha256": digest}},
    }
    if direct != expected:
        message = "wheel_origin_mismatch"
        raise RuntimeError(message)
    _installed_bytes(wheel, site, dist)
    return {
        "prefix": str(prefix),
        "site": str(site),
        "origins": origins,
        "version": dist.version,
        "wheel_sha256": digest,
        "python": sys.version,
    }


def safe_report(report: DemoReport) -> dict[str, JsonValue]:
    """Expose only states and the redacted final view, never the initial answer."""
    return {
        "initial_read": {
            "state": report.initial_read.state,
            "source_count": len(report.initial_read.sources),
        },
        "final_read": {
            "state": report.final_read.state,
            "user_input": report.final_read.user_input,
            "answer": report.final_read.answer,
            "sources": [],
            "message": report.final_read.message,
        },
        "late_release": {
            "allowed": report.late_release.allowed,
            "reason": report.late_release.reason,
        },
        "saved_count": report.saved_count,
        "model_calls": report.model_calls,
        "retry_count": report.retry_count,
    }


class Arguments(argparse.Namespace):
    """Typed options for the standalone executable."""

    json: bool = False
    verify_install: bool = False
    wheel: Path | None = None


def main() -> int:
    """Run the synthetic cycle and optionally verify the installed wheel in this process."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--json", action="store_true")
    _ = parser.add_argument("--verify-install", action="store_true")
    _ = parser.add_argument("--wheel", type=Path)
    args = parser.parse_args(namespace=Arguments())
    if args.verify_install and args.wheel is None:
        parser.error("--verify-install requires --wheel")
    try:
        report = asyncio.run(run_demo_cycle())
        if (
            report.initial_read.state != "available"
            or report.final_read.state != "unavailable"
            or report.final_read.answer is not None
            or report.final_read.sources
            or report.late_release.allowed
            or report.late_release.reason != "stale_revision"
            or (report.saved_count, report.model_calls, report.retry_count) != (1, 2, 1)
        ):
            return 1
        result = safe_report(report)
        if args.verify_install and args.wheel is not None:
            result["installation"] = verify_install(args.wheel)
        _ = sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
    except Exception:  # noqa: BLE001 -- CLI must not expose protected text or exception details.
        _ = sys.stderr.write('{"error":"demo_failed"}\n')
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
