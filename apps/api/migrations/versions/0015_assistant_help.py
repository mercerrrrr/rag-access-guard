"""Extend closed server help outcomes without rewriting existing conversations or audits."""

from typing import Final

from alembic import op

revision = "0015_assistant_help"
down_revision = "0014_demo_seed_state"
branch_labels = None
depends_on = None

_TURN_STATE: Final = """(
    (state='pending' AND answer IS NULL AND completed_at IS NULL AND ordinal IS NULL
     AND lease_expires_at IS NOT NULL AND NOT provenance_complete
     AND NOT server_generated_neutral AND neutral_reason IS NULL)
    OR (state='available' AND answer IS NOT NULL AND completed_at IS NOT NULL
     AND ordinal IS NOT NULL AND ordinal > 0 AND provenance_complete
     AND NOT server_generated_neutral AND neutral_reason IS NULL)
    OR (state='neutral' AND answer IS NULL AND completed_at IS NOT NULL
     AND ordinal IS NOT NULL AND ordinal > 0 AND NOT provenance_complete
     AND server_generated_neutral AND neutral_reason IN
     ('no_context','generation_unavailable','policy_changed','interrupted'))
    ) IS TRUE"""
_AUDIT_EVENTS: Final = """event_type IN (
    'session_created', 'session_revoked', 'login_denied',
    'user_changed', 'role_changed', 'membership_added', 'membership_removed',
    'document_changed', 'grant_added', 'grant_removed', 'access_checked')"""


def _replace(*, help_enabled: bool) -> None:
    turn = _TURN_STATE
    audit = _AUDIT_EVENTS
    if help_enabled:
        turn = turn.replace("'interrupted'", "'interrupted','assistant_help'")
        audit = audit.replace("'access_checked'", "'access_checked','assistant_help'")
    op.drop_constraint("ck_chat_turns_state", "chat_turns", type_="check")
    op.create_check_constraint("ck_chat_turns_state", "chat_turns", turn)
    op.drop_constraint("ck_audit_events_event_type", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_event_type", "audit_events", audit)


def upgrade() -> None:
    """Permit one new server reason and content-free event while preserving all rows."""
    _replace(help_enabled=True)


def downgrade() -> None:
    """Refuse a downgrade whose old vocabulary cannot represent persisted help records."""
    op.execute("LOCK TABLE chat_turns, audit_events IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM chat_turns WHERE neutral_reason='assistant_help')
        OR EXISTS(SELECT 1 FROM audit_events WHERE event_type='assistant_help') THEN
            RAISE EXCEPTION 'help downgrade would discard supported history';
        END IF;
    END $$""")
    _replace(help_enabled=False)
