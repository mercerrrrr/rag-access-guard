"""Completed-step bindings and marker locks participate in caller-owned transactions."""

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.demo.contracts import DemoMarker
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.plan import SeedBinding, SeedLedger
from rag_access_guard_api.persistence import DemoSeedState, DemoSeedStep


async def lock_marker(connection: AsyncConnection, marker: DemoMarker) -> None:
    """Acquire only after policy/session gates; reject a marker replaced since preflight."""
    row = (
        (
            await connection.execute(
                select(
                    DemoSeedState.instance_id,
                    DemoSeedState.dataset_id,
                    DemoSeedState.database_name,
                    DemoSeedState.database_role,
                ).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or DemoMarker.model_validate(dict(row)) != marker:
        raise DemoOwnershipError(code="marker_mismatch")


async def read_ledger(connection: AsyncConnection, marker: DemoMarker) -> SeedLedger:
    """Read only safe keys, server UUIDs, and content/definition digests."""
    rows = (
        await connection.execute(
            select(
                DemoSeedStep.key,
                DemoSeedStep.object_id,
                DemoSeedStep.version_id,
                DemoSeedStep.upload_sha256,
                DemoSeedStep.origin_sha256,
                DemoSeedStep.definition_sha256,
            )
            .where(DemoSeedStep.instance_id == marker.instance_id)
            .order_by(DemoSeedStep.key)
        )
    ).tuples()
    return SeedLedger(tuple(SeedBinding(*row) for row in rows))


async def complete_step(
    connection: AsyncConnection, marker: DemoMarker, binding: SeedBinding
) -> None:
    """Insert completion atomically with the object; content updates replace only its binding."""
    values = {
        "object_id": binding.object_id,
        "version_id": binding.version_id,
        "upload_sha256": binding.upload_sha256,
        "origin_sha256": binding.origin_sha256,
        "definition_sha256": binding.definition_sha256,
    }
    current = (
        await connection.execute(
            select(DemoSeedStep.key).where(
                DemoSeedStep.instance_id == marker.instance_id, DemoSeedStep.key == binding.key
            )
        )
    ).scalar_one_or_none()
    if current is None:
        _ = await connection.execute(
            insert(DemoSeedStep).values(instance_id=marker.instance_id, key=binding.key, **values)
        )
    else:
        _ = await connection.execute(
            update(DemoSeedStep)
            .where(DemoSeedStep.instance_id == marker.instance_id, DemoSeedStep.key == binding.key)
            .values(**values)
        )
