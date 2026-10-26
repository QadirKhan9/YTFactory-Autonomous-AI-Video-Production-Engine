"""LLM router — the anti-lock-in chokepoint (D-005).

Every LLM call funnels through Router.invoke(); vendors are never called
directly. The router owns:
  * tier resolution from registry/providers.yaml (provider death = YAML edit)
  * model-family decorrelation (verifier must differ from writer's family)
  * provider demotion after repeated errors
  * one JSON-repair attempt on SCHEMA failures (NFR-R-03)
  * --dry-run: validates the agent's stub payload against its Pydantic schema,
    so even zero-API runs exercise the policy firewall.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional, Type

import yaml
from pydantic import BaseModel, ValidationError

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "registry" / "providers.yaml"

REPAIR_FEEDBACK = (
    "Your previous reply failed JSON schema validation. "
    "Reply again with ONLY a JSON object fixing these errors:\n{errors}"
)


class RouterError(RuntimeError):
    """No model in the tier could produce a schema-valid response."""


# class-name markers for errors that heal on their own (429/5xx/connection);
# everything else (auth, bad model id, schema) keeps the long demotion
TRANSIENT_MARKERS = (
    "RateLimit",
    "ServiceUnavailable",
    "InternalServerError",
    "Overloaded",
    "APIConnection",
    "ConnectionError",
    "Timeout",
)


def _is_transient(exc: BaseException) -> bool:
    name = type(exc).__name__
    return any(marker in name for marker in TRANSIENT_MARKERS)


def model_family(model: str) -> str:
    """'groq/llama-3.3-70b' -> 'llama'; 'gemini/flash-lite' -> 'flash'."""
    name = model.split("/", 1)[-1]
    return re.split(r"[-_]", name)[0].lower()


class Router:
    def __init__(
        self,
        dry_run: bool = False,
        registry_path: str | Path = REGISTRY_PATH,
    ):
        self.dry_run = dry_run
        self.registry = yaml.safe_load(Path(registry_path).read_text(encoding="utf-8"))
        self._demoted: dict[str, tuple[float, bool]] = {}  # model -> (expiry, transient)
        self.last_usage: dict[str, Any] = {}
        self.usage_log: list[dict[str, Any]] = []  # drained per step by record_usage

    # -- registry ---------------------------------------------------------
    def tier_models(self, tier: str) -> list[str]:
        models = self.registry.get("tiers", {}).get(tier)
        if not models:
            raise RouterError(f"unknown or empty tier: {tier!r}")
        return list(models)

    def _live_models(self, tier: str) -> list[str]:
        now = time.monotonic()
        return [
            m for m in self.tier_models(tier) if self._demoted.get(m, (0.0, False))[0] <= now
        ]

    def _demote(self, model: str, reason: str, transient: bool = False) -> None:
        demotion = self.registry.get("demotion", {})
        if transient:
            seconds = float(demotion.get("transient_demote_seconds", 60))
            label = f"{seconds:.0f}s (transient)"
        else:
            seconds = float(demotion.get("demote_minutes", 60)) * 60
            label = f"{seconds / 60:.0f} min"
        self._demoted[model] = (time.monotonic() + seconds, transient)
        print(f"[router] demoted {model} for {label}: {reason}")

    def _recover_wait(self, tier: str) -> float:
        """Seconds until the earliest demoted model heals; 0 if not worth waiting."""
        if self._live_models(tier):
            return 0.0
        expiries = [
            self._demoted[m][0] for m in self.tier_models(tier) if m in self._demoted
        ]
        if not expiries:
            return 0.0
        wait = min(expiries) - time.monotonic()
        cap = float(self.registry.get("demotion", {}).get("max_recover_wait_s", 75))
        return wait if 0 < wait <= cap else 0.0

    def assert_decorrelated(self, tier_a: str, model_a: str, tier_b: str, model_b: str) -> None:
        """Raise if two roles that must differ share a model family."""
        if tier_a == tier_b and len(self.tier_models(tier_a)) == 1:
            raise RouterError(f"tier {tier_a!r} has a single model; decorrelation impossible")
        if model_family(model_a) == model_family(model_b):
            raise RouterError(
                f"decorrelation violated: {model_a} and {model_b} share family "
                f"{model_family(model_a)!r}"
            )

    # -- dispatch ---------------------------------------------------------
    def invoke(
        self,
        *,
        agent: str,
        tier: str,
        messages: list[dict],
        schema: Type[BaseModel],
        stub: Optional[dict] = None,
        forbid_family: Optional[str] = None,
    ) -> BaseModel:
        """Return a schema-validated model. Records usage in self.last_usage."""
        if self.dry_run:
            if stub is None:
                raise RouterError(f"dry-run stub missing for agent {agent}")
            self.last_usage = {"provider": "dry-run", "model": "stub", "tokens_in": 0, "tokens_out": 0}
            return schema.model_validate(stub)

        # a failed invoke must never leak the PREVIOUS call's usage (NFR-F-03)
        self.last_usage = {}

        errors: list[str] = []
        for cycle in (0, 1):  # one bounded heal-wait for rate-limit storms
            models = self._live_models(tier)
            if not models:
                wait = self._recover_wait(tier)
                if wait and cycle == 0:
                    print(
                        f"[router] tier {tier!r} rate-limited; waiting {wait:.0f}s "
                        "for the earliest model to heal"
                    )
                    time.sleep(wait)
                    continue
                raise RouterError(f"all models in tier {tier!r} are demoted")

            for model in models:
                if forbid_family and model_family(model) == forbid_family:
                    continue
                try:
                    import litellm  # lazy: only needed for real API calls
                except ImportError as exc:
                    raise RouterError(
                        "litellm is not installed; run `pip install litellm` for real (non-dry) runs"
                    ) from exc

                payload_messages = list(messages)
                for attempt in (0, 1):  # 1 original + 1 repair (NFR-R-03)
                    try:
                        resp = litellm.completion(model=model, messages=payload_messages, temperature=0.3)
                    except Exception as exc:  # 429/5xx/network -> failover (RATE_LIMIT/TRANSIENT)
                        cls = type(exc).__name__
                        errors.append(f"{model}: {cls}: {exc}")
                        self._demote(model, f"{cls}: {exc}", transient=_is_transient(exc))
                        break

                    usage = {
                        "provider": model.split("/", 1)[0],
                        "model": model,
                        "tokens_in": _usage(resp, "prompt_tokens"),
                        "tokens_out": _usage(resp, "completion_tokens"),
                    }
                    self.last_usage = usage
                    self.usage_log.append(dict(usage))
                    text = resp.choices[0].message.content or ""
                    try:
                        data = _extract_json(text)
                        return schema.model_validate(data)
                    except (json.JSONDecodeError, ValidationError) as exc:
                        if attempt == 0:
                            payload_messages = payload_messages + [
                                {"role": "assistant", "content": text},
                                {"role": "user", "content": REPAIR_FEEDBACK.format(errors=str(exc))},
                            ]
                            continue
                        errors.append(f"{model}: schema: {exc}")
                        break  # repair exhausted -> next model in tier

            # every live model failed this cycle — one short heal-wait if any
            # failure was transient (free-tier 429s retry in 13-51s)
            wait = self._recover_wait(tier)
            if wait and cycle == 0:
                print(
                    f"[router] tier {tier!r} exhausted transiently; waiting "
                    f"{wait:.0f}s for recovery"
                )
                time.sleep(wait)
                continue
            break

        raise RouterError(f"tier {tier!r} exhausted: " + " | ".join(errors))


def _usage(resp: Any, field: str) -> int:
    try:
        return int(getattr(resp.usage, field, 0) or 0)
    except Exception:
        return 0


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ``` fences)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise json.JSONDecodeError("no JSON object found", candidate, 0)
    return json.loads(candidate[start : end + 1])
