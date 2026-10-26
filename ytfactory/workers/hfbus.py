"""File-based transport bus over a private Hugging Face dataset repo
(prd.md §11.4 / rules.md §3.3): Kaggle notebooks cannot accept inbound
connections — workers *pull* from `inbox/` and publish to `outbox/`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class BusError(RuntimeError):
    """Transport failure talking to the HF dataset repo."""


class HfBus:
    def __init__(self, repo_id: str, token: str | None = None):
        from huggingface_hub import HfApi

        if not repo_id:
            raise BusError("HF bus repo id is empty (set tts_hf_repo / YT_TTS_BUS_REPO)")
        self.repo_id = repo_id
        self.api = HfApi(token=token)

    @staticmethod
    def from_config(cfg: dict | None = None) -> "HfBus":
        import os

        cfg = cfg or {}
        repo = cfg.get("tts_hf_repo") or os.environ.get("YT_TTS_BUS_REPO") or ""
        return HfBus(repo, token=cfg.get("hf_token") or None)

    def ensure_repo(self) -> None:
        self.api.create_repo(
            repo_id=self.repo_id, repo_type="dataset", exist_ok=True, private=True
        )

    def upload_text(self, text: str, remote: str) -> None:
        import io

        self.api.upload_file(
            path_or_fileobj=io.BytesIO(text.encode("utf-8")),
            path_in_repo=remote,
            repo_id=self.repo_id,
            repo_type="dataset",
        )

    def upload_path(self, local: Path, remote: str) -> None:
        self.api.upload_file(
            path_or_fileobj=str(local),
            path_in_repo=remote,
            repo_id=self.repo_id,
            repo_type="dataset",
        )

    def download_path(self, remote: str, local: Path) -> Path:
        from huggingface_hub import hf_hub_download

        got = hf_hub_download(
            repo_id=self.repo_id, filename=remote, repo_type="dataset"
        )
        local.parent.mkdir(parents=True, exist_ok=True)
        if Path(got).resolve() != local.resolve():
            local.write_bytes(Path(got).read_bytes())
        return local

    def read_json(self, remote: str) -> Any:
        from huggingface_hub import hf_hub_download

        got = hf_hub_download(
            repo_id=self.repo_id, filename=remote, repo_type="dataset"
        )
        return json.loads(Path(got).read_text(encoding="utf-8"))

    def list_files(self, prefix: str = "") -> list[str]:
        try:
            files = self.api.list_repo_files(self.repo_id, repo_type="dataset")
        except Exception as exc:
            raise BusError(f"cannot list {self.repo_id}: {exc}") from exc
        return [f for f in files if f.startswith(prefix)]

    def wait_for(self, remote: str, timeout_s: float, interval_s: float = 15.0) -> Any:
        """Poll until `remote` appears; return its JSON payload."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if remote in self.list_files(""):
                return self.read_json(remote)
            time.sleep(interval_s)
        raise BusError(f"timed out after {timeout_s:.0f}s waiting for {remote}")
