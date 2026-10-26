"""Idempotency key engine (FR-Res-01, design.md §4.1).

The key hashes the *configured tier* (`cheap`/`writer`/`verifier`/`deterministic`),
never the resolved endpoint: provider failover (§7 error taxonomy) must not
change step identity. The resolved provider/model is recorded in usage_ledger.
"""
from __future__ import annotations

import hashlib
import json

from ytfactory import CODE_VERSION, PROMPT_VERSION

DETERMINISTIC_TIER = "deterministic"


def canonical_inputs(inputs: dict) -> str:
    return json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_idem_key(
    step_name: str,
    inputs: dict,
    prompt_version: str = PROMPT_VERSION,
    model_tier: str = DETERMINISTIC_TIER,
    code_version: str = CODE_VERSION,
) -> str:
    raw_payload = (
        f"{step_name}|{canonical_inputs(inputs)}|{prompt_version}|"
        f"{model_tier}|{code_version}"
    )
    return hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
