"""YTFactory TTS batch kernel (Phase 2 FR-A-05 primary rung).

Input:  scenes.json from the attached dataset (zain234/kagglw), written per
        job by ytfactory.backends.tts_kaggle_batch.KaggleBatchBackend.
        New format:  {"job_id", "voice", "speed",
                      "sentences": [{"ord", "text", "voice"}]}
        Legacy format (manual testing): [{"id": 1, "text": "..."}]
Output: /kaggle/working/sent_<ord>_<md5(text+voice)[:10]>.wav (24 kHz)
        + manifest.json {"job_id", "wavs": [{"ord", "file", "seconds"}], "error"}
"""
import subprocess
import sys


def run(cmd):
    print(">>", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True)


VENV = "/tmp/kokoro-env"
run("apt-get -qq -y install espeak-ng > /dev/null 2>&1 || true")
run(f"{sys.executable} -m pip install -q uv")
run(f"{sys.executable} -m uv venv --seed --python 3.12 {VENV}")
run(f"{VENV}/bin/python -m pip install 'kokoro>=0.9.4' soundfile")

RUNNER = r'''
import glob, hashlib, json, time, traceback

import numpy as np
import soundfile as sf
from kokoro import KPipeline

found = glob.glob("/kaggle/input/**/scenes.json", recursive=True)
if not found:
    raise SystemExit("scenes.json not found - is the kagglw dataset attached?")
job = json.load(open(found[0]))

if isinstance(job, list):  # legacy manual format: [{"id": 1, "text": "..."}]
    job = {
        "job_id": "legacy",
        "voice": "af_heart",
        "speed": 1.0,
        "sentences": [
            {"ord": s["id"], "text": s["text"], "voice": "af_heart"} for s in job
        ],
    }

VOICE = job.get("voice") or "af_heart"
SPEED = float(job.get("speed") or 1.0)
manifest = {"job_id": job.get("job_id"), "wavs": [], "error": None}
t0 = time.time()
try:
    pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    for s in job["sentences"]:
        text = s["text"]
        voice = s.get("voice") or VOICE
        key = hashlib.md5((text + voice).encode()).hexdigest()[:10]
        fname = "sent_%d_%s.wav" % (s["ord"], key)
        chunks = [a for _, _, a in pipeline(text, voice=voice, speed=SPEED)]
        audio = (
            np.concatenate([np.asarray(c) for c in chunks])
            if chunks
            else np.zeros(0, dtype=np.float32)
        )
        sf.write("/kaggle/working/" + fname, audio, 24000)
        manifest["wavs"].append(
            {"ord": s["ord"], "file": fname, "seconds": round(len(audio) / 24000, 2)}
        )
        print("synth ord=%d %s %.2fs" % (s["ord"], fname, len(audio) / 24000), flush=True)
except Exception:
    manifest["error"] = traceback.format_exc()

json.dump(manifest, open("/kaggle/working/manifest.json", "w"), indent=2)
print(
    "job %s: %d wavs, %.1fs audio, %.1fs wall"
    % (
        manifest["job_id"],
        len(manifest["wavs"]),
        sum(w["seconds"] for w in manifest["wavs"]),
        time.time() - t0,
    ),
    flush=True,
)
if manifest["error"]:
    raise SystemExit("job failed:\n" + manifest["error"])
'''
open("/tmp/run_scenes.py", "w").write(RUNNER)
run(f"{VENV}/bin/python /tmp/run_scenes.py")
