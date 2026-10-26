"""Phase 6.1 GPU pull-worker bus (PRD 11.4 / architecture §1.3).

Laptop side: enqueue image jobs as an `inbox/<batch_id>.json` manifest, then
reap `outbox/<job_id>.result.json` + blobs with sha256 verification.

Transports (both speak the same inbox/outbox dialect):
  local — directories under store/gpu_bus (tests, simulator, single machine)
  hf    — private Hugging Face dataset repo via workers.hfbus (YT_GPU_BUS_REPO)

The real FLUX.1-schnell inference runs in the Kaggle notebook
(kaggle/gpu_worker/gpu_worker.py — pull-based, no inbound connections).
`simulate_worker` is the deterministic local stand-in used by tests and
offline verification: same claims/heartbeats/results, placeholder pixels.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LOCAL_ROOT = REPO_ROOT / "store" / "gpu_bus"

LEASE_S = 900                 # 15 min (architecture §1.3)
HEARTBEAT_S = 60              # worker heartbeat cadence
DEFAULT_MODEL = "FLUX.1-schnell"

# FR-V-08 / rules.md: blocklists are code.
BLOCKED_MODELS = frozenset(
    {
        "FLUX.1 [dev]", "FLUX.1-dev", "flux.1 dev",
        "XTTS v2", "F5-TTS", "Fish Speech NC", "MusicGen",
        "Wav2Lip", "SD 3.5 Large",
    }
)
ALLOWED_IMAGE_MODELS = frozenset({DEFAULT_MODEL})


class BusError(RuntimeError):
    pass


def assert_model_allowed(model: str) -> str:
    m = (model or "").strip()
    low = m.lower()
    for bad in BLOCKED_MODELS:
        if bad.lower() in low:
            raise BusError(f"model blocklisted (FR-V-08): {m}")
    if m not in ALLOWED_IMAGE_MODELS:
        raise BusError(f"model not in allowlist {sorted(ALLOWED_IMAGE_MODELS)}: {m}")
    return m


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# transports
# --------------------------------------------------------------------------
class LocalBus:
    """inbox/outbox directories with O_EXCL claims and mtime heartbeats."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def ensure(self) -> "LocalBus":
        (self.root / "inbox").mkdir(parents=True, exist_ok=True)
        (self.root / "outbox").mkdir(parents=True, exist_ok=True)
        return self

    # -- primitives ------------------------------------------------------
    def put_json(self, remote: str, obj: dict) -> None:
        path = self.root / remote
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def get_json(self, remote: str) -> dict:
        path = self.root / remote
        if not path.exists():
            raise BusError(f"missing {remote}")
        return json.loads(path.read_text(encoding="utf-8"))

    def put_file(self, local_path: str | Path, remote: str) -> None:
        path = self.root / remote
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        shutil.copyfile(local_path, tmp)
        os.replace(tmp, path)

    def get_file(self, remote: str, dest_dir: Path) -> Path:
        src = self.root / remote
        if not src.exists():
            raise BusError(f"missing {remote}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(remote).name
        shutil.copyfile(src, dest)
        return dest

    def list(self, prefix: str = "") -> list[str]:
        out: list[str] = []
        for folder in ("inbox", "outbox"):
            d = self.root / folder
            if not d.exists():
                continue
            for p in sorted(d.iterdir()):
                name = f"{folder}/{p.name}"
                if not prefix or name.startswith(prefix):
                    out.append(name)
        return out

    # -- lease -------------------------------------------------------------
    def _claim_path(self, batch_id: str, worker_id: str) -> Path:
        return self.root / "inbox" / f"{batch_id}.{worker_id}.claim"

    def claim(self, batch_id: str, worker_id: str) -> bool:
        """Exclusive claim; a stale (> LEASE_S) claim may be stolen."""
        self.ensure()
        mine = self._claim_path(batch_id, worker_id)
        if mine.exists():
            mine.touch()  # refresh our own lease
            return True
        for other in self.root.glob(f"inbox/{batch_id}.*.claim"):
            if time.time() - other.stat().st_mtime > LEASE_S:
                other.unlink(missing_ok=True)  # reaper stole it back
                continue
            return False
        try:
            fd = os.open(mine, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{worker_id} {_utcnow()}".encode())
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def heartbeat(self, batch_id: str, worker_id: str) -> None:
        mine = self._claim_path(batch_id, worker_id)
        if mine.exists():
            mine.touch()

    def claim_age_s(self, batch_id: str, worker_id: str) -> float | None:
        mine = self._claim_path(batch_id, worker_id)
        if not mine.exists():
            return None
        return time.time() - mine.stat().st_mtime


class HfBusTransport:
    """Same surface backed by the private HF dataset repo (claims are
    optimistic uploads — HF has no O_EXCL; ownership decided by first write)."""

    def __init__(self, repo_id: str, token: str | None = None):
        from ytfactory.workers.hfbus import HfBus

        self._bus = HfBus(repo_id, token=token)
        self.repo_id = repo_id
        self.kind = "hf"

    def ensure(self) -> "HfBusTransport":
        self._bus.ensure_repo()
        return self

    def put_json(self, remote: str, obj: dict) -> None:
        self._bus.upload_text(json.dumps(obj, indent=2), remote)

    def get_json(self, remote: str) -> dict:
        return self._bus.read_json(remote)

    def put_file(self, local_path: str | Path, remote: str) -> None:
        self._bus.upload_path(Path(local_path), remote)

    def get_file(self, remote: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(remote).name
        return Path(self._bus.download_path(remote, dest))

    def list(self, prefix: str = "") -> list[str]:
        try:
            return self._bus.list_files(prefix)
        except Exception as exc:  # noqa: BLE001
            raise BusError(str(exc)) from exc

    def claim(self, batch_id: str, worker_id: str) -> bool:
        remote = f"inbox/{batch_id}.{worker_id}.claim"
        try:
            existing = [
                n for n in self._bus.list_files(f"inbox/{batch_id}.")
                if n.endswith(".claim")
            ]
        except Exception as exc:  # noqa: BLE001
            raise BusError(str(exc)) from exc
        for name in existing:
            if name == remote:
                return True
            return False
        self._bus.upload_text(f"{worker_id} {_utcnow()}", remote)
        return True

    def heartbeat(self, batch_id: str, worker_id: str) -> None:
        self._bus.upload_text(f"{worker_id} {_utcnow()}", f"inbox/{batch_id}.{worker_id}.claim")

    def claim_age_s(self, batch_id: str, worker_id: str) -> float | None:
        return None  # HF listing has no mtime; freshness left to result wait


def bus_from_env(cfg: dict | None = None):
    cfg = cfg or {}
    repo = cfg.get("gpu_hf_repo") or os.environ.get("YT_GPU_BUS_REPO")
    if repo:
        return HfBusTransport(repo, token=cfg.get("hf_token") or None).ensure()
    root = (
        cfg.get("gpu_bus_root")
        or os.environ.get("YT_GPU_BUS_ROOT")
        or str(DEFAULT_LOCAL_ROOT)
    )
    return LocalBus(root).ensure()


def gpu_enabled(cfg: dict | None = None) -> bool:
    """'auto' activates only when a transport is explicitly configured —
    otherwise live runs would stall waiting for a worker that isn't there."""
    cfg = cfg or {}
    mode = str(cfg.get("image_gen", "auto")).lower()
    if mode in {"off", "none", "disabled"}:
        return False
    if mode == "gpu":
        return True
    return bool(
        os.environ.get("YT_GPU_BUS_REPO")
        or os.environ.get("YT_GPU_BUS_ROOT")
        or cfg.get("gpu_bus")
        or cfg.get("gpu_bus_root")
    )


# --------------------------------------------------------------------------
# laptop: enqueue + reap
# --------------------------------------------------------------------------
def enqueue(
    jobs: list[dict],
    *,
    cfg: dict | None = None,
    model: str = DEFAULT_MODEL,
    bus=None,
) -> tuple[object, str, dict]:
    """-> (bus, batch_id, manifest). Each job: {job_id, prompt, seed, ...}."""
    model = assert_model_allowed(model)
    if not jobs:
        raise BusError("no GPU jobs to enqueue")
    bus = bus or bus_from_env(cfg)
    batch_id = uuid.uuid4().hex[:12]
    manifest_jobs = []
    for job in jobs:
        prompt = str(job.get("prompt") or "").strip()
        if not prompt:
            raise BusError(f"job {job.get('job_id')} has no prompt")
        manifest_jobs.append(
            {
                "job_id": job["job_id"],
                "prompt": prompt,
                "seed": int(job.get("seed") or abs(int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16))),
                "width": int(job.get("width", 1280)),
                "height": int(job.get("height", 720)),
                "steps": int(job.get("steps", 4)),
            }
        )
    manifest = {
        "batch_id": batch_id,
        "model": model,
        "created_at": _utcnow(),
        "jobs": manifest_jobs,
    }
    bus.put_json(f"inbox/{batch_id}.json", manifest)
    return bus, batch_id, manifest


def reap(
    bus,
    manifest: dict,
    *,
    dest_dir: Path,
    timeout_s: float,
    interval_s: float = 1.0,
) -> dict:
    """Poll outbox until every job has a verified result.
    -> {job_id: {"result": dict, "path": Path}}. Raises BusError on timeout."""
    jobs = manifest["jobs"]
    found: dict[str, dict] = {}
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for job in jobs:
            jid = job["job_id"]
            if jid in found:
                continue
            try:
                result = bus.get_json(f"outbox/{jid}.result.json")
            except Exception:  # noqa: BLE001 - not there yet
                continue
            if result.get("error"):
                raise BusError(f"gpu job {jid} failed: {result['error']}")
            try:
                remote_file = result.get("file") or f"outbox/{jid}.png"
                path = bus.get_file(remote_file, dest_dir)
                sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            except Exception:  # noqa: BLE001 - transient network, retry till deadline
                continue
            if sha != result.get("sha256"):
                raise BusError(f"sha mismatch for {jid}: bus {result.get('sha256')} != {sha}")
            found[jid] = {"result": result, "path": Path(path)}
        if len(found) == len(jobs):
            return found
        time.sleep(interval_s)
    missing = [j["job_id"] for j in jobs if j["job_id"] not in found]
    raise BusError(f"gpu bus timeout after {timeout_s:.0f}s: {len(found)}/{len(jobs)} done, missing {missing}")


def run_batch(
    jobs: list[dict],
    *,
    cfg: dict | None = None,
    dest_dir: Path,
    timeout_s: float | None = None,
    note=None,
) -> dict | None:
    """End-to-end laptop flow. -> results, or None when GPU is disabled or
    the bus failed (caller falls back to library/typographic assets)."""
    cfg = cfg or {}
    if not gpu_enabled(cfg):
        return None
    say = note or (lambda m: None)
    try:
        bus, batch_id, manifest = enqueue(jobs, cfg=cfg,
                                          model=str(cfg.get("gpu_model", DEFAULT_MODEL)))
        say(f"S13 gpu batch {batch_id}: {len(jobs)} job(s) enqueued")
        timeout = timeout_s if timeout_s is not None else float(cfg.get("gpu_timeout_s", 60))
        results = reap(bus, manifest, dest_dir=dest_dir, timeout_s=timeout)
        say(f"S13 gpu batch {batch_id}: {len(results)} result(s) verified")
        return results
    except BusError as exc:
        say(f"S13 gpu unavailable, falling back: {exc}")
        return None


# --------------------------------------------------------------------------
# worker side (simulator + shared helpers)
# --------------------------------------------------------------------------
def placeholder_image(prompt: str, seed: int, size: tuple[int, int] = (1280, 720)) -> Image.Image:
    """Deterministic local stand-in pixels (same protocol as real FLUX)."""
    digest = hashlib.sha256(f"{prompt}|{seed}".encode()).digest()
    c1 = tuple(digest[i] for i in range(3))
    c2 = tuple(255 - digest[i] for i in range(3, 6))
    w, h = size
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        draw.line(
            [(0, y), (w, y)],
            fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)),
        )
    rng_x = digest[6:10]
    cx = int.from_bytes(rng_x[:2], "big") % w
    cy = int.from_bytes(rng_x[2:4], "big") % h
    r = min(w, h) // 5
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tuple(digest[10:13]))
    return img


