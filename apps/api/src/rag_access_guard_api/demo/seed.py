"""Owned resumable orchestration above existing security and ingestion services."""

from dataclasses import dataclass

from anyio.to_thread import run_sync
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from rag_access_guard_api.demo import bootstrap
from rag_access_guard_api.demo.accounts import (
    authenticate_operator,
    create_seed_user,
    credential_for,
)
from rag_access_guard_api.demo.contracts import DemoConfig, SeedCounts, SeedReport
from rag_access_guard_api.demo.credentials import CredentialRecovery, ensure_credentials
from rag_access_guard_api.demo.documents import SeedDocumentStep, apply_document
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.inputs import SeedInputs, read_inputs
from rag_access_guard_api.demo.ledger import lock_marker
from rag_access_guard_api.demo.mutations import SeedAuthority, apply_access
from rag_access_guard_api.demo.plan import SeedLedger, SeedPlan, SeedState, plan_seed
from rag_access_guard_api.demo.private_write import owner_only
from rag_access_guard_api.demo.reports import (
    export_suggestions,
    output_locations,
    report_bindings,
    write_outputs,
)
from rag_access_guard_api.demo.snapshot import snapshot
from rag_access_guard_api.services.security import PolicyUnitOfWork, lock_policy


@dataclass(frozen=True, slots=True)
class SeedOptions:
    """Explicit mutation and individually approved content changes."""

    apply: bool = False
    initialize_owned_database: bool = False
    accepted_content_updates: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SeedWork:
    """Bind a planned run to the inputs and completed ledger that produced it."""

    inputs: SeedInputs
    plan: SeedPlan
    ledger: SeedLedger


async def read_state(engine: AsyncEngine, inputs: SeedInputs) -> tuple[SeedLedger, SeedState, int]:
    """Read owned metadata under the same policy-first order as mutations."""
    async with engine.begin() as connection:
        revision = await lock_policy(connection, exclusive=False)
        await lock_marker(connection, inputs.marker)
        ledger, state = await snapshot(connection, inputs.marker, inputs.access)
    return ledger, state, revision


def validate_apply(config: DemoConfig, options: SeedOptions, work: SeedWork) -> None:
    """Reject drift, unapproved versions, lost credentials, and unsafe secret parents."""
    plan, ledger = work.plan, work.ledger
    if plan.drift:
        raise DemoOwnershipError(code="tracked_object_drift")
    if any(change.key not in options.accepted_content_updates for change in plan.content_changes):
        raise DemoOwnershipError(code="content_update_required")
    if (
        any(binding.key.startswith("user:") for binding in ledger.bindings)
        and not config.credentials_path.exists()
    ):
        raise DemoOwnershipError(code="credential_missing")
    parents = [config.credentials_path.parent, config.ownership_manifest.parent / "seed-runs"]
    parents.extend(
        path.parent for path in (config.report_path, config.suggestions_path) if path is not None
    )
    for parent in parents:
        if parent.exists():
            owner_only(parent)


def counts(inputs: SeedInputs, *, objects: int = 0, versions: int = 0) -> SeedCounts:
    """Distinguish desired totals from changes committed during this run."""
    return SeedCounts(
        users=len(inputs.access.users),
        roles=len(inputs.access.roles),
        memberships=len(inputs.access.memberships),
        grants=len(inputs.access.grants),
        documents=len(inputs.manifest.documents),
        new_objects=objects,
        new_versions=versions,
    )


