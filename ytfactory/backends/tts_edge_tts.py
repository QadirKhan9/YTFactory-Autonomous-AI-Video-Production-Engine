"""Edge-TTS backend (Microsoft free cloud TTS) — zero-model-download fallback.

Uses the `edge-tts` package which connects to Microsoft's neural TTS service
for free with no API key. Quality is high (neural voices).
Fallback rung after Kokoro/Piper; always available when edge-tts is installed.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from ytfactory.backends.base import (
    DEFAULT_VOICE,
    BackendError,
    flatten_paths,
    missing_sentences,
    sentence_cache_name,
)

DEFAULT_EDGE_VOICE = "en-US-JennyNeural"

# Map common Kokoro / generic voice names to Microsoft Neural voices
EDGE_VOICE_MAP = {
    "af_heart": "en-US-JennyNeural",
    "af_bella": "en-US-AriaNeural",
    "af_sarah": "en-US-JennyNeural",
    "af_sky": "en-US-AriaNeural",
    "am_adam": "en-US-GuyNeural",
    "am_michael": "en-US-ChristopherNeural",
    "bf_emma": "en-GB-SoniaNeural",
    "bm_george": "en-GB-RyanNeural",
}


class EdgeTTSBackend:
    name = "edge_tts"

    def __init__(self, cfg: dict | None = None):
        self.cfg = dict(cfg or {})
        self.edge_voice = self.cfg.get("edge_tts_voice") or DEFAULT_EDGE_VOICE

    def available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def _resolve_edge_voice(self, requested_voice: str | None) -> str:
        if self.cfg.get("edge_tts_voice"):
            return str(self.cfg["edge_tts_voice"])
        if requested_voice and "Neural" in requested_voice:
            return requested_voice
        return EDGE_VOICE_MAP.get(requested_voice or "", self.edge_voice)

    def synthesize(self, scenes: list[dict], out_dir: str) -> list[str]:
        try:
            import edge_tts
        except ImportError:
            raise BackendError("edge-tts not installed: pip install edge-tts")

        Path(out_dir).mkdir(parents=True, exist_ok=True)
        todo = missing_sentences(scenes, out_dir)

        async def _run_all() -> None:
            for sent in todo:
                voice_key = sent.get("voice", DEFAULT_VOICE)
                target = Path(out_dir) / sentence_cache_name(
                    sent["ord"], sent["text"], voice_key
                )
                mp3_path = target.with_suffix(".mp3")
                speaking_voice = self._resolve_edge_voice(voice_key)
                communicate = edge_tts.Communicate(sent["text"], speaking_voice)
                await communicate.save(str(mp3_path))
                result = subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", str(mp3_path),
                        "-ar", "22050", "-ac", "1", str(target)
                    ],
                    capture_output=True,
                )
                mp3_path.unlink(missing_ok=True)
                if result.returncode != 0:
                    raise BackendError(
                        f"ffmpeg mp3->wav failed for sentence {sent['ord']}: "
                        + result.stderr.decode(errors="replace")[:300]
                    )

        asyncio.run(_run_all())
        return flatten_paths(out_dir, scenes)
