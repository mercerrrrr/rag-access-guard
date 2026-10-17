import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("profile_id", "model_family"),
    [("qwen25-instruct-small-demo-v1", "qwen2.5"), ("qwen35-extractive-demo-v1", "qwen3.5")],
)
def test_tokenizer_provisioning_normalizes_windows_metadata_and_selects_model_family(
    tmp_path: Path,
    profile_id: str,
    model_family: str,
) -> None:
    script = Path("scripts/provision-model-tokenizer.sh").resolve()
    digest = sha256(b"fixture").hexdigest()
    uv = tmp_path / "uv"
    metadata_line = (
        f"printf '%s\\r\\n' revision Qwen/Fixture {digest} " + f"{digest} {model_family}:fixture"
    )
    _ = uv.write_text(
        f"#!/usr/bin/env bash\n{metadata_line}\n",
        encoding="utf-8",
        newline="\n",
    )
    curl = tmp_path / "curl"
    _ = curl.write_text(
        """#!/usr/bin/env bash
case "$*" in *$'\\r'*) exit 3;; esac
while [[ $# -gt 0 ]]; do
 if [[ $1 == --output ]]; then printf fixture > "$2"; fi
 if [[ $1 == https://* ]]; then printf '%s\\n' "$1" >> "$URL_LOG"; fi
 shift
done
""",
        encoding="utf-8",
        newline="\n",
    )
    uv.chmod(0o700)
    curl.chmod(0o700)
    urls = tmp_path / "urls.txt"
    environment = os.environ.copy()
    environment["PATH"] = str(tmp_path) + os.pathsep + environment["PATH"]
    environment["URL_LOG"] = urls.as_posix()
    _ = environment.pop("RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH", None)
    bash = shutil.which("bash")
    assert bash is not None

    result = subprocess.run(  # noqa: S603 -- fixed tracked script and isolated fixture executables, no shell.
        [bash, script.as_posix(), "--profile", profile_id],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".cache/qwen3/revision/tokenizer.json").read_bytes() == b"fixture"
    assert urls.read_text(encoding="utf-8").splitlines() == [
        "https://huggingface.co/Qwen/Fixture/resolve/revision/tokenizer.json",
        f"https://registry.ollama.ai/v2/library/{model_family}/blobs/sha256:{digest}",
    ]
