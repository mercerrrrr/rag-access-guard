"""Read-only scientific gold admission through freshly authenticated actor gates."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from experiments.demo_inputs import DemoInputs
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.demo.evaluation_admission import validate_case_evidence, verify_marker
from rag_access_guard_api.demo.evaluation_contracts import DemoEvaluationCase
from rag_access_guard_api.services.security import PolicyUnitOfWork


@dataclass(frozen=True, slots=True)
class DemoAdmission:
    """No grants, versions, migrations or permission repair are performed by evaluation."""

    inputs: DemoInputs
    engine: AsyncEngine

    async def check(self, session: str, case: DemoEvaluationCase) -> None:
        """Bind actor evidence, allowed versions, policy and owned marker in one snapshot."""
        if not self.inputs.unchanged():
            raise DemoDatasetError(code="input_drift")
        async with PolicyUnitOfWork(self.engine).protected_read(session) as uow:
            await verify_marker(uow.connection, self.inputs.marker)
            if uow.revision != self.inputs.dataset.policy_revision:
                raise DemoDatasetError(code="policy_revision_drift")
            await validate_case_evidence(uow, case)
