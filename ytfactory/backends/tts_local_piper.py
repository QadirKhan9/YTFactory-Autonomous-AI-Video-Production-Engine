"""Local Piper backend (MIT, CPU) — final floor rung of FR-A-05.

Voice model: `models/piper/<voice>.onnx` (config `piper_model` or env
`PIPER_MODEL_PATH`). Default voice: en_US-lessac-medium.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

from ytfactory.backends.base import (
    DEFAULT_SPEED,
    DEFAULT_VOICE,
    BackendError,
    flatten_paths,
    missing_sentences,
    model_slot,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_VOICE_ID = "en_US-lessac-medium"
DEFAULT_MODEL = REPO_ROOT / "models" / "piper" / f"{DEFAULT_VOICE_ID}.onnx"
HF_REPO = "rhasspy/piper-voices"
HF_PATH = "en/en_US/lessac/medium"


def resolve_model(cfg: dict | None = None) -> Path:
    cfg = cfg or {}
    return Path(
        cfg.get("piper_model")
        or os.environ.get("PIPER_MODEL_PATH")
        or DEFAULT_MODEL
    )


def ensure_model() -> Path:
    """Download the default Piper voice once (~63 MB). Lazy."""
    model = resolve_model()
    if model.exists():
        return model
    from huggingface_hub import hf_hub_download

    model.parent.mkdir(parents=True, exist_ok=True)
    for hf_name, target in (
        (f"{HF_PATH}/{model.name}", model),
        (f"{HF_PATH}/{model.name}.json", model.parent / (model.name + ".json")),
    ):
        if not target.exists():
            got = hf_hub_download(repo_id=HF_REPO, filename=hf_name)
            if Path(got).resolve() != target.resolve():
                target.write_bytes(Path(got).read_bytes())
    return model


class PiperBackend:
    name = "local_piper"

    def __init__(self, cfg: dict | None = None):
        self.cfg = dict(cfg or {})
        self.model_path = resolve_model(self.cfg)
        self.voice_id = self.cfg.get("piper_voice", DEFAULT_VOICE_ID)
        self.speed = float(self.cfg.get("tts_speed", DEFAULT_SPEED))

    def available(self) -> bool:
        if not self.model_path.exists():
            return False
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return True

    def synthesize(self, scenes: list[dict], out_dir: str) -> list[str]:
        from ytfactory.backends.base import sentence_cache_name
        from ytfactory.workers.audio import write_wav

        Path(out_dir).mkdir(parents=True, exist_ok=True)
        todo = missing_sentences(scenes, out_dir)
        if todo:
            model = ensure_model()
            with model_slot:  # NFR-M-02
                from piper import PiperVoice

                voice = PiperVoice.load(str(model))
                from piper import SynthesisConfig

                syn_cfg = SynthesisConfig(length_scale=1.0 / max(self.speed, 0.1))
                for sent in todo:
                    target = Path(out_dir) / sentence_cache_name(
                        sent["ord"], sent["text"], sent.get("voice", DEFAULT_VOICE)
                    )
                    try:
                        with wave.open(str(target), "wb") as wf:
                            voice.synthesize_wav(sent["text"], wf, syn_config=syn_cfg)
                    except Exception as exc:
                        target.unlink(missing_ok=True)
                        raise BackendError(
                            f"piper failed on sentence {sent['ord']}: {exc}"
                        )
        return flatten_paths(out_dir, scenes)
