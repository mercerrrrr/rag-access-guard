#!/usr/bin/env bash
set -euo pipefail

revision=768f209d9ea81521153ed38c47d515654e938aea
destination="${RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH:-.cache/qwen3/${revision}/tokenizer.json}"
mkdir -p "$(dirname "$destination")"
curl --fail --location --retry 3 --connect-timeout 10 --max-time 180 \
  "https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507/resolve/${revision}/tokenizer.json" \
  --output "$destination"
printf '%s  %s\n' \
  aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4 \
  "$destination" | sha256sum --check
template="$(dirname "$destination")/template.txt"
curl --fail --location --retry 3 --connect-timeout 10 --max-time 60 \
  'https://registry.ollama.ai/v2/library/qwen3/blobs/sha256:2d54db2b9bb29ce7db54fea63a891f5859603813c555b1f88b5e0994652897f9' \
  --output "$template"
printf '%s  %s\n' \
  2d54db2b9bb29ce7db54fea63a891f5859603813c555b1f88b5e0994652897f9 \
  "$template" | sha256sum --check