async def apply_steps(
    engine: AsyncEngine, config: DemoConfig, work: SeedWork
) -> tuple[SeedAuthority, int, int]:
    """Trusted local account creation precedes ordinary authenticated administrative steps."""
    inputs, plan, ledger = work.inputs, work.plan, work.ledger
    completed_users = frozenset(
        binding.key.removeprefix("user:")
        for binding in ledger.bindings
        if binding.key.startswith("user:")
    )
    journal = await run_sync(
        ensure_credentials,
        config.credentials_path,
        CredentialRecovery(inputs.marker, inputs.access, completed_users),
    )
    created = 0
    for user in sorted(inputs.access.users, key=lambda item: (not item.is_admin, item.login)):
        if f"user:{user.login}" in plan.planned_new_objects:
            created += await create_seed_user(engine, user, journal)
    operator = next(user for user in inputs.access.users if user.is_admin)
    _, token, csrf = await authenticate_operator(
        engine, config, credential_for(journal, operator.login)
    )
    authority = SeedAuthority(PolicyUnitOfWork(engine), inputs.marker, token, csrf)
    tracked = {binding.key: binding for binding in ledger.bindings}
    changed = {change.key for change in plan.content_changes}
    versions = 0
    for document in inputs.manifest.documents:
        key = f"document:{document.key}"
        if key in plan.planned_new_objects or document.key in changed:
            committed = await apply_document(
                authority, inputs, SeedDocumentStep(document, tracked.get(key))
            )
            versions += committed
            created += committed and tracked.get(key) is None
    created += await apply_access(authority, inputs.access)
    return authority, created, versions


async def seed_demo(config: DemoConfig, options: SeedOptions) -> SeedReport:
    """Apply only eligible incomplete steps; dry-run uses the same target guard."""
    if options.initialize_owned_database and not options.apply:
        raise DemoOwnershipError(code="initialize_requires_apply")
    inputs = await run_sync(read_inputs, config, options.accepted_content_updates)
    marker = await run_sync(
        lambda: bootstrap.prepare_owned_database(config, apply=False, initialize=False)
    )
    engine = create_async_engine(
        config.database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"connect_timeout": 2},
    )
    try:
        ledger, state, before = (
            await read_state(engine, inputs)
            if marker is not None
            else (SeedLedger(), SeedState(inputs.access), None)
        )
        plan = plan_seed(inputs.manifest, ledger, state)
        if options.apply:
            validate_apply(config, options, SeedWork(inputs, plan, ledger))
            if marker is None and not options.initialize_owned_database:
                raise DemoOwnershipError(code="initialize_required")
        if not options.apply:
            report_path, _ = await run_sync(output_locations, config)
            report = SeedReport(
                mode="dry_run",
                instance_id=inputs.marker.instance_id,
                dataset_id=config.dataset_id,
                manifest_sha256=inputs.manifest_sha256,
                counts=counts(inputs),
                plan=plan,
                documents=report_bindings(ledger),
                policy_revision_before=before,
                policy_revision_after=before,
                report_path=report_path,
            )
            return await run_sync(write_outputs, report, None)
        _ = await run_sync(
            lambda: bootstrap.prepare_owned_database(
                config, apply=True, initialize=options.initialize_owned_database
            )
        )
        async with engine.connect() as coordinator:
            _ = await coordinator.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": bootstrap.SEED_LOCK}
            )
            await coordinator.commit()
            try:
                ledger, state, before = await read_state(engine, inputs)
                plan = plan_seed(inputs.manifest, ledger, state)
                work = SeedWork(inputs, plan, ledger)
                validate_apply(config, options, work)
                report_path, suggestions_path = await run_sync(output_locations, config)
                authority, created, versions = await apply_steps(engine, config, work)
                final_ledger, final_state, after = await read_state(engine, inputs)
                if plan_seed(inputs.manifest, final_ledger, final_state).drift:
                    raise DemoOwnershipError(code="tracked_object_drift")
                suggestions = await export_suggestions(authority, inputs, final_ledger)
                report = SeedReport(
                    mode="applied",
                    instance_id=inputs.marker.instance_id,
                    dataset_id=config.dataset_id,
                    manifest_sha256=inputs.manifest_sha256,
                    counts=counts(inputs, objects=created, versions=versions),
                    plan=plan,
                    documents=report_bindings(final_ledger),
                    policy_revision_before=before,
                    policy_revision_after=after,
                    report_path=report_path,
                    suggestions_path=suggestions_path,
                )
                return await run_sync(write_outputs, report, suggestions)
            finally:
                _ = await coordinator.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": bootstrap.SEED_LOCK}
                )
                await coordinator.commit()
    finally:
        await engine.dispose()
