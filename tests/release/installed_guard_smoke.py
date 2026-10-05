import asyncio
import importlib.metadata
import json
import sys
import sysconfig
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import rag_access_guard
from rag_access_guard import CandidateChunk, Guard, PolicySnapshot, PreparedContext, SourceRef

PRINCIPAL = UUID(int=1)
THREAD = UUID(int=2)
SOURCE = SourceRef(document_id=UUID(int=3), document_version_id=UUID(int=4), chunk_id=UUID(int=5))
TEXT = "Synthetic release probe"
DIGEST = sha256(TEXT.encode()).hexdigest()


@dataclass(frozen=True)
class ByteCounter:
    identity: str = "release-probe-utf8-bytes-v1"

    def count(self, text: str) -> int:
        return len(text.encode())


@dataclass
class MemoryPolicy:
    granted: bool = True
    revision: int = 1

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        requested = tuple(dict.fromkeys(source_refs))
        active = principal_id == PRINCIPAL
        allowed = tuple(ref for ref in requested if ref == SOURCE and self.granted and active)
        return PolicySnapshot(
            principal_id=principal_id,
            revision=self.revision,
            allowed_refs=allowed,
            denied_refs=tuple(ref for ref in requested if ref not in allowed),
            provenance_valid=all(ref == SOURCE for ref in requested),
            principal_active=active,
            thread_owned=None if thread_id is None else active and thread_id == THREAD,
            canonical_chunk_hashes=tuple((ref, DIGEST) for ref in allowed),
        )


async def smoke() -> dict[str, bool]:
    guard = Guard(ByteCounter())
    policy = MemoryPolicy()
    chunk = CandidateChunk(
        source_ref=SOURCE, text=TEXT, content_sha256=DIGEST, token_count=len(TEXT)
    )
    prepared = await guard.prepare_context(PRINCIPAL, (chunk,), (), policy)
    assert isinstance(prepared, PreparedContext)
    release = await guard.authorize_release(PRINCIPAL, THREAD, prepared, policy)
    read = await guard.authorize_read(PRINCIPAL, (SOURCE,), policy)
    other = await guard.authorize_read(UUID(int=6), (SOURCE,), policy)
    unknown_ref = SourceRef(
        document_id=UUID(int=7), document_version_id=UUID(int=8), chunk_id=UUID(int=9)
    )
    unknown = await guard.authorize_read(PRINCIPAL, (unknown_ref,), policy)
    policy.granted = False
    policy.revision += 1
    revoked_read = await guard.authorize_read(PRINCIPAL, (SOURCE,), policy)
    revoked_release = await guard.authorize_release(PRINCIPAL, THREAD, prepared, policy)
    revoked_context = await guard.prepare_context(PRINCIPAL, (chunk,), (), policy)
    return {
        "prepare_allowed": prepared.source_refs == (SOURCE,) and TEXT in prepared.model_context,
        "release_allowed": release.allowed,
        "read_allowed": read.allowed,
        "read_revoked": not revoked_read.allowed,
        "release_revoked": not revoked_release.allowed
        and revoked_release.reason == "stale_revision",
        "prepare_revoked": isinstance(revoked_context, PreparedContext)
        and not revoked_context.source_refs
        and TEXT not in revoked_context.model_context,
        "unknown_denied": not unknown.allowed and unknown.reason == "invalid_provenance",
        "other_principal_denied": not other.allowed,
    }


def main() -> int:
    site = Path(sysconfig.get_paths()["purelib"]).resolve()
    assert site.is_relative_to(Path(sys.prefix).resolve())
    assert Path(rag_access_guard.__file__).resolve().is_relative_to(site)
    assert not importlib.metadata.distribution("rag-access-guard").requires
    result = asyncio.run(smoke())
    for name, module in tuple(sys.modules.items()):
        if name == "rag_access_guard" or name.startswith("rag_access_guard."):
            assert module.__file__ is not None
            assert Path(module.__file__).resolve().is_relative_to(site)
    assert not {"fastapi", "sqlalchemy", "rag_access_guard_api", "experiments"} & sys.modules.keys()
    assert result
    assert all(result.values())
    _ = sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
