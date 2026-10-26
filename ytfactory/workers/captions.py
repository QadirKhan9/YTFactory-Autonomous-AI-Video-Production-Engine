"""Arithmetic subtitle generator (FR-Sub-01/02): captions come straight from
`sentences.start_ms / duration_ms` — zero ASR timing drift.

Rules:
  * sentence text > 84 chars (2 lines x 42 chars) is split on word boundaries
    into multiple cues, each cue <= 2 lines of <= 42 chars;
  * cue durations are allocated character-proportional to each sub-cue;
  * sum of cue durations must equal the master audio duration +/- 80 ms.
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_LINE = 42
MAX_CUE_CHARS = 84          # 2 x 42
TOTAL_TOLERANCE_MS = 80.0   # assertion: sum(cues) == master +/- 80 ms


@dataclass
class Cue:
    start_ms: float
    end_ms: float
    lines: list[str]        # 1 or 2 lines, each <= MAX_LINE chars

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def char_count(self) -> int:
        return sum(len(line) for line in self.lines)


def wrap_lines(text: str, width: int = MAX_LINE) -> list[str]:
    """Greedy word wrap; long single words are hard-split at `width`."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > width:  # pathological long token
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def split_cues(text: str) -> list[list[str]]:
    """Split sentence text into cue line-groups (<= 2 lines, <= 42 chars each).

    Short sentences stay a single cue. Longer ones wrap to <=42-char lines,
    then group lines into pairs so every cue is <= 84 visible characters.
    """
    text = " ".join(text.split())
    if len(text) <= MAX_CUE_CHARS:
        return [[line] for line in wrap_lines(text)] if text else []
    lines = wrap_lines(text)
    cues: list[list[str]] = []
    for i in range(0, len(lines), 2):
        cues.append(lines[i : i + 2])
    return cues


def build_cues(
    sentences: list[dict],
    total_tolerance_ms: float = TOTAL_TOLERANCE_MS,
) -> tuple[list[Cue], dict]:
    """Allocate sentence time across its cues, character-proportionally.

    Each sentence owns the span up to the NEXT sentence's start, so the
    silence gaps between sentences are absorbed by the preceding cue —
    captions stay on screen through natural pauses and
    sum(cues) == master duration by construction (phases.md 2.3).

    sentences: [{ord, text, start_ms, duration_ms}] in script order.
    Returns (cues, stats) where stats includes `sum_ms` and `drift_ms`.
    """
    cues: list[Cue] = []
    for idx, sent in enumerate(sentences):
        parts = split_cues(sent["text"])
        if not parts:
            continue
        start = float(sent["start_ms"])
        if idx + 1 < len(sentences):
            span = float(sentences[idx + 1]["start_ms"]) - start
        else:
            span = float(sent["duration_ms"])  # master ends with the last sentence
        total_chars = sum(sum(len(line) for line in part) for part in parts) or 1

        cursor = start
        for i, part in enumerate(parts):
            chars = sum(len(line) for line in part)
            if i == len(parts) - 1:
                end = start + span  # last cue absorbs rounding residue + gap
            else:
                share = span * (chars / total_chars)
                end = cursor + share
            cues.append(Cue(start_ms=cursor, end_ms=end, lines=part))
            cursor = end

    sum_ms = sum(c.end_ms - c.start_ms for c in cues)
    script_span = 0.0
    if sentences:
        last = sentences[-1]
        script_span = float(last["start_ms"]) + float(last["duration_ms"]) - float(
            sentences[0]["start_ms"]
        )
    drift = sum_ms - script_span
    stats = {
        "cue_count": len(cues),
        "sum_ms": round(sum_ms, 3),
        "script_span_ms": round(script_span, 3),
        "drift_ms": round(drift, 3),
        "within_tolerance": abs(drift) <= total_tolerance_ms,
        "max_line_breach": max(
            (len(line) for c in cues for line in c.lines), default=0
        )
        > MAX_LINE,
    }
    return cues, stats


def _ts(seconds: float, sep: str) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = []
    for i, cue in enumerate(cues, start=1):
        blocks.append(
            f"{i}\n{_ts(cue.start_ms / 1000, ',')} --> {_ts(cue.end_ms / 1000, ',')}\n"
            f"{cue.text}\n"
        )
    return "\n".join(blocks)


def to_vtt(cues: list[Cue]) -> str:
    blocks = ["WEBVTT", ""]
    for cue in cues:
        blocks.append(
            f"{_ts(cue.start_ms / 1000, '.')} --> {_ts(cue.end_ms / 1000, '.')}\n"
            f"{cue.text}\n"
        )
    return "\n".join(blocks)


def assert_total(
    cues: list[Cue], master_duration_ms: float, tolerance_ms: float = TOTAL_TOLERANCE_MS
) -> float:
    """Raise AssertionError when sum(cues) drifts from the master clock."""
    total = sum(c.end_ms - c.start_ms for c in cues)
    drift = total - master_duration_ms
    if abs(drift) > tolerance_ms:
        raise AssertionError(
            f"caption total {total:.1f} ms vs master {master_duration_ms:.1f} ms "
            f"({drift:+.1f} ms outside +-{tolerance_ms} ms)"
        )
    return drift
