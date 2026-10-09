#!/usr/bin/env bash
set -euo pipefail

profile=qwen3-thinking-legacy-v1
if [[ $# -ne 0 ]]; then
  if [[ $# -ne 2 || "$1" != --profile ]]; then
    printf 'Usage: %s [--profile PROFILE_ID]\n' "$0" >&2
    exit 2
  fi
  profile="$2"
fi
metadata="$(uv run --frozen python -c '
import sys
from rag_access_guard_api.services.model_profiles import get_model_profile
p = get_model_profile(sys.argv[1])
print("\n".join((p.manifest.tokenizer_revision, p.manifest.tokenizer_id, p.tokenizer_sha256, p.template_sha256)))
' "$profile")"
mapfile -t bundle <<< "$metadata"
revision="${bundle[0]}"
destination="${RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH:-.cache/qwen3/${revision}/tokenizer.json}"
mkdir -p "$(dirname "$destination")"
curl --fail --location --retry 3 --connect-timeout 10 --max-time 180 \
  "https://huggingface.co/${bundle[1]}/resolve/${revision}/tokenizer.json" \
  --output "$destination"
printf '%s  %s\n' \
  "${bundle[2]}" \
  "$destination" | sha256sum --check
if [[ "$profile" == qwen3-thinking-legacy-v1 ]]; then
  template="$(dirname "$destination")/template.txt"
  curl --fail --location --retry 3 --connect-timeout 10 --max-time 60 \
    "https://registry.ollama.ai/v2/library/qwen3/blobs/sha256:${bundle[3]}" \
    --output "$template"
  printf '%s  %s\n' "${bundle[3]}" "$template" | sha256sum --check
fi
