#!/usr/bin/env bash
set -euo pipefail

revision=614241f622f53c4eeff9890bdc4f31cfecc418b3
model_dir="${RAG_ACCESS_GUARD_MODEL_PATH:-.cache/e5/${revision}}"
mkdir -p "$model_dir"
RAG_ACCESS_GUARD_TOKENIZER_PATH="$model_dir/tokenizer.json" bash scripts/provision-tokenizer.sh

download() {
  local filename="$1" checksum="$2" temporary
  if [[ -f "$model_dir/$filename" ]] && printf '%s  %s\n' "$checksum" "$model_dir/$filename" | sha256sum --check --status; then
    return
  fi
  temporary="$(mktemp "$model_dir/$filename.part.XXXXXX")"
  if curl --fail --location --retry 3 --connect-timeout 10 --max-time 300 \
      "https://huggingface.co/intfloat/multilingual-e5-small/resolve/${revision}/${filename}" \
      --output "$temporary" && printf '%s  %s\n' "$checksum" "$temporary" | sha256sum --check; then
    mv -- "$temporary" "$model_dir/$filename"
  else
    rm -- "$temporary"
    return 1
  fi
}

download config.json 69137736cab8b8903a07fe8afaafdda25aac55415a12a55d1bffa9f581abf959
download model.safetensors 1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477
printf 'E5 snapshot verified: %s\n' "$revision"
