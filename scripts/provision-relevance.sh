#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s ABSOLUTE_MODEL_DIRECTORY\n' "$0" >&2
  exit 2
fi
destination="$1"
metadata="$(uv run --frozen python -c '
import sys
from pathlib import Path
from rag_access_guard_api.schemas.relevance_config import ARTIFACTS, RelevanceConfig
c = RelevanceConfig(model_path=Path(sys.argv[1]))
print(f"{c.model_id}\t{c.revision}")
for name, size, digest in ARTIFACTS:
    print(f"{name}\t{size}\t{digest}")
' "$destination")"
metadata="${metadata//$'\r'/}"
mapfile -t records <<< "$metadata"
IFS=$'\t' read -r model revision <<< "${records[0]}"
for record in "${records[@]:1}"; do
  IFS=$'\t' read -r name size digest <<< "$record"
  path="$destination/$name"
  mkdir -p "$(dirname "$path")"
  if [[ -f "$path" ]] && printf '%s  %s\n' "$digest" "$path" | sha256sum --check --status; then
    continue
  fi
  temporary="$(mktemp "${path}.download-XXXXXX")"
  trap 'rm -f -- "$temporary"' EXIT
  curl --fail --location --retry 3 --connect-timeout 10 --max-time 300 \
    "https://huggingface.co/${model}/resolve/${revision}/${name}" --output "$temporary"
  test "$(wc -c < "$temporary")" -eq "$size"
  printf '%s  %s\n' "$digest" "$temporary" | sha256sum --check
  mv -- "$temporary" "$path"
  trap - EXIT
done
uv run --frozen python -c '
import sys
from pathlib import Path
from rag_access_guard_api.schemas.relevance_config import RelevanceConfig, verify_relevance_artifacts
c = RelevanceConfig(model_path=Path(sys.argv[1]))
verify_relevance_artifacts(c)
print(c.model_dump_json())
' "$destination"
