"""Local demo-quality input integrity before credentials, output, DB or inference."""

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from experiments.run_environment import GitState, git_state
from experiments.suite_invocation import DemoQualityInvocation
from rag_access_guard_api.demo.access import AccessDefinition
from rag_access_guard_api.demo.contracts import DemoConfig, DemoMarker
from rag_access_guard_api.demo.evaluation import DemoDatasetError, validate_dataset
from rag_access_guard_api.demo.evaluation_contracts import DemoEvaluationDataset
from rag_access_guard_api.demo.files import load_config, load_private_json
from rag_access_guard_api.schemas.retrieval_config import RetrievalConfig, load_retrieval_config
from rag_access_guard_api.services.demo_corpus import load_manifest, validate_corpus
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.model_profiles import get_model_profile


def calibration_cases_hash(dataset: DemoEvaluationDataset) -> str:
    """The threshold identity includes only the twenty preselected calibration cases."""
    cases = [c.model_dump(mode="json") for c in dataset.cases if c.split == "calibration"]
    return sha256(
        json.dumps(cases, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def allowed_snapshot_hash(dataset: DemoEvaluationDataset) -> str:
    """Bind each frozen case/actor to its complete allowed active-version snapshot."""
    snapshot = sorted(
        (c.id, c.principal_login, sorted(str(v) for v in c.allowed_version_ids))
        for c in dataset.cases
    )
    return sha256(
        json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class DemoInputs:
    """Verified local inputs; no caller can silently switch a scientific identity."""

    config: DemoConfig
    dataset: DemoEvaluationDataset
    marker: DemoMarker
    retrieval: RetrievalConfig
    hashes: tuple[tuple[Path, str], ...]
    code: GitState
    repository: Path

    @classmethod
    def load(cls, invocation: DemoQualityInvocation) -> "DemoInputs":
        """Perform local parse/hash validation without opening a connection."""
        config = load_config(invocation.config)
        dataset = load_private_json(invocation.scenarios, DemoEvaluationDataset)
        validate_dataset(dataset)
        marker = load_private_json(config.ownership_manifest, DemoMarker)
        access = load_private_json(config.access_definition, AccessDefinition)
        corpus = load_manifest(config.corpus_manifest)
        report = validate_corpus(config.corpus_manifest)
        profile = get_model_profile(config.model_profile)
        if (
            dataset.corpus_sha256 != report.manifest_sha256
            or dataset.profile_hash != profile_hash(profile)
            or dataset.access_matrix_sha256 != sha256(access.model_dump_json().encode()).hexdigest()
            or dataset.allowed_snapshot_sha256 != allowed_snapshot_hash(dataset)
            or marker.dataset_id != config.dataset_id
            or corpus.dataset_id != config.dataset_id
        ):
            raise DemoDatasetError(code="local_identity_mismatch")
        retrieval = load_retrieval_config(config.retrieval_config)
        if invocation.mode in {"holdout", "rehearsal"} and (
            retrieval.corpus_sha256 != dataset.corpus_sha256
            or retrieval.dataset_sha256 != calibration_cases_hash(dataset)
        ):
            raise DemoDatasetError(code="selected_calibration_mismatch")
        paths = (
            invocation.config,
            invocation.scenarios,
            config.corpus_manifest,
            config.ownership_manifest,
            config.access_definition,
            config.retrieval_config,
            *(config.corpus_manifest.parent / d.relative_path for d in corpus.documents),
        )
        hashes = tuple((p, sha256(p.read_bytes()).hexdigest()) for p in paths)
        repository = Path(__file__).resolve().parents[1]
        return cls(config, dataset, marker, retrieval, hashes, git_state(repository), repository)

    def intact(self) -> bool:
        """Detect byte/code drift independently of the clean-code scientific gate."""
        return git_state(self.repository) == self.code and all(
            sha256(path.read_bytes()).hexdigest() == digest for path, digest in self.hashes
        )

    def unchanged(self) -> bool:
        """Scientific completion requires clean exact code and unchanged external input bytes."""
        return not self.code.dirty and self.intact()
