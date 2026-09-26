#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = []
# ///
# Run from the synchronized workspace: uv run --frozen python scripts/check_ollama_chat.py
"""Manually probe the pinned local model; this is not a replacement for API tests."""

from __future__ import annotations

from time import monotonic

import anyio

from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.schemas.generation import GenerationUnavailable


async def main() -> None:
    """Use only synthetic input and print final content, never model reasoning."""
    started = monotonic()
    try:
        adapter = OllamaAdapter()
        answer = await adapter.generate(
            user_input="Which two safeguards are required for corporate remote access?",
            system_supplied_context="Corporate remote access requires VPN and MFA.",
        )
    except GenerationUnavailable:
        print("Local generation unavailable; verification failed.")  # noqa: T201
        raise SystemExit(1) from None
    print(answer)  # noqa: T201
    print(f"Verified local generation: {monotonic() - started:.2f}s")  # noqa: T201


if __name__ == "__main__":
    anyio.run(main)
