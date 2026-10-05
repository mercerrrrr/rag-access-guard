"""Per-trial decisions with stable aliases for generated database identities."""

import json
import re
from dataclasses import replace
from http import HTTPStatus
from uuid import UUID

from experiments.observations import ArmResult, Observation
from rag_access_guard import SourceRef
from rag_access_guard_api.schemas.access import AccessibleDocuments
from rag_access_guard_api.schemas.chat import ThreadDetail


class Identities:
    """Preserve fixture names and reference equality across all actions in one arm."""

    def __init__(self, arm: ArmResult) -> None:
        """Seed fixture aliases before assigning per-arm provenance identities."""
        self.aliases: dict[UUID, UUID] = {}
        for _, identity in sorted(arm.documents):
            _ = self.identity(identity)
        for observation in arm.observations:
            refs = observation.source_refs + observation.forbidden_refs
            if observation.request is not None:
                refs += observation.request.source_refs + observation.request.forbidden_system_refs
            for ref in refs:
                _ = self.ref(ref)

    def identity(self, value: UUID) -> UUID:
        """Allocate by occurrence, not by the random UUID's lexical order."""
        return self.aliases.setdefault(value, UUID(int=len(self.aliases) + 1))

    def ref(self, value: SourceRef) -> SourceRef:
        """Retain the full document/version/chunk relationship."""
        return SourceRef(
            document_id=self.identity(value.document_id),
            document_version_id=self.identity(value.document_version_id),
            chunk_id=self.identity(value.chunk_id),
        )

    def context(self, value: str) -> str:
        """Replace known provenance UUIDs only; retain actual context text."""
        for match in re.finditer(r'"turn_id"\s*:\s*"([0-9a-f-]{36})"', value):
            _ = self.identity(UUID(match[1]))
        return re.sub(
            r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}",
            lambda match: str(self.aliases.get(UUID(match[0]), UUID(match[0]))),
            value,
        )

    def wire_state(self, observation: Observation) -> str:
        """Preserve read states and listed versions without request IDs or timestamps."""
        if observation.http_status != HTTPStatus.OK:
            return ""
        if observation.surface == "stored_read":
            detail = ThreadDetail.model_validate_json(observation.response_body)
            return json.dumps([turn.state for turn in detail.turns])
        if observation.surface == "document_list":
            documents = AccessibleDocuments.model_validate_json(observation.response_body)
            return json.dumps(
                [
                    (
                        str(self.identity(item.id)),
                        str(self.identity(item.active_version_id)),
                        item.title,
                    )
                    for item in documents.items
                ]
            )
        return ""


def normalized_arm(arm: ArmResult) -> ArmResult:
    """Discard timings and incidental HTTP identities, never policy outcomes."""
    identities = Identities(arm)
    observations: list[Observation] = []
    for observation in arm.observations:
        request = observation.request
        if request is not None:
            request = replace(
                request,
                system_supplied_context=identities.context(request.system_supplied_context),
                source_refs=tuple(map(identities.ref, request.source_refs)),
                forbidden_system_refs=tuple(map(identities.ref, request.forbidden_system_refs)),
            )
        observations.append(
            replace(
                observation,
                source_refs=tuple(map(identities.ref, observation.source_refs)),
                forbidden_refs=tuple(map(identities.ref, observation.forbidden_refs)),
                elapsed_ms=0,
                attempt_elapsed_ms=None,
                http_elapsed_ms=None,
                response_body=identities.wire_state(observation),
                request=request,
            )
        )
    return replace(
        arm,
        documents=tuple((key, identities.identity(value)) for key, value in sorted(arm.documents)),
        observations=tuple(observations),
        attempts=tuple(
            replace(
                attempt,
                elapsed_ms=0,
                stage_ms=tuple((stage, 0) for stage, _ in attempt.stage_ms),
            )
            for attempt in arm.attempts
        ),
    )
