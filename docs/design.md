# YTFactory — Detailed Software Design Specification (`design.md`)

**Version:** 1.0  
**Status:** Active Technical Design  
**Target Hardware:** Intel Core i5-6300U / 8 GB RAM / Intel HD 520 / Windows 11 ($0.00 Cost, No Credit Card)

---

## 1. System Component Overview

YTFactory is architected around a stateless blackboard pattern orchestrated by a deterministic Python state machine operating over SQLite WAL (`ytfactory.db`).

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                           │
│  • State Machine Orchestrator (`orchestrator/state_machine.py`)         │
│  • Idempotency Engine (`orchestrator/idem.py`)                          │
│  • SQLite WAL Blackboard Database (`store/ytfactory.db`)                │
│  • LiteLLM Router Proxy (`orchestrator/router.py`)                      │
│  • FastAPI + htmx Local Control Dashboard (`ui/`)                       │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ Leases jobs by capability tag
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
│ CPU WORKER       │        │ NET WORKER       │        │ GPU BURST WORKER │
│ (Local Laptop)   │        │ (Local Laptop)   │        │ (Kaggle Batch)   │
│ • FFmpeg render  │        │ • Academic APIs  │        │ • FLUX.1-schnell │
│ • Kokoro TTS     │        │ • Stock search   │        │ • Pull-based     │
│ • DeBERTa NLI    │        │ • LiteLLM calls  │        │ • HF Bus inbox   │
│ • Playwright     │        │ • Web Harvest    │        │ • Batch submission│
└────────┬─────────┘        └────────┬─────────┘        └────────┬─────────┘
         │                           │                           │
         └───────────────────────────┼───────────────────────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ ARTIFACT PLANE                                                          │
│  • Content-Addressed Storage (CAS): `store/blobs/<sha256[:2]>/<sha256>` │
│  • Hugging Face Private Dataset Transport Bus (Git-LFS)                 │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Data Models (Pydantic Schemas)

Pydantic v2 schemas act as strict policy firewalls between untrusted LLM outputs and the system blackboard.

### 2.1 Script & Fact Grounding Schema
```python
from pydantic import BaseModel, Field, model_validator
from typing import List, Optional

class SentenceSchema(BaseModel):
    ord: int = Field(..., description="1-based sequence index")
    text: str = Field(..., min_length=2, description="Sentence text")
    is_rhetorical: bool = Field(False, description="True if sentence contains no factual assertions")
    claim_ids: List[str] = Field(default_factory=list, description="IDs of claims supporting this sentence")
    emphasis: Optional[str] = Field(None, description="Prosody hint: 'strong', 'soft', 'fast', 'slow'")

    # Validator: Non-rhetorical sentences MUST carry claim_ids
    @model_validator(mode="after")
    def validate_grounding(self) -> "SentenceSchema":
        if not self.is_rhetorical and not self.claim_ids:
            raise ValueError(f"Sentence {self.ord} is marked factual but carries no claim_ids")
        return self

class ScriptDraftSchema(BaseModel):
    sentences: List[SentenceSchema]
```

### 2.2 Scene Director Schema
```python
class SceneSchema(BaseModel):
    ord: int = Field(..., description="1-based scene sequence index")
    sentence_ids: List[int] = Field(..., min_items=1, description="Mapped sentence ordinals")
    visual_intent: str = Field(..., description="Visual concept description")
    stock_queries: List[str] = Field(..., max_items=5, description="Search queries for stock media")
    gen_prompt: str = Field(..., description="FLUX.1 prompt for image synthesis fallback")
    motion: str = Field("kenburns_zoom_in", description="Camera animation type")

class ScenePlanSchema(BaseModel):
    scenes: List[SceneSchema]
```

### 2.3 Edit Decision List (EDL) Schema
```python
class EDLSceneAsset(BaseModel):
    sha256: str
    kind: str  # "image" | "video" | "typographic_card"
    license_id: str
    gate_status: str  # Must be "approved"

class EDLScene(BaseModel):
    ord: int
    start_ms: int
    end_ms: int
    duration_ms: int
    frames: int
    asset: EDLSceneAsset
    motion: dict
    overlay: Optional[dict] = None

class EDLSchema(BaseModel):
    version: int = 1
    output_width: int = 1920
    output_height: int = 1080
    fps: int = 30
    pix_fmt: str = "yuv420p"
    master_audio_sha256: str
    master_audio_lufs: float = -14.0
    master_audio_duration_ms: int
    scenes: List[EDLScene]
```

