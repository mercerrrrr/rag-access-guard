"""Validate complete external inputs and output identities before setup writes."""

from dataclasses import dataclass
from pathlib import Path

from rag_access_guard_api.demo.access import AccessDefinition
from rag_access_guard_api.demo.contracts import DemoConfig, DemoMarker
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.files import load_private_json
from rag_access_guard_api.demo.paths import private_path
from rag_access_guard_api.schemas.demo_corpus import DemoCorpusManifest
from rag_access_guard_api.services.demo_corpus import load_manifest, validate_corpus


@dataclass(frozen=True, slots=True)
class SeedInputs:
    """Validated private input identities, independent of database or session state."""

    manifest: DemoCorpusManifest
    access: AccessDefinition
    marker: DemoMarker
    manifest_sha256: str
    corpus_root: Path


def read_inputs(config: DemoConfig, accepted: frozenset[str]) -> SeedInputs:
    """Validation and ordinary parsers run before any database or credential mutation."""
    manifest_path = private_path(config.corpus_manifest)
    manifest = load_manifest(manifest_path)
    access = load_private_json(config.access_definition, AccessDefinition)
    marker = load_private_json(config.ownership_manifest, DemoMarker)
    keys = {document.key for document in manifest.documents}
    if config.dataset_id != manifest.dataset_id or marker.dataset_id != config.dataset_id:
        raise DemoOwnershipError(code="dataset_mismatch")
    if not accepted <= keys:
        raise DemoOwnershipError(code="unknown_content_key")
    if any(grant.document_key not in keys for grant in access.grants):
        raise DemoOwnershipError(code="unknown_grant_document")
    paths = [
        config.corpus_manifest,
        config.ownership_manifest,
        config.access_definition,
        config.credentials_path,
        config.retrieval_config,
    ]
    outputs = [path for path in (config.report_path, config.suggestions_path) if path is not None]
    if len({private_path(path) for path in paths + outputs}) != len(paths + outputs):
        raise DemoOwnershipError(code="path_collision")
    if any(private_path(path).exists() for path in outputs):
        raise DemoOwnershipError(code="private_output_exists")
    root = private_path(manifest_path.parent)
    for document in manifest.documents:
        _ = private_path(root / document.relative_path)
    report = validate_corpus(manifest_path)
    return SeedInputs(manifest, access, marker, report.manifest_sha256, root)
