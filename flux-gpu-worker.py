"""Kaggle GPU pull-worker — FLUX.1-schnell batch image generation.

Runs standalone inside a Kaggle notebook (GPU sessions cannot accept inbound
connections, architecture §1.3): polls the HF dataset bus inbox, claims a
batch, renders, publishes outbox blobs + sha256 results, heartbeats every 60 s.

Protocol (PRD §11.4):
  inbox/<batch_id>.json          — job manifest written by the laptop
  inbox/<batch_id>.<worker>.claim— lease (15 min; heartbeat refreshes)
  outbox/<job_id>.png            — rendered blob
  outbox/<job_id>.result.json    — {sha256, width, height, model, file}

Blocklists are code (rules.md): only FLUX.1-schnell (Apache 2.0) may run.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

try:  # script kernels buffer stdout in 8 KB blocks — force line buffering
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001 - reconfigure unavailable on some pythons
    pass

# --- FR-V-08 blocklist (duplicated on purpose: the notebook is standalone) --
BLOCKED_MODELS = (
    "FLUX.1 [dev]", "FLUX.1-dev", "XTTS v2", "F5-TTS",
    "Fish Speech NC", "MusicGen", "Wav2Lip", "SD 3.5 Large",
)
ALLOWED_MODELS = ("FLUX.1-schnell",)

# --- config: bus repo + HF token come from a mounted config dataset first
# (no secrets UI needed), then Kaggle Secrets as fallback -----------------
def _load_env() -> None:
    if os.environ.get("YT_GPU_BUS_REPO") and os.environ.get("HF_TOKEN"):
        return
    import glob

    for cfg in sorted(glob.glob("/kaggle/input/**/bus_config.json", recursive=True)):
        try:
            with open(cfg, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:  # noqa: BLE001 - skip malformed mounts
            continue
        repo = str(data.get("YT_GPU_BUS_REPO", "")).strip()
        token = str(data.get("HF_TOKEN", "")).strip()
        if repo and not os.environ.get("YT_GPU_BUS_REPO"):
            os.environ["YT_GPU_BUS_REPO"] = repo
            print(f"[gpu-worker] bus repo from config dataset: {cfg}")
        if token and not os.environ.get("HF_TOKEN"):
            os.environ["HF_TOKEN"] = token
    if os.environ.get("YT_GPU_BUS_REPO"):
        return
    try:
        from kaggle_secrets import UserSecretsClient

        secrets = UserSecretsClient()
        os.environ["YT_GPU_BUS_REPO"] = secrets.get_secret_value("YT_GPU_BUS_REPO")
        token = secrets.get_secret_value("HF_TOKEN")
        if token and not os.environ.get("HF_TOKEN"):
            os.environ["HF_TOKEN"] = token
    except Exception as exc:  # noqa: BLE001 - main() reports the missing repo
        print(f"[gpu-worker] secrets unavailable: {type(exc).__name__}: {exc}")


_load_env()
HF_REPO = os.environ.get("YT_GPU_BUS_REPO", "").strip()
WORKER_ID = f"kaggle-{uuid.uuid4().hex[:8]}"
POLL_INTERVAL_S = 20
HEARTBEAT_S = 60
IDLE_EXIT_POLLS = 12          # ~4 min with no work -> give the session back
MAX_SESSION_S = 55 * 60       # stay inside a Kaggle GPU session


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _api():
    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    return HfApi(token=token)


def _upload(api, blob: bytes, remote: str) -> None:
    import io

    api.upload_file(
        path_or_fileobj=io.BytesIO(blob),
        path_in_repo=remote,
        repo_id=HF_REPO,
        repo_type="dataset",
    )


def _download(remote: str) -> bytes:
    from huggingface_hub import hf_hub_download

    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    got = hf_hub_download(
        repo_id=HF_REPO, filename=remote, repo_type="dataset", token=token
    )
    with open(got, "rb") as fh:
        return fh.read()


def _list(api, prefix: str) -> list[str]:
    try:
        files = api.list_repo_files(HF_REPO, repo_type="dataset")
    except Exception:
        return []
    return [f for f in files if f.startswith(prefix)]


def _claim(api, batch_id: str) -> bool:
    remote = f"inbox/{batch_id}.{WORKER_ID}.claim"
    for name in _list(api, f"inbox/{batch_id}."):
        if name.endswith(".claim"):
            if name == remote:
                return True
            return False  # another live worker owns this batch
    _upload(api, f"{WORKER_ID} {_now()}".encode(), remote)
    return True


def _heartbeat_loop(api, batch_id: str, stop: dict) -> None:
    import threading

    remote = f"inbox/{batch_id}.{WORKER_ID}.claim"

    def beat() -> None:
        while not stop.get("done"):
            try:
                _upload(api, f"{WORKER_ID} {_now()}".encode(), remote)
            except Exception:
                pass
            time.sleep(HEARTBEAT_S)

    threading.Thread(target=beat, daemon=True).start()


def _assert_allowed(model: str) -> None:
    if model not in ALLOWED_MODELS or any(b in model for b in BLOCKED_MODELS):
        raise RuntimeError(f"model not allowed: {model}")


OFFICIAL_REPO = "black-forest-labs/FLUX.1-schnell"
GGUF_REPO = "city96/FLUX.1-schnell-gguf"
GGUF_DEFAULT = "flux1-schnell-Q4_K_M.gguf"


def _ensure_deps() -> None:
    """Worker image may lack gguf/accelerate or ship an old diffusers."""
    import importlib.util
    import subprocess
    import sys

    missing = [p for p in ("gguf", "accelerate") if importlib.util.find_spec(p) is None]
    want_upgrade = False
    if importlib.util.find_spec("diffusers") is not None:
        try:
            import diffusers

            major, minor = (int(x) for x in diffusers.__version__.split(".")[:2])
            want_upgrade = (major, minor) < (0, 33)
        except Exception:  # noqa: BLE001 - unparsable version -> try upgrade
            want_upgrade = True
    if not missing and not want_upgrade:
        return
    cmd = [sys.executable, "-m", "pip", "install", "-q"]
    if missing:
        cmd += missing
    if want_upgrade:
        cmd += ["-U", "diffusers"]
    print(f"[gpu-worker] installing deps: {' '.join(cmd[4:])}")
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=900)
    except Exception as exc:  # noqa: BLE001 - GGUF path degrades to full load
        print(f"[gpu-worker] pip install failed: {type(exc).__name__}: {exc}")


def _pipeline_gguf(dtype):
    """Quantized transformer (~6.5 GB Q4) + fp16 encoders — fits 29 GB RAM / 16 GB VRAM."""
    from diffusers import FluxPipeline, FluxTransformer2DModel, GGUFQuantizationConfig

    gguf_file = os.environ.get("YT_FLUX_GGUF", GGUF_DEFAULT).strip() or GGUF_DEFAULT
    print(f"[gpu-worker] loading GGUF transformer: {GGUF_REPO}::{gguf_file}")
    transformer = FluxTransformer2DModel.from_pretrained(
        GGUF_REPO,
        subfolder="transformer",
        quantization_config=GGUFQuantizationConfig(gguf_file=gguf_file),
        torch_dtype=dtype,
    )
    return FluxPipeline.from_pretrained(OFFICIAL_REPO, transformer=transformer, torch_dtype=dtype)


def _pipeline(model: str):
    """Load once per session; prefer GGUF-quantized weights (fits a 30 GB box),
    fall back to a full fp16/bf16 load on roomier machines."""
    _assert_allowed(model)
    _ensure_deps()
    import torch
    from diffusers import FluxPipeline

    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    pipe = None
    try:
        pipe = _pipeline_gguf(dtype)
    except Exception as exc:  # noqa: BLE001 - fall back to full precision load
        print(f"[gpu-worker] GGUF path failed ({type(exc).__name__}: {exc}); full load")
    if pipe is None:
        pipe = FluxPipeline.from_pretrained(OFFICIAL_REPO, torch_dtype=dtype)
    try:
        pipe.enable_model_cpu_offload()
    except Exception:  # noqa: BLE001 - only if every component fits on the GPU
        pipe = pipe.to("cuda")
    return pipe


def _render(pipe, job: dict, workdir: str) -> bytes:
    import io

    import torch

    generator = torch.Generator("cuda").manual_seed(int(job.get("seed", 0)))
    image = pipe(
        prompt=job["prompt"],
        width=int(job.get("width", 1280)),
        height=int(job.get("height", 720)),
        num_inference_steps=int(job.get("steps", 4)),
        guidance_scale=0.0,
        generator=generator,
    ).images[0]
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def process_batch(api, batch_id: str, pipe_holder: dict) -> bool:
    """-> True when this batch had new work to render (completed batches are
    skipped so a 55-minute session never re-renders the same manifest)."""
    if not _claim(api, batch_id):
        print(f"[gpu-worker] batch {batch_id} claimed elsewhere, skipping")
        return False
    manifest = json.loads(_download(f"inbox/{batch_id}.json").decode("utf-8"))
    out_files = set(_list(api, "outbox/"))
    if all(
        f"outbox/{job['job_id']}.result.json" in out_files
        for job in manifest.get("jobs", [])
    ):
        return False
    model = manifest.get("model", "FLUX.1-schnell")
    stop = {"done": False}
    _heartbeat_loop(api, batch_id, stop)
    try:
        for job in manifest.get("jobs", []):
            jid = job["job_id"]
            try:
                if pipe_holder.get("pipe") is None:
                    pipe_holder["pipe"] = _pipeline(model)
                blob = _render(pipe_holder["pipe"], job, os.getcwd())
                sha = hashlib.sha256(blob).hexdigest()
                _upload(api, blob, f"outbox/{jid}.png")
                _upload(
                    api,
                    json.dumps(
                        {
                            "job_id": jid,
                            "file": f"outbox/{jid}.png",
                            "sha256": sha,
                            "width": int(job.get("width", 1280)),
                            "height": int(job.get("height", 720)),
                            "model": model,
                            "simulated": False,
                            "license_id": "apache-2.0",
                            "worker": WORKER_ID,
                        }
                    ).encode(),
                    f"outbox/{jid}.result.json",
                )
                print(f"[gpu-worker] {jid} rendered ({len(blob)} bytes)")
            except Exception as exc:  # noqa: BLE001 - report per-job, keep batch alive
                _upload(
                    api,
                    json.dumps({"job_id": jid, "error": f"{type(exc).__name__}: {exc}"}).encode(),
                    f"outbox/{jid}.result.json",
                )
    finally:
        stop["done"] = True
    return True


def main() -> int:
    if not HF_REPO:
        print("[gpu-worker] YT_GPU_BUS_REPO not set — nothing to do")
        return 1
    api = _api()
    started = time.time()
    idle = 0
    pipe_holder = {"pipe": None}
    print(f"[gpu-worker] {WORKER_ID} watching {HF_REPO}")
    while time.time() - started < MAX_SESSION_S:
        batches = [n for n in _list(api, "inbox/") if n.endswith(".json")]
        did = False
        for name in batches:
            batch_done = process_batch(
                api, os.path.splitext(os.path.basename(name))[0], pipe_holder
            )
            did = batch_done or did
        if did:
            idle = 0
        else:
            idle += 1
            if idle % 15 == 0:
                print(f"[gpu-worker] no new work yet (idle poll {idle})")
            if idle >= IDLE_EXIT_POLLS:
                print("[gpu-worker] inbox idle — ending session")
                break
        time.sleep(POLL_INTERVAL_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
