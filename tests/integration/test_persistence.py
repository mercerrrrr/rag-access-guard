from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Connection, func, select
from sqlalchemy.orm import Session as DatabaseSession

from rag_access_guard_api.persistence import (
    AuditEvent,
    Document,
    DocumentGrant,
    PolicyState,
    Role,
    Session,
    User,
    UserRole,
)


def test_typed_records_roundtrip_through_migrated_schema(schema_connection: Connection) -> None:
    with DatabaseSession(schema_connection, join_transaction_mode="create_savepoint") as database:
        synthetic_hash = uuid4().hex
        user = User(
            login="reader", display_name="Reader", password_hash=synthetic_hash, is_admin=True
        )
        role = Role(code="engineering", display_name="Engineering")
        database.add_all([user, role])
        database.flush()
        assert database.scalar(select(func.count()).select_from(DocumentGrant)) == 0

        document = Document(title="Synthetic document", created_by=user.id)
        database.add(document)
        database.flush()
        grant = DocumentGrant(document_id=document.id, user_id=user.id, created_by=user.id)
        membership = UserRole(user_id=user.id, role_id=role.id)
        session = Session(
            user_id=user.id,
            token_digest=bytes(range(32)),
            csrf_token_digest=bytes(range(32, 64)),
            absolute_expires_at=datetime.now(UTC) + timedelta(hours=8),
        )
        event = AuditEvent(
            actor_user_id=user.id,
            document_id=document.id,
            grant_id=grant.id,
            event_type="grant_added",
            stage="policy",
            outcome="success",
            policy_revision=0,
        )
        database.add_all([grant, membership, session, event])
        database.flush()
        user_id, grant_id, session_id, event_id = user.id, grant.id, session.id, event.id
        database.expunge_all()

        saved_user = database.get(User, user_id)
        saved_grant = database.get(DocumentGrant, grant_id)
        saved_session = database.get(Session, session_id)
        saved_event = database.get(AuditEvent, event_id)
        state = database.get(PolicyState, 1)
        assert saved_user is not None
        assert saved_user.is_active
        assert saved_user.is_admin
        assert saved_user.created_at.utcoffset() is not None
        assert synthetic_hash not in repr(saved_user)
        assert saved_grant is not None
        assert saved_grant.user_id == user_id
        assert saved_grant.role_id is None
        assert saved_session is not None
        assert saved_session.token_digest == bytes(range(32))
        assert saved_session.csrf_token_digest == bytes(range(32, 64))
        assert (
            saved_session.created_at
            <= saved_session.last_seen_at
            < saved_session.absolute_expires_at
        )
        assert saved_session.revoked_at is None
        assert saved_event is not None
        assert saved_event.grant_id == grant_id
        assert saved_event.source_count == 0
        assert saved_event.event_type == "grant_added"
        assert saved_event.occurred_at.utcoffset() is not None
        assert state is not None
        assert state.revision == 0
        assert state.updated_at.utcoffset() is not None