---

## 3. Database Schema & Blackboard Structure

SQLite database (`ytfactory.db`) runs in **WAL mode** (`PRAGMA journal_mode=WAL;`).

### 3.1 Primary Table Definitions
- **`projects`**: Core entity (`id`, `slug`, `topic`, `status`, `config_json`, `created_at`).
- **`steps`**: Tracks DAG step execution. Primary key: `(project_id, step_name)`. Unique constraint: `(project_id, idem_key)`.
- **`jobs`**: Transactional worker lease queue (`id`, `project_id`, `step_name`, `capability`, `status`, `payload_json`, `worker_id`, `lease_expires_at`).
- **`artifacts`**: Content-Addressed Storage metadata (`sha256`, `mime`, `size_bytes`, `storage_path`, `created_at`).
- **`sources`**: Ingested research documents (`id`, `url`, `url_sha`, `domain`, `quality_tier`, `clean_text`, `is_retracted`).
- **`evidence`**: Text chunks extracted from sources (`id`, `source_id`, `chunk_text`, `char_span`, `embedding_blob`).
- **`claims`**: Fact assertions (`id`, `claim_text`, `verdict`, `verification_note`).
- **`sentences`**: Script sentences (`project_id`, `ord`, `text`, `is_rhetorical`, `claim_ids_json`, `duration_ms`, `audio_sha256`).
- **`scenes`**: Visual scene definitions (`project_id`, `ord`, `sentence_ids_json`, `visual_intent`, `gen_prompt`, `start_ms`, `end_ms`, `frames`).
- **`media_assets`**: Asset licensing ledger (`sha256`, `source_url`, `license_id`, `commercial_ok`, `terms_html_sha`, `gate_status`).
- **`usage_ledger`**: Token and GPU tracking (`id`, `project_id`, `step_name`, `provider`, `model`, `tokens_in`, `tokens_out`, `estimated_cost`).
- **`events`**: Structured append-only audit log (`id`, `project_id`, `event_type`, `level`, `message`, `metadata_json`, `timestamp`).

---

## 4. State Machine & Resumable Execution Engine

The orchestrator is a deterministic Python loop managing project state transitions:

```text
S00 (INTAKE)
 └──► S01 (RESEARCH_PLAN) ──► S02 (SOURCE_HARVEST)
       └──► S03 (SOURCE_SCORE) [▸ HALT IF <5 TIER-A SOURCES]
             └──► S04 (EVIDENCE_INDEX) ──► S05 (OUTLINE) ──► S06 (DRAFT)
                   └──► S07 (CRITIQUE)
                         └──► S08 (FACT_CHECK) [▸ HALT IF UNSUPPORTED CLAIMS]
                                └──► S09 (SCENE_PLAN)
                                      └──► S10 (TTS MASTER CLOCK)
                                            └──► S17 (CAPTIONS) ──► S11 (VISUAL_SOURCE) ──► S12 (LICENSE_GATE) [▸ HALT IF UNAPPROVED ASSETS]
                                                  ├──► S13 (IMAGE_GEN GPU)
                                                  └──► S14 (AUDIO_BED)
                                                        └──► S15 (EDL_COMPILE)
                                                              └──► S16 (RENDER)
                                                                    └──► S18 (PACKAGING)
                                                                         └──► S19 (AUTO_QC) [▸ HALT IF QC FAILS]
                                                                               └──► S20 (AI_QC)
                                                                                     └──► S21 (HUMAN_GATE) [▸ MANDATORY REVIEW]
                                                                                           └──► S22 (DELIVER)
```

### 4.1 Idempotency Key Engine (`idem.py`)
```python
import hashlib, json

def compute_idem_key(step_name: str, inputs: dict, prompt_version: str, model_tier: str, code_version: str) -> str:
    # model_tier = configured tier name ("cheap" | "writer" | "verifier" | "deterministic");
    # the resolved provider/model goes to usage_ledger, never into the key.
    canonical_inputs = json.dumps(inputs, sort_keys=True, separators=(',', ':'))
    raw_payload = f"{step_name}|{canonical_inputs}|{prompt_version}|{model_tier}|{code_version}"
    return hashlib.sha256(raw_payload.encode('utf-8')).hexdigest()
```

---

## 5. Swappable TTS Backend Architecture

