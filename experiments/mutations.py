"""Scenario security changes through the same policy-first transaction protocol."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from experiments.fixture_manifest import FixtureManifest, read_fixture
from experiments.scenario_types import (
    DirectRevoke,
    RemoveMembership,
    ReplaceVersion,
    RoleRevoke,
    SessionChange,
)
from experiments.seed import Actor, SeededCorpus, SyntheticEmbedder
from rag_access_guard_api.persistence import Session, User
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.audit import AuditRecord
from rag_access_guard_api.services.grants import revoke_grant
from rag_access_guard_api.services.indexing import ingest_text_version
from rag_access_guard_api.services.roles import set_membership
from rag_access_guard_api.services.security import PolicyUnitOfWork, database_clock

type Mutation = DirectRevoke | RoleRevoke | RemoveMembership | ReplaceVersion | SessionChange


@dataclass(frozen=True, slots=True)
class Mutations:
    """Apply scenario changes only inside an owned experiment database."""

    engine: AsyncEngine
    corpus: SeededCorpus
    actor: Actor
    root: Path
    manifest: FixtureManifest

    async def apply(self, action: Mutation) -> None:
        """Commit the requested mutation before releasing its ordering barrier."""
        admin = self.corpus.actors["admin"]
        if isinstance(action, ReplaceVersion):
            fixture = next(f for f in self.manifest.fixtures if f.key == action.replacement)
            data = await asyncio.to_thread(read_fixture, self.root, fixture)
            _ = await ingest_text_version(
                self.engine,
                admin.token,
                self.corpus.documents[action.document],
                upload=UploadPayload(
                    filename=f"{action.replacement}.txt", media_type="text/plain", data=data
                ),
                csrf_token=admin.csrf,
                embedder=SyntheticEmbedder(),
            )
            return
        async with PolicyUnitOfWork(self.engine).mutation(admin.token) as uow:
            match action:
                case DirectRevoke():
                    await revoke_grant(
                        uow,
                        self.corpus.documents[action.document],
                        self.corpus.grants[("direct", action.document, action.user)],
                    )
                case RoleRevoke():
                    await revoke_grant(
                        uow,
                        self.corpus.documents[action.document],
                        self.corpus.grants[("role", action.document, action.role)],
                    )
                case RemoveMembership():
                    await set_membership(
                        uow,
                        self.corpus.roles[action.role],
                        self.corpus.actors[action.user].user_id,
                        present=False,
                    )
                case SessionChange():
                    now = await database_clock(uow.connection)
                    if action.kind == "deactivate_principal":
                        _ = await uow.connection.execute(
                            update(User)
                            .where(User.id == self.actor.user_id)
                            .values(is_active=False)
                        )
                    elif action.kind == "logout":
                        _ = await uow.connection.execute(
                            update(Session)
                            .where(Session.id == self.actor.session_id)
                            .values(revoked_at=now)
                        )
                    else:
                        _ = await uow.connection.execute(
                            update(Session)
                            .where(Session.id == self.actor.session_id)
                            .values(
                                created_at=now - timedelta(hours=9),
                                last_seen_at=now - timedelta(hours=2),
                                absolute_expires_at=now - timedelta(hours=1),
                            )
                        )
                    _ = await uow.record_change(
                        AuditRecord(
                            event_type="user_changed"
                            if action.kind == "deactivate_principal"
                            else "session_revoked",
                            stage="policy",
                            outcome="success",
                            actor_user_id=admin.user_id,
                            principal_id=self.actor.user_id,
                        )
                    )