def simulate_worker(
    bus,
    *,
    worker_id: str = "sim-local",
    stop_when_idle: bool = True,
    idle_polls: int = 3,
    interval_s: float = 0.5,
    heartbeat_every_s: float = 30.0,
    max_batches: int = 64,
) -> int:
    """Local stand-in for the Kaggle notebook: claim -> render -> outbox.
    -> number of batches processed."""
    import tempfile

    processed = 0
    idle = 0
    with tempfile.TemporaryDirectory(prefix="gpusim-") as tmp:
        tmp_dir = Path(tmp)
        while processed < max_batches:
            manifests = [n for n in bus.list("inbox/") if n.endswith(".json")]
            did = False
            for name in manifests:
                batch_id = Path(name).stem
                try:
                    manifest = bus.get_json(name)
                except BusError:
                    continue
                # already-rendered batches are skipped (durable outbox marker)
                out_names = set(bus.list("outbox/"))
                if all(
                    f"outbox/{job['job_id']}.result.json" in out_names
                    for job in manifest.get("jobs", [])
                ):
                    continue
                if not bus.claim(batch_id, worker_id):
                    continue
                model = manifest.get("model", DEFAULT_MODEL)
                try:
                    assert_model_allowed(model)
                except BusError as exc:
                    for job in manifest.get("jobs", []):
                        bus.put_json(
                            f"outbox/{job['job_id']}.result.json",
                            {"job_id": job["job_id"], "error": str(exc)},
                        )
                    continue
                last_beat = time.time()
                for job in manifest.get("jobs", []):
                    if time.time() - last_beat > HEARTBEAT_S:
                        bus.heartbeat(batch_id, worker_id)
                        last_beat = time.time()
                    img = placeholder_image(job["prompt"], int(job.get("seed", 0)),
                                            (int(job.get("width", 1280)), int(job.get("height", 720))))
                    blob = tmp_dir / f"{job['job_id']}.png"
                    img.save(blob, "PNG")
                    sha = hashlib.sha256(blob.read_bytes()).hexdigest()
                    remote = f"outbox/{job['job_id']}.png"
                    bus.put_file(blob, remote)
                    bus.put_json(
                        f"outbox/{job['job_id']}.result.json",
                        {
                            "job_id": job["job_id"],
                            "file": remote,
                            "sha256": sha,
                            "width": img.width,
                            "height": img.height,
                            "model": model,
                            "simulated": True,
                            "license_id": "internal_synthetic",
                            "worker": worker_id,
                        },
                    )
                processed += 1
                did = True
            if did:
                idle = 0
            else:
                idle += 1
                if stop_when_idle and idle >= idle_polls:
                    break
                time.sleep(interval_s)
    return processed


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="ytfactory GPU bus local simulator")
    p.add_argument("--root", default=str(DEFAULT_LOCAL_ROOT))
    p.add_argument("--worker-id", default=f"sim-{uuid.uuid4().hex[:6]}")
    p.add_argument("--watch", action="store_true",
                   help="poll forever (spawn before a live rerun; Ctrl+C to stop)")
    p.add_argument("--idle-polls", type=int, default=3)
    p.add_argument("--interval", type=float, default=0.5)
    args = p.parse_args(argv)
    bus = LocalBus(args.root).ensure()
    if args.watch:
        try:
            while True:
                n = simulate_worker(bus, worker_id=args.worker_id, stop_when_idle=False,
                                    interval_s=args.interval)
                if n:
                    print(f"[gpubus] processed {n} batch(es)")
        except KeyboardInterrupt:
            return 0
    n = simulate_worker(bus, worker_id=args.worker_id, idle_polls=args.idle_polls,
                        interval_s=args.interval)
    print(f"[gpubus] processed {n} batch(es)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