```text
               ┌──────────────────────────────────────────────┐
               │         TTS Orchestrator Interface           │
               │  synthesize(scenes, out_dir) -> list[str]    │
               └──────────────────────┬───────────────────────┘
                                      │
                   ┌──────────────────┴──────────────────┐
                   ▼                                     ▼
     ┌───────────────────────────┐         ┌───────────────────────────┐
     │ Kaggle Batch Backend      │         │ Local Fallback Chain      │
     │ (`tts_kaggle_batch.py`)   │         │ 1. `tts_local_kokoro.py`  │
     │ • Hash cache check        │         │    (kokoro-onnx CPU)      │
     │ • Filter missing scenes   │         │ 2. `tts_local.py` (Piper) │
     │ • Single job submit       │         │ • Hash cache check first  │
     │ • Poll status & download  │         │ • Local disk write        │
     └───────────────────────────┘         └───────────────────────────┘
```

### 5.1 Local MD5 Hash Caching
Before synthesizing, the backend computes the expected filename for each scene:
$$\text{filename} = \text{scene\_} \langle \text{id} \rangle \text{\_} \langle \text{md5(text + voice)[:10]} \rangle \text{.wav}$$
If all files exist in `out_dir`, the function immediately returns local paths without making any network calls.

---

## 6. EDL Compiler & FFmpeg Rendering Pipeline

### 6.1 Audio Master Clock Rule
Audio synthesis occurs at Step S10 before timeline compilation.
$$\text{duration\_ms} = \frac{\text{exact\_samples}}{\text{sample\_rate}} \times 1000$$
$$\text{start\_frame}(n) = \text{round}\left(\frac{\text{start\_ms}(n) \times \text{fps}}{1000}\right), \quad \text{frames}(n) = \text{start\_frame}(n+1) - \text{start\_frame}(n)$$

### 6.2 Rendering Sequence (Step S16)
1. **Per-Scene Segment Rendering:** Each scene is rendered to a temporary MP4 (`yuv420p`, 1920×1080, 30fps CFR) using `-threads 2`. Ken Burns panning effects use `crop` linear expressions instead of `zoompan`.
2. **Segment Caching:** Rendered MP4 segments are cached by:
   $$\text{segment\_hash} = \text{sha256}(\text{asset\_sha} \mid \text{motion\_params} \mid \text{duration\_ms} \mid \text{render\_params})$$
3. **Stream-Copy Concatenation:** Segments are concatenated using FFmpeg concat demuxer (`-c copy`), avoiding re-encoding.
4. **Single Audio Mux & Normalization:** Video stream is combined with `master_audio.wav` normalized via `pyloudnorm` to **−14 LUFS / −1.5 dBTP**.

---

## 7. Fact Verification & NLI Pipeline Architecture

```text
[ Script Sentence ] ──► [ Extract Claim Text ]
                              │
                              ▼
                 ┌─────────────────────────┐
                 │ Local DeBERTa NLI Gate  │
                 │ (deberta-v3-small ONNX) │
                 └────────────┬────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼ (>0.9)             ▼ (0.1–0.9)          ▼ (<0.1)
  [ Auto-Approved ]   [ LLM Fact Checker (A6) ]   [ Auto-Rejected ]
                      (Decorrelated Model)               │
                              │                          ▼
                      ┌───────┴───────┐           [ Trigger Rewrite ]
                      ▼               ▼
                [ Supported ]   [ Unsupported ]
                                      │
                                      ▼
                             [ HALT: BLOCKED_FACTS ]
```

---

## 8. Local Web Interface Architecture (FastAPI + htmx)

The user dashboard runs at `http://localhost:8000` using FastAPI, Jinja2 templates, and htmx 2.x for hypermedia updates without Node.js dependencies.

### 8.1 Key UI Routes & Handlers
- `GET /`: Dashboard overview (active projects, queue status, resource gauges).
- `POST /projects`: Submit new topic and initialize project DAG.
- `GET /projects/{id}`: Interactive project DAG status and log tail.
- `GET /projects/{id}/review`: **Human Review Gate (`AWAITING_HUMAN`)** page.
  - HTML5 Video player streaming `final.mp4`.
  - Storyboard scene grid with asset badges and swap options.
  - 20-point auto-QC checklist status.
- `POST /projects/{id}/approve`: Operator approves video, transitioning status to `DELIVERED`.
- `POST /projects/{id}/request_changes`: Operator requests step rollback and asset swap.
