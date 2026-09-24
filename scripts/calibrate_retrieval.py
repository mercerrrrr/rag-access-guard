#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = ["rag-access-guard-api"]
# ///
# How to run: uv sync --frozen, then from the repository root:
# uv run python scripts/calibrate_retrieval.py --dataset PATH --output PATH
# The workspace uv.lock supplies the runtime and local package dependencies.
"""Run the workspace's offline retrieval calibration command."""

from __future__ import annotations

from rag_access_guard_api.cli.calibrate_retrieval import main

if __name__ == "__main__":
    main()
