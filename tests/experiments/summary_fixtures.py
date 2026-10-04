from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from experiments.observations import ArmRecord, ArmResult, PairResult
from experiments.run_manifest import RunCounts, RunIdentity, RunManifest, TrialSpec
from experiments.trials import TrialRecord


def paired_record() -> TrialRecord:
    trial = TrialSpec(
        trial_id="allowed.0",
        case_id="allowed",
        repetition=0,
        seed=7,
        warmup=False,
        order=("baseline", "guarded"),
        cache_state="uncontrolled",
    )
    baseline = ArmResult(
        arm="baseline",
        config_hash="a" * 64,
        corpus_hash="b" * 64,
        renderer_identity="renderer",
        tokenizer_identity="tokenizer",
        model_identity="fake",
        observations=(),
        documents=(),
    )
    guarded = ArmResult(
        arm="guarded",
        config_hash="a" * 64,
        corpus_hash="b" * 64,
        renderer_identity="renderer",
        tokenizer_identity="tokenizer",
        model_identity="fake",
        observations=(),
        documents=(),
    )
    return TrialRecord(
        trial=trial,
        status="completed",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
        elapsed_ms=20,
        pair=PairResult(
            case_id="allowed",
            seed=7,
            order=trial.order,
            baseline=baseline,
            guarded=guarded,
            comparison_fingerprint="c" * 64,
        ),
        arms=(
            ArmRecord(status="completed", result=baseline),
            ArmRecord(status="completed", result=guarded),
        ),
    )


def persist_run(path: Path, record: TrialRecord) -> RunManifest:
    raw = (record.model_dump_json() + "\n").encode()
    manifest = RunManifest(
        run_id=UUID(int=1),
        status="completed",
        started_at=record.started_at,
        finished_at=record.finished_at,
        dirty=False,
        environment={},
        schedule=(record.trial,),
        counts=RunCounts(scheduled=1, completed=1),
        records_sha256=sha256(raw).hexdigest(),
        identity=RunIdentity(
            git_sha="d" * 40,
            config={
                "llm": "fake",
                "settings": {
                    "comparison": {
                        "model_identity": "fake",
                        "tokenizer_identity": "tokenizer",
                        "renderer_identity": "renderer",
                    }
                },
            },
            corpus_hash="b" * 64,
            scenario_hash="e" * 64,
            lock_hashes={},
            model_tag="fake",
            model_digest="f" * 64,
            runtime_config_hash="a" * 64,
            trial_config_hashes={record.trial.trial_id: "a" * 64},
            runtime_versions={},
        ),
    )
    _ = (path / "records.jsonl").write_bytes(raw)
    _ = (path / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    return manifest
