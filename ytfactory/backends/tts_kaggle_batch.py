"""Kaggle batch TTS backend (FR-A-05 primary rung) — pull/push CLI flow.

Integrates the proven-ready Kaggle project (kernel `zain234/kokoro-tts-job`
+ dataset `zain234/kagglw`) as the primary production rung:

  1. stage `store/kaggle_job/scenes.json` (job payload) + dataset metadata;
  2. `kaggle datasets version` — publish the job to the input dataset;
  3. download scenes.json back and verify the job_id round-tripped
     (guards against the kernel reading a stale version);
  4. `kaggle kernels push` — updates code and triggers the run;
  5. poll `kaggle kernels status` until terminal, then
     `kaggle kernels output --file-pattern` to fetch manifest + WAVs;
  6. copy `sent_<ord>_<key>.wav` files into the local sentence cache.

On timeout/error this backend raises BackendError so S10 falls through to
the local backends — the DAG never stalls on TTS availability (rules.md §3.3:
batch jobs only, no inbound connections).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from ytfactory.backends.base import (
    DEFAULT_SPEED,
    DEFAULT_VOICE,
    BackendError,
    flatten_paths,
    missing_sentences,
    sentence_cache_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_KERNEL_SLUG = "zain234/kokoro-tts-job"
DEFAULT_DATASET_ID = "zain234/kagglw"
DEFAULT_KERNEL_DIR = REPO_ROOT / "kaggle" / "kernel"
DEFAULT_JOB_DIR = REPO_ROOT / "store" / "kaggle_job"

RUN_ACTIVE = ("RUNNING", "QUEUED", "PENDING", "SUBMITTED", "CREATING")
_run_lock = threading.Lock()  # one remote job at a time (NFR-M-02 spirit)


def _cli(args: list[str], timeout: float) -> str:
    import os

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            ["kaggle", *args],
            capture_output=True,
            timeout=timeout,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise BackendError("kaggle CLI not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackendError(f"kaggle {' '.join(args)} timed out") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise BackendError(f"kaggle {' '.join(args)} failed: {tail}")
    return (proc.stdout or "") + (proc.stderr or "")


def kaggle_available() -> bool:
    if not (Path.home() / ".kaggle" / "kaggle.json").exists():
        return False
    return shutil.which("kaggle") is not None


class KaggleBatchBackend:
    name = "kaggle_batch"

    def __init__(self, cfg: dict | None = None):
        self.cfg = dict(cfg or {})
        self.kernel_slug = self.cfg.get("kaggle_kernel_slug", DEFAULT_KERNEL_SLUG)
        self.dataset_id = self.cfg.get("kaggle_dataset_id", DEFAULT_DATASET_ID)
        self.kernel_dir = Path(self.cfg.get("kaggle_kernel_dir", DEFAULT_KERNEL_DIR))
        self.job_dir = Path(self.cfg.get("kaggle_job_dir", DEFAULT_JOB_DIR))
        self.timeout_s = float(self.cfg.get("tts_poll_timeout_s", 1800))
        self.interval_s = float(self.cfg.get("tts_poll_interval_s", 15))

    def available(self) -> bool:
        return kaggle_available() and (self.kernel_dir / "kernel-metadata.json").exists()

    # ------------------------------------------------------------------ jobs
    def _stage_job(self, job: dict) -> None:
        self.job_dir.mkdir(parents=True, exist_ok=True)
        (self.job_dir / "scenes.json").write_text(
            json.dumps(job, ensure_ascii=False), encoding="utf-8"
        )
        (self.job_dir / "dataset-metadata.json").write_text(
            json.dumps({"id": self.dataset_id, "title": "kagglw",
                        "licenses": [{"name": "CC0-1.0"}]}, indent=2),
            encoding="utf-8",
        )

    def _publish_job(self, job: dict, deadline: float) -> None:
        _cli(
            ["datasets", "version", "-p", str(self.job_dir),
             "-m", f"ytfactory {job['job_id']}"],
            timeout=300,
        )
        # round-trip check: the published scenes.json must be OUR job
        # (otherwise the kernel would synthesize a stale payload)
        verify_dir = self.job_dir / "_verify"
        verify_dir.mkdir(exist_ok=True)
        while time.time() < deadline:
            try:
                _cli(
                    ["datasets", "download", self.dataset_id, "-f", "scenes.json",
                     "-p", str(verify_dir), "-o", "-q"],
                    timeout=120,
                )
                got = json.loads(
                    (verify_dir / "scenes.json").read_text(encoding="utf-8")
                )
                if isinstance(got, dict) and got.get("job_id") == job["job_id"]:
                    return
            except BackendError:
                pass  # version still propagating
            time.sleep(self.interval_s)
        raise BackendError("dataset version did not propagate in time")

    def _wait_idle(self, deadline: float) -> None:
        while time.time() < deadline:
            out = _cli(["kernels", "status", self.kernel_slug], timeout=60)
            if not any(flag in out for flag in RUN_ACTIVE):
                return
            time.sleep(self.interval_s)
        raise BackendError("previous kernel run still active at timeout")

    def _run_kernel(self, deadline: float) -> None:
        # push uploads code AND triggers the run (kaggle CLI 2.x semantics)
        remaining = max(60, int(deadline - time.time()))
        _cli(
            ["kernels", "push", "-p", str(self.kernel_dir), "-t", str(remaining)],
            timeout=min(300, remaining),
        )
        while time.time() < deadline:
            out = _cli(["kernels", "status", self.kernel_slug], timeout=60)
            if not any(flag in out for flag in RUN_ACTIVE):
                return
            time.sleep(self.interval_s)
        raise BackendError("kernel run did not finish before tts_poll_timeout_s")

    def _fetch_outputs(self, job_id: str, out_dir: Path, deadline: float) -> dict:
        got_dir = self.job_dir / "_output"
        if got_dir.exists():
            shutil.rmtree(got_dir, ignore_errors=True)
        got_dir.mkdir(parents=True, exist_ok=True)
        _cli(
            ["kernels", "output", self.kernel_slug, "-p", str(got_dir),
             "--file-pattern", r"^(manifest\.json|sent_.*\.wav)$", "-o", "-q"],
            timeout=300,
        )
        manifest_path = got_dir / "manifest.json"
        if not manifest_path.exists():
            raise BackendError(f"kernel produced no manifest.json for job {job_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("error"):
            raise BackendError(f"kaggle worker error: {manifest['error'][-400:]}")
        if manifest.get("job_id") not in (job_id, "legacy"):
            raise BackendError("kernel returned a stale manifest")
        for entry in manifest.get("wavs", []):
            src = got_dir / entry["file"]
            if src.exists():
                shutil.copyfile(src, out_dir / entry["file"])
        return manifest

    def synthesize(self, scenes: list[dict], out_dir: str) -> list[str]:
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        todo = missing_sentences(scenes, out_dir)
        if not todo:
            return flatten_paths(out_dir, scenes)

        job = {
            "job_id": uuid.uuid4().hex[:12],
            "voice": self.cfg.get("tts_voice", DEFAULT_VOICE),
            "speed": float(self.cfg.get("tts_speed", DEFAULT_SPEED)),
            "sentences": [
                {"ord": s["ord"], "text": s["text"],
                 "voice": s.get("voice", DEFAULT_VOICE)}
                for s in todo
            ],
        }
        deadline = time.time() + self.timeout_s
        with _run_lock:  # serialize remote jobs
            try:
                self._stage_job(job)
                self._publish_job(job, deadline)
                self._wait_idle(deadline)
                self._run_kernel(deadline)
                self._fetch_outputs(job["job_id"], out_path, deadline)
            except BackendError:
                raise
            except Exception as exc:  # noqa: BLE001 - never stall the DAG
                raise BackendError(f"kaggle batch failed: {exc}") from exc

        still_missing = missing_sentences(scenes, out_dir)
        if still_missing:
            ords = [s["ord"] for s in still_missing[:10]]
            raise BackendError(f"kaggle batch returned without sentences {ords}")
        # prove the cache contract matches the local rungs
        for sent in todo:
            expected = sentence_cache_name(
                sent["ord"], sent["text"], sent.get("voice", DEFAULT_VOICE)
            )
            if not (out_path / expected).exists():
                raise BackendError(f"kaggle batch wrote unexpected filename {expected}")
        return flatten_paths(out_dir, scenes)
