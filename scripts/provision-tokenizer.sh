#!/usr/bin/env bash
set -euo pipefail

revision=614241f622f53c4eeff9890bdc4f31cfecc418b3
destination="${RAG_ACCESS_GUARD_TOKENIZER_PATH:-.cache/e5/${revision}/tokenizer.json}"
mkdir -p "$(dirname "$destination")"
curl --fail --location --retry 3 --connect-timeout 10 --max-time 180 \
  "https://huggingface.co/intfloat/multilingual-e5-small/resolve/${revision}/tokenizer.json" \
  --output "$destination"
printf '%s  %s\n' \
  0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39 \
  "$destination" | sha256sum --check
