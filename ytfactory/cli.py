"""YTFactory CLI.

    python -m ytfactory.cli run "How does the human brain create memories?" --dry-run
    python -m ytfactory.cli resume <slug>
    python -m ytfactory.cli rerun <slug> --from S03
    python -m ytfactory.cli approve <slug>
    python -m ytfactory.cli list [--project <slug>]
    python -m ytfactory.cli llm-check [--timeout 30]

Runs are dry-run by default (zero API calls, no network); pass --live for
real execution once the relevant phases ship. `llm-check` does make live
API calls (one tiny completion per registry model).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from ytfactory.db import DEFAULT_DB, Blackboard, canonical_json
from ytfactory.orchestrator.state_machine import (
    ORDER,
    approve,
    resolve_step,
    run_project,
)
from ytfactory.steps import STEP_NAMES

OK_STATUSES = ("DELIVERED", "APPROVED", "PAUSED")


def _open(args: argparse.Namespace) -> Blackboard:
    bb = Blackboard(args.db)
    bb.init_schema()
    return bb


def _coerce(value: str):
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _parse_overrides(pairs: list[str] | None) -> dict:
    """--set KEY=VALUE (repeatable) -> config overrides."""
    out: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects KEY=VALUE, got {pair!r}")
        key, value = pair.split("=", 1)
        if not key.strip():
            raise SystemExit(f"--set expects KEY=VALUE, got {pair!r}")
        out[key.strip()] = _coerce(value.strip())
    return out


def _apply_overrides(bb: Blackboard, project, pairs: list[str] | None) -> dict:
    """Persist --set overrides onto the project config (resume/rerun)."""
    overrides = _parse_overrides(pairs)
    if not overrides:
        return json.loads(project["config_json"])
    config = json.loads(project["config_json"])
    config.update(overrides)
    cfg_json = canonical_json(config)
    cfg_hash = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    bb.conn.execute(
        "UPDATE projects SET config_json = ?, config_hash = ?, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
        (cfg_json, cfg_hash, project["id"]),
    )
    return config


def cmd_run(args: argparse.Namespace) -> int:
    bb = _open(args)
    config = {
        "audience": args.audience,
        "duration_min": args.duration,
        "target_wpm": args.wpm,
    }
    config.update(_parse_overrides(args.set))
    if args.tts_backend:
        config["tts_backend"] = args.tts_backend  # pins S10 rung (created-at-once)
    pid = bb.create_project(args.topic, config)
    project = bb.get_project(pid)
    print(f"project {project['slug']} ({project['status']}) — {args.topic}")
    print(f"mode: {'LIVE' if args.live else 'DRY-RUN (zero API calls)'}"
          f"{'  until ' + args.until if args.until else ''}\n")
    try:
        status = run_project(bb, pid, dry_run=not args.live, until=args.until)
    finally:
        bb.close()
    print(f"\n=> {project['slug']}: {status}")
    return 0 if status in OK_STATUSES else 2


def cmd_resume(args: argparse.Namespace) -> int:
    bb = _open(args)
    project = bb.resolve_project(args.project)
    if not project:
        print(f"no project matching {args.project!r}", file=sys.stderr)
        return 1
    _apply_overrides(bb, project, args.set)
    print(f"resuming {project['slug']} (was {project['status']})")
    try:
        status = run_project(bb, project["id"], dry_run=not args.live, until=args.until)
    finally:
        bb.close()
    print(f"\n=> {project['slug']}: {status}")
    return 0 if status in OK_STATUSES else 2


def cmd_rerun(args: argparse.Namespace) -> int:
    bb = _open(args)
    project = bb.resolve_project(args.project)
    if not project:
        print(f"no project matching {args.project!r}", file=sys.stderr)
        return 1
    step = resolve_step(args.step)
    _apply_overrides(bb, project, args.set)
    removed = bb.invalidate_from(project["id"], step, STEP_NAMES)
    bb.set_project_status(project["id"], "RUNNING")  # DELIVERED must not short-circuit
    print(f"invalidated {removed} step(s) from {step}")
    try:
        status = run_project(bb, project["id"], dry_run=not args.live, until=args.until)
    finally:
        bb.close()
    print(f"\n=> {project['slug']}: {status}")
    return 0 if status in OK_STATUSES else 2


def cmd_approve(args: argparse.Namespace) -> int:
    bb = _open(args)
    try:
        status = approve(bb, args.project)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        bb.close()
    print(f"approved — {status} (run `resume` to finish S22)")
    return 0


def cmd_library(args: argparse.Namespace) -> int:
    bb = _open(args)
    try:
        from ytfactory.workers import vislib

        if args.seed:
            have = vislib.count(bb.conn)
            if have < args.seed:
                added = vislib.seed_abstract(args.seed - have, conn=bb.conn)
                print(f"seeded {added} abstract asset(s)")
            else:
                print("library already at target")
        vislib.backfill_embeddings(bb.conn, note=print)
        stats = vislib.stats(bb.conn)
        encoders = [
            label
            for label in (stats.get("clip_model"), stats.get("feature_model"))
            if label
        ]
        print(
            f"visual library: {stats['total']} asset(s) "
            f"({stats['generated']} generated, {stats['embedded']} embedded"
            + (f" via {', '.join(encoders)}" if encoders else "")
            + ")"
        )
        if stats["total"] == 0:
            print("hint: python -m ytfactory.cli library --seed 200")
    finally:
        bb.close()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    bb = _open(args)
    try:
        projects = bb.conn.execute(
            "SELECT id, slug, status, topic FROM projects ORDER BY id"
        ).fetchall()
        if not projects:
            print("no projects")
            return 0
        for p in projects:
            print(f"[{p['id']}] {p['slug']:<44} {p['status']:<16} {p['topic']}")
        if args.project:
            project = bb.resolve_project(args.project)
            if not project:
                print(f"no project matching {args.project!r}", file=sys.stderr)
                return 1
            print(f"\nsteps for {project['slug']}:")
            rows = {
                r["step_name"]: r
                for r in bb.conn.execute(
                    "SELECT step_name, status, gate, error FROM steps WHERE project_id = ?",
                    (project["id"],),
                ).fetchall()
            }
            for name in STEP_NAMES:
                row = rows.get(name)
                if row:
                    mark = {"done": "ok", "blocked": f"GATE {row['gate']}", "failed": "FAILED"}.get(
                        row["status"], row["status"]
                    )
                    print(f"  {name:<20} {mark}")
                    if row["error"] and row["status"] in ("blocked", "failed"):
                        print(f"    {row['error']}")
                else:
                    print(f"  {name:<20} -")
    finally:
        bb.close()
    return 0


def _probe_model(model: str, timeout: int) -> str:
    """One tiny completion per registry model. Returns 'ok' or 'fail: ...'."""
    os.environ.setdefault("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", ""))
    try:
        import litellm
    except ImportError as exc:  # pragma: no cover - environment dependent
        return f"fail: litellm not installed ({exc})"
    try:
        litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
            max_tokens=8,
            timeout=timeout,
        )
        return "ok"
    except Exception as exc:  # noqa: BLE001 — report any provider error, never crash
        return f"fail: {type(exc).__name__}: {str(exc)[:120]}"


def cmd_llm_check(args: argparse.Namespace) -> int:
    from ytfactory.orchestrator.router import Router, model_family

    router = Router(dry_run=True)  # registry only; no network until probe()
    results: dict[str, list[tuple[str, str, str]]] = {}  # tier -> [(model, family, verdict)]
    for tier in ("cheap", "writer", "verifier"):
        rows = []
        for model in router.tier_models(tier):
            verdict = _probe_model(model, args.timeout)
            status = "OK  " if verdict == "ok" else verdict
            rows.append((model, model_family(model), status))
            print(f"{tier:<10} {model:<32} {status}")
        results[tier] = rows

    ok = {t: [r for r in rows if r[2] == "OK  "] for t, rows in results.items()}
    print()
    empty = [t for t, hits in ok.items() if not hits]
    if empty:
        print(f"FAIL: no working model in tier(s): {', '.join(empty)}")

    writer_families = {r[1] for r in ok.get("writer", [])}
    verifier_families = {r[1] for r in ok.get("verifier", [])}
    unsafe: list[str] = []
    if not writer_families or not verifier_families:
        print("decorrelation: skipped (a tier has no working model)")
    else:
        # Runtime forbids only A4's actual family -> verifier must keep an
        # escape hatch for EVERY family A4 might end up using.
        unsafe = sorted(f for f in writer_families if not (verifier_families - {f}))
        if unsafe:
            print(
                f"FAIL: decorrelation at risk — if A4 uses {unsafe}, "
                f"verifier has no other family (verifier={sorted(verifier_families)})"
            )
        else:
            print(
                f"decorrelation: OK (writer={sorted(writer_families)}, "
                f"verifier={sorted(verifier_families)}, "
                f"verifier-only={sorted(verifier_families - writer_families)})"
            )
    total = sum(len(v) for v in results.values())
    print(f"models OK: {sum(len(v) for v in ok.values())}/{total}")
    healthy = not empty and bool(writer_families) and bool(verifier_families) and not unsafe
    return 0 if healthy else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ytfactory", description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="path to blackboard sqlite")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_live(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dry-run", action="store_true", help="zero-API run (default)")
        p.add_argument("--live", action="store_true", help="real API/worker execution")
        p.add_argument(
            "--until",
            metavar="STEP",
            help="run up to and including STEP (e.g. S17), then pause",
        )
        p.add_argument(
            "--set",
            dest="set",
            action="append",
            metavar="KEY=VALUE",
            help="config override, repeatable (e.g. --set gpu_timeout_s=900)",
        )

    p_run = sub.add_parser("run", help="create a project and run S00–S22")
    p_run.add_argument("topic", help="video topic / research question")
    p_run.add_argument("--audience", default="beginner")
    p_run.add_argument("--duration", type=int, default=8, help="target minutes")
    p_run.add_argument("--wpm", type=int, default=150, help="target words per minute")
    p_run.add_argument(
        "--tts-backend",
        choices=["kaggle_batch", "local_kokoro", "local_piper"],
        default=None,
        help="pin the S10 TTS rung for this project (default: auto chain)",
    )
    add_live(p_run)
    p_run.set_defaults(func=cmd_run)

    p_resume = sub.add_parser("resume", help="resume a project from where it stopped")
    p_resume.add_argument("project", help="slug or id")
    add_live(p_resume)
    p_resume.set_defaults(func=cmd_resume)

    p_rerun = sub.add_parser("rerun", help="invalidate from a step and re-run")
    p_rerun.add_argument("project", help="slug or id")
    p_rerun.add_argument("--from", dest="step", required=True, help="step name, e.g. S03")
    add_live(p_rerun)
    p_rerun.set_defaults(func=cmd_rerun)

    p_approve = sub.add_parser("approve", help="human sign-off at the S21 gate")
    p_approve.add_argument("project", help="slug or id")
    p_approve.set_defaults(func=cmd_approve)

    p_library = sub.add_parser("library", help="manage the reusable visual library")
    p_library.add_argument("--seed", type=int, default=0, metavar="N",
                           help="ensure at least N abstract assets (FR-V-09)")
    p_library.set_defaults(func=cmd_library)

    p_list = sub.add_parser("list", help="list projects and their step states")
    p_list.add_argument("--project", help="show step detail for one project")
    p_list.set_defaults(func=cmd_list)

    p_llm = sub.add_parser(
        "llm-check",
        help="ping every registry model (live), verify tiers + decorrelation",
    )
    p_llm.add_argument("--timeout", type=int, default=30, help="per-model timeout (s)")
    p_llm.set_defaults(func=cmd_llm_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
