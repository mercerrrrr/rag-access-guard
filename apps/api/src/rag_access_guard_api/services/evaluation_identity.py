"""Bind verified readiness to the actual immutable server-selected profile."""

import json
from hashlib import sha256

from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.evaluation_identity import EvaluationMetadata
from rag_access_guard_api.schemas.generation import InferenceUnavailableError
from rag_access_guard_api.schemas.retrieval_config import load_retrieval_config
from rag_access_guard_api.schemas.system import SystemStatus
from rag_access_guard_api.services.model_profiles import ModelProfile, get_model_profile


def profile_hash(profile: ModelProfile) -> str:
    """Hash every selected model, tokenizer, template, renderer, option and runtime field."""
    payload = {
        "profile_id": profile.profile_id,
        "manifest": profile.manifest.model_dump(mode="json"),
        "tokenizer_sha256": profile.tokenizer_sha256,
        "template_sha256": profile.template_sha256,
        "runtime_version": profile.runtime_version,
        "renderer_revision": profile.renderer_revision,
        "generation_options": profile.options(),
        "rendered_empty_sha256": sha256(
            profile.render(user_input="", system_supplied_context="").encode()
        ).hexdigest(),
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evaluation_metadata(settings: Settings, readiness: SystemStatus) -> EvaluationMetadata:
    """Only checked real local artifacts may support a scientific identity projection."""
    profile = get_model_profile(settings.model_profile)
    if (
        settings.llm_adapter != "ollama"
        or readiness.mode != "local"
        or readiness.model.state != "ready"
        or readiness.search.state != "ready"
        or readiness.model.name != profile.manifest.model_name
    ):
        raise InferenceUnavailableError
    retrieval = load_retrieval_config(settings.retrieval_config_path)
    return EvaluationMetadata(
        profile_hash=profile_hash(profile),
        model_digest=profile.manifest.model_digest,
        renderer_identity=profile.renderer_revision,
        retrieval_config_hash=retrieval.computed_hash(),
        corpus_sha256=retrieval.corpus_sha256,
    )
