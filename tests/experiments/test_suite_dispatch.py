import subprocess
import sys
from pathlib import Path

from experiments.suite_dispatch import SecurityInvocation, parse_invocation


def test_demo_requires_explicit_mode_before_output(tmp_path: Path) -> None:
    # Given a demo invocation without its required mode.
    output = tmp_path / "missing-mode"
    # When the real CLI parses it.
    result = subprocess.run(  # noqa: S603 — Fixed interpreter and owned fixture arguments.
        [
            sys.executable,
            "-m",
            "experiments.run",
            "--suite",
            "demo-quality",
            "--config",
            str(tmp_path / "config.json"),
            "--scenarios",
            str(tmp_path / "dataset.json"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    # Then mode validation rejects before file reads or writes.
    assert result.returncode == 2
    assert "--mode" in result.stderr
    assert not output.exists()


def test_security_defaults_remain_legacy(tmp_path: Path) -> None:

    # Given only the historically required output.
    # When the shared parser selects its default suite.
    invocation = parse_invocation(["--output", str(tmp_path / "security")])
    # Then the original RunOptions values are preserved.
    assert isinstance(invocation, SecurityInvocation)
    assert invocation.options.seed == 7
    assert invocation.options.repetitions == 1
    assert invocation.options.llm == "fake"
    assert invocation.options.scenarios == Path("experiments/scenarios.json")
    assert invocation.options.config == Path("experiments/default_config.json")
    assert invocation.options.reproduce is None
