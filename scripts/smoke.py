#!/usr/bin/env python3
"""End-to-end smoke test for a running kappajudge service (local compose, CI, or Cloud Run behind IAP).

Standard library only, so it runs on any CI runner. Prints one PASS/FAIL/SKIP line per check and
writes a JSON summary with --json-out. Exit 0 only if no check failed.

  # local / CI compose stack (also exercises migrations, the eval job and the gate CLI)
  SMOKE_API_KEY=... python scripts/smoke.py --base-url http://127.0.0.1:8504 \\
      --compose "docker compose -p p4-judge -f docker-compose.yml -f docker-compose.smoke.yml" \\
      --gate-cli --json-out results/smoke_local.json
  # deployed (staging / prod canary): IAP identity token + API key from Secret Manager
  SMOKE_API_KEY=... SMOKE_ID_TOKEN=... python3 scripts/smoke.py --base-url https://canary---... --json-out smoke.json

Secrets come only from the environment (SMOKE_API_KEY, SMOKE_ID_TOKEN), never from argv.
The label write uses the fixed labeler id `smoke-probe` (upsert: one row per item, ever), which the
app excludes from agreement statistics. --read-only skips every write.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import random
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LEAK_MARKERS = ("Traceback", 'File "', "psycopg", "site-packages", "starlette.", "Exception:")
RAG_CRITERIA = {"correctness": 4, "completeness": 4, "coherence": 4, "groundedness": 4, "safety": 2}
GENERAL_CRITERIA = {"correctness": 4, "completeness": 4, "coherence": 4}


class CheckFailed(AssertionError):
    pass


class Skip(Exception):
    pass


@dataclass
class Resp:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass
class Ctx:
    base: str
    api_key: str
    id_token: str
    timeout: float
    compose: list[str]
    gate_cli: bool
    read_only: bool
    skip_rate_limit: bool
    nonce: str = field(default_factory=lambda: f"{random.randrange(16**8):08x}")
    state: dict[str, Any] = field(default_factory=dict)

    def request(
        self,
        method: str,
        path: str,
        *,
        key: str | None = "",
        body: bytes | None = None,
        content_type: str = "application/json",
        retry_429: bool = True,
    ) -> Resp:
        """HTTP request; key="" uses the configured API key, None sends none. Honors Retry-After on 429."""
        headers = {"User-Agent": "kappajudge-smoke/1"}
        if self.id_token:
            headers["Authorization"] = f"Bearer {self.id_token}"  # IAP; the API key travels in X-API-Key
        k = self.api_key if key == "" else key
        if k:
            headers["X-API-Key"] = k
        if body is not None:
            headers["Content-Type"] = content_type
        resp = Resp(0, {}, b"")
        for _attempt in range(4):
            req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)  # noqa: S310
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - URL from the operator
                    resp = Resp(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
            except urllib.error.HTTPError as e:
                resp = Resp(e.code, {k.lower(): v for k, v in e.headers.items()}, e.read())
            if resp.status == 429 and retry_429:
                time.sleep(min(10, int(resp.headers.get("retry-after", "2"))))
                continue
            return resp
        return resp

    def api(self, path: str, **kw: Any) -> Resp:
        return self.request("GET", path, **kw)

    def post_json(self, path: str, payload: Any, **kw: Any) -> Resp:
        return self.request("POST", path, body=json.dumps(payload).encode(), **kw)

    def sh(
        self, *args: str, env: dict[str, str] | None = None, timeout: float = 120
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self.compose, *args], capture_output=True, text=True, timeout=timeout, cwd=ROOT, env=env, check=False
        )


def expect(cond: bool, msg: str) -> None:
    if not cond:
        raise CheckFailed(msg)


def no_leak(r: Resp, what: str) -> None:
    for m in LEAK_MARKERS:
        expect(m not in r.text, f"{what}: response leaks internals ({m!r})")


def last_json(text: str) -> dict[str, Any]:
    """The last JSON object printed by a CLI (progress lines may precede it)."""
    start = text.rfind("\n{")
    start = 0 if text.startswith("{") and start < 0 else start + 1
    out: dict[str, Any] = json.loads(text[start:])
    return out


# ---------------------------------------------------------------------------------------------- checks
def c_liveness(c: Ctx) -> str:
    r = c.request("GET", "/healthz", key=None)
    expect(r.status == 200 and r.json().get("status") == "ok", f"/healthz -> {r.status}")
    return "200 ok"


def c_readiness(c: Ctx) -> str:
    r = c.request("GET", "/readyz", key=None)
    data = r.json()
    expect(r.status == 200, f"/readyz -> {r.status} {data}")
    expect(data.get("database") is True and data.get("migrations_pending") == [], f"deps not ready: {data}")
    return "postgres reachable, 0 pending migrations"


def c_streamlit_health(c: Ctx) -> str:
    r = c.request("GET", "/_stcore/health", key=None)
    expect(r.status == 200 and r.text.strip() == "ok", f"/_stcore/health -> {r.status} {r.text[:40]!r}")
    return "200 ok"


def c_streamlit_page(c: Ctx) -> str:
    for path in ("/", "/label"):
        r = c.request("GET", path, key=None)
        expect(r.status == 200, f"{path} -> {r.status}")
        expect("text/html" in r.headers.get("content-type", ""), f"{path} is not HTML")
        expect('id="root"' in r.text, f"{path}: no Streamlit app root element")
    return "/ and /label serve the Streamlit app shell"


def c_security_headers(c: Ctx) -> str:
    for path, strict in (("/healthz", True), ("/api/v1/status", True), ("/", False)):
        h = c.request("GET", path, key=None).headers
        expect(h.get("x-content-type-options") == "nosniff", f"{path}: nosniff missing")
        expect(h.get("x-frame-options") == "DENY", f"{path}: X-Frame-Options missing")
        expect("frame-ancestors 'none'" in h.get("content-security-policy", ""), f"{path}: CSP frame-ancestors")
        expect(h.get("referrer-policy") == "no-referrer", f"{path}: Referrer-Policy missing")
        expect("uvicorn" not in h.get("server", "").lower(), f"{path}: server banner exposed")
        if strict:
            expect("default-src 'none'" in h.get("content-security-policy", ""), f"{path}: strict CSP missing")
    return "nosniff, DENY, CSP, no-referrer, no server banner"


def c_auth_rejected(c: Ctx) -> str:
    r = c.api("/api/v1/status", key=None, retry_429=True)
    expect(r.status == 401, f"no key -> {r.status} (want 401)")
    r = c.api("/api/v1/status", key="wrong-" + "x" * 30)
    expect(r.status == 401, f"wrong key -> {r.status} (want 401)")
    no_leak(r, "401")
    return "no key 401, wrong key 401"


def c_auth_accepted(c: Ctx) -> str:
    r = c.api("/api/v1/status")
    expect(r.status == 200, f"valid key -> {r.status}")
    data = r.json()
    for k in ("service", "env", "llm_calls_enabled", "label_writes_enabled", "schema"):
        expect(k in data, f"status lacks {k}")
    c.state["status"] = data
    return f"200 (env={data['env']})"


def c_kill_switch_state(c: Ctx) -> str:
    st = c.state.get("status") or c.api("/api/v1/status").json()
    expect(
        st["llm_calls_enabled"] is False,
        "the UI service must run with LLM_CALLS_ENABLED=false (it never calls a model)",
    )
    return f"UI LLM kill switch on; label writes {'enabled' if st['label_writes_enabled'] else 'DISABLED'}"


def c_queue_lists_items(c: Ctx) -> str:
    r = c.api("/api/v1/queue?labeler_id=smoke-reader&source=p1&limit=5")
    expect(r.status == 200, f"queue -> {r.status}")
    items = r.json()["items"]
    expect(1 <= len(items) <= 5, f"expected 1..5 queue items from Postgres, got {len(items)}")
    for it in items:
        expect(set(it) == {"item_id", "source", "kind", "task", "response"}, f"bad item schema {sorted(it)}")
        expect(it["source"] == "p1" and it["kind"] in ("rag", "general"), f"bad item {it['item_id']}")
    ids = [it["item_id"] for it in items]
    expect(ids == sorted(ids), "queue is not in stable item order")
    c.state["queue_item"] = items[0]
    return f"{len(items)} items, first {ids[0]}"


def _label(c: Ctx, **over: Any) -> dict[str, Any]:
    item = c.state.get("queue_item") or {"item_id": "p1-q001-hybrid", "kind": "rag"}
    crit = RAG_CRITERIA if item["kind"] == "rag" else GENERAL_CRITERIA
    body = {"item_id": item["item_id"], "labeler_id": "smoke-probe", "pass_no": 1, "scores": dict.fromkeys(crit, 1)}
    body.update(over)
    return body


def c_input_validation(c: Ctx) -> str:
    cases = {
        "score out of range": _label(c, scores={**_label(c)["scores"], "correctness": 9}),
        "missing criteria": _label(c, scores={"correctness": 1}),
        "bad labeler id": _label(c, labeler_id="bad id;--"),
        "unknown field": {**_label(c), "admin": True},
    }
    for name, body in cases.items():
        r = c.post_json("/api/v1/labels", body)
        expect(r.status == 422, f"{name} -> {r.status} (want 422)")
        no_leak(r, name)
    r = c.post_json("/api/v1/labels", _label(c, labeler_id="owner"))
    expect(r.status == 403, f"API write as a human labeler -> {r.status} (want 403)")
    r = c.api("/api/v1/queue?labeler_id=smoke-reader&limit=999")
    expect(r.status == 422, f"limit=999 -> {r.status} (want 422)")
    return f"{len(cases)} bad bodies 422, human labeler 403, bad query 422"


def c_oversize_rejected(c: Ctx) -> str:
    r = c.post_json("/api/v1/labels", _label(c, note="x" * 70000))
    expect(r.status == 413, f"70 KB body -> {r.status} (want 413)")
    return "70 KB body 413"


def c_errors_no_stack_traces(c: Ctx) -> str:
    r = c.request("POST", "/api/v1/labels", body=b'{"item_id": "p1-q0', retry_429=True)
    expect(r.status == 422 and r.json().get("error") == "invalid_json", f"malformed JSON -> {r.status}")
    no_leak(r, "malformed JSON")
    r = c.api("/api/v1/does-not-exist")
    expect(r.status == 404 and r.json().get("error") == "not_found", f"unknown API route -> {r.status}")
    no_leak(r, "404")
    r = c.post_json("/api/v1/labels", _label(c, item_id="no-such-item"))
    expect(r.status == 404, f"unknown item -> {r.status} (want 404, not a DB error)")
    no_leak(r, "unknown item")
    return "422/404 JSON bodies, no traces or driver errors"


def c_label_roundtrip(c: Ctx) -> str:
    if c.read_only:
        raise Skip("--read-only")
    item = c.state.get("queue_item")
    if item is None:
        raise CheckFailed("queue check must pass first")
    crit = RAG_CRITERIA if item["kind"] == "rag" else GENERAL_CRITERIA
    scores = {k: random.randint(0, mx) for k, mx in crit.items()}
    note = f"smoke {c.nonce}"
    r = c.post_json("/api/v1/labels", _label(c, scores=scores, note=note))
    expect(r.status == 201, f"write -> {r.status} {r.text[:200]}")
    r = c.api(f"/api/v1/labels?item_id={item['item_id']}&labeler_id=smoke-probe&pass_no=1")
    expect(r.status == 200, f"read back -> {r.status}")
    got = r.json()
    expect(got["scores"] == scores and got["note"] == note, f"read-back mismatch: {got.get('scores')} vs {scores}")
    q = c.api("/api/v1/queue?labeler_id=smoke-probe&source=p1&limit=50").json()["items"]
    expect(item["item_id"] not in [i["item_id"] for i in q], "labeled item still in the labeler's queue")
    return f"wrote+read {item['item_id']} scores={scores}; item left smoke-probe's queue"


def c_eval_runs_listed(c: Ctx) -> str:
    r = c.api("/api/v1/runs?kind=release_gate&limit=5")
    expect(r.status == 200 and isinstance(r.json().get("runs"), list), f"runs -> {r.status}")
    r = c.api("/api/v1/runs?kind=bogus")
    expect(r.status == 422, f"bad kind -> {r.status}")
    return "200 list; bad kind 422"


def c_rate_limit(c: Ctx) -> str:
    if c.skip_rate_limit:
        raise Skip("--skip-rate-limit")
    n = 150  # > burst x instances, so a few Cloud Run instances with per-instance buckets still trip

    def one(_: int) -> Resp:
        return c.api("/api/v1/does-not-exist", retry_429=False)

    with cf.ThreadPoolExecutor(max_workers=20) as ex:
        results = list(ex.map(one, range(n)))
    limited = [r for r in results if r.status == 429]
    expect(bool(limited), f"{n} parallel requests produced no 429")
    ra = limited[0].headers.get("retry-after", "")
    expect(ra.isdigit() and int(ra) >= 1, f"429 without a valid Retry-After ({ra!r})")
    time.sleep(min(int(ra), 10) + 1)
    r = c.api("/api/v1/status")
    expect(r.status == 200, f"no recovery after Retry-After: {r.status}")
    return f"{len(limited)}/{n} got 429, Retry-After={ra}s, recovered"


# ------------------------------------------------------------------------------ compose-only checks
def need_compose(c: Ctx) -> None:
    if not c.compose:
        raise Skip("needs --compose (local/CI stack)")


def c_migrations_idempotent(c: Ctx) -> str:
    need_compose(c)
    before = c.api("/api/v1/status").json()["schema"]["applied"]
    outs = []
    for _ in range(2):
        p = c.sh("run", "--rm", "-T", "migrate")
        expect(p.returncode == 0, f"migrate exit {p.returncode}: {p.stderr[-300:]}")
        outs.append(last_json(p.stdout))
    expect(outs[1] == {"applied": []}, f"second migrate applied {outs[1]}")
    after = c.api("/api/v1/status").json()["schema"]
    expect(after["applied"] == before and after["pending"] == [], f"schema changed: {before} -> {after}")
    expect(c.request("GET", "/readyz", key=None).status == 200, "not ready after re-running migrations")
    return f"2 re-runs applied nothing; versions {after['applied']}"


def c_eval_job_preflight(c: Ctx) -> str:
    need_compose(c)
    p = c.sh("run", "--rm", "-T", "evaljob", "python", "-m", "judgecal.evalrun", "--smoke")
    expect(p.returncode == 0, f"preflight exit {p.returncode}: {p.stdout[-300:]} {p.stderr[-300:]}")
    res = last_json(p.stdout)
    expect(res["ok"] is True, f"preflight not ok: {res}")
    return f"ok; checks={res['checks']}"


def c_eval_job_recorded_run(c: Ctx) -> str:
    need_compose(c)
    if c.read_only:
        raise Skip("--read-only")
    before = len(c.api("/api/v1/runs?kind=smoke&limit=100").json()["runs"])
    p = c.sh("run", "--rm", "-T", "evaljob")
    expect(p.returncode == 0, f"eval job exit {p.returncode}: {p.stderr[-400:]}")
    res = last_json(p.stdout)
    expect(res["gate_ok"] is True and res["usd"] == 0, f"recorded run: gate_ok={res['gate_ok']} usd={res['usd']}")
    runs = c.api("/api/v1/runs?kind=smoke&limit=100").json()["runs"]
    expect(len(runs) == min(before + 1, 100), f"run not stored ({before} -> {len(runs)})")
    expect(str(runs[-1]["run_id"]) == res["run_id"], "latest stored run is not this run")
    return f"8 items replayed ($0), gate ok, stored run {res['run_id'][:8]}"


def c_eval_job_drift_detected(c: Ctx) -> str:
    need_compose(c)
    p = c.sh(
        "run", "--rm", "-T", "evaljob", "python", "-m", "judgecal.evalrun", "--out", "/tmp/eval_runs",  # noqa: S108
        "--prompt", "/demo/rag_answer_weakened.md", "--fail-on-drift",
    )  # fmt: skip
    expect(p.returncode == 3, f"weakened prompt: exit {p.returncode} (want 3 = drift)")
    expect('"event": "eval_gate_drift"' in p.stderr, "no eval_gate_drift log event (alert source)")
    res = last_json(p.stdout)
    expect(res["gate_ok"] is False and all(f.startswith("regression:") for f in res["gate_failures"]), str(res))
    return f"drift exit 3 + eval_gate_drift event; {len(res['gate_failures'])} regressions"


def c_kill_switch_eval_job(c: Ctx) -> str:
    need_compose(c)
    p = c.sh(
        "run", "--rm", "-T", "-e", "LLM_RECORDED_MODE=false", "-e", "LLM_CALLS_ENABLED=false",
        "-e", "JUDGECAL_CACHE_DIR=/tmp/empty-cache", "evaljob",
    )  # fmt: skip
    expect(p.returncode != 0, "eval job succeeded with the kill switch on and an empty cache")
    expect("LLM_CALLS_ENABLED=false" in p.stderr, "eval job did not fail on the kill switch")
    expect('"event": "eval_run_failed"' in p.stderr, "no eval_run_failed log event (alert source)")
    return f"refused before any model call (exit {p.returncode}), eval_run_failed logged"


def need_gate_cli(c: Ctx) -> None:
    if not c.gate_cli:
        raise Skip("needs --gate-cli (repo checkout + judgecal installed)")


def _gate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "judgecal.gate", *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
        check=False,
    )


def c_gate_cli_baseline_passes(c: Ctx) -> str:
    need_gate_cli(c)
    p = _gate("--record", "eval_runs/baseline.json")
    res = json.loads(p.stdout)
    expect(p.returncode == 0 and res["ok"] is True, f"baseline gate exit {p.returncode}: {res.get('failures')}")
    return "exit 0, ok"


def c_gate_cli_regressed_fails(c: Ctx) -> str:
    need_gate_cli(c)
    p = _gate("--record", "tests/fixtures/regressed_record.json", "--prompt", "scripts/demo/rag_answer_weakened.md")
    res = json.loads(p.stdout)
    expect(p.returncode == 1 and res["ok"] is False, f"regressed record exit {p.returncode}")
    expect(res["failures"] and all(f.startswith("regression:") for f in res["failures"]), str(res["failures"]))
    return f"exit 1; {len(res['failures'])} regressions ({', '.join(f.split()[1] for f in res['failures'])})"


CHECKS: list[tuple[str, Callable[[Ctx], str]]] = [
    ("liveness", c_liveness),
    ("readiness", c_readiness),
    ("streamlit_health", c_streamlit_health),
    ("streamlit_app_page", c_streamlit_page),
    ("security_headers", c_security_headers),
    ("auth_rejected_without_or_with_wrong_key", c_auth_rejected),
    ("auth_accepted_with_key", c_auth_accepted),
    ("kill_switch_ui_never_calls_llm", c_kill_switch_state),
    ("labeling_queue_lists_items_from_postgres", c_queue_lists_items),
    ("input_validation_422", c_input_validation),
    ("oversize_body_413", c_oversize_rejected),
    ("errors_without_stack_traces", c_errors_no_stack_traces),
    ("label_write_read_roundtrip", c_label_roundtrip),
    ("eval_runs_listed", c_eval_runs_listed),
    ("migrations_idempotent", c_migrations_idempotent),
    ("eval_job_preflight", c_eval_job_preflight),
    ("eval_job_recorded_run_stored_gate_ok", c_eval_job_recorded_run),
    ("eval_job_drift_detected", c_eval_job_drift_detected),
    ("kill_switch_eval_job_refuses_llm", c_kill_switch_eval_job),
    ("gate_cli_passes_baseline", c_gate_cli_baseline_passes),
    ("gate_cli_fails_regressed_record", c_gate_cli_regressed_fails),
    ("rate_limit_429_retry_after", c_rate_limit),  # last: it spends the key's burst
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--api-key-env", default="SMOKE_API_KEY", help="env var holding the X-API-Key value")
    ap.add_argument("--id-token-env", default="SMOKE_ID_TOKEN", help="env var holding an IAP OIDC token (optional)")
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--compose", default="", help="compose command prefix; enables migration/eval-job checks")
    ap.add_argument("--gate-cli", action="store_true", help="also run the gate CLI checks (repo checkout)")
    ap.add_argument("--read-only", action="store_true", help="skip checks that write")
    ap.add_argument("--skip-rate-limit", action="store_true")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--only", default="", help="comma-separated check names")
    args = ap.parse_args(argv)
    ctx = Ctx(
        base=args.base_url.rstrip("/"),
        api_key=os.environ.get(args.api_key_env, ""),
        id_token=os.environ.get(args.id_token_env, ""),
        timeout=args.timeout,
        compose=shlex.split(args.compose),
        gate_cli=args.gate_cli,
        read_only=args.read_only,
        skip_rate_limit=args.skip_rate_limit,
    )
    if not ctx.api_key:
        print(f"FAIL setup: ${args.api_key_env} is empty (the API checks need a key)")
        return 2
    only = {s for s in args.only.split(",") if s}
    results: list[dict[str, Any]] = []
    t_start = time.perf_counter()
    for name, fn in CHECKS:
        if only and name not in only:
            continue
        t0 = time.perf_counter()
        try:
            detail, status = fn(ctx), "PASS"
        except Skip as s:
            detail, status = str(s), "SKIP"
        except CheckFailed as e:
            detail, status = str(e), "FAIL"
        except Exception as e:
            detail, status = f"{type(e).__name__}: {e}", "FAIL"
        ms = round((time.perf_counter() - t0) * 1000)
        print(f"{status:4} {name:44} {ms:6d} ms  {detail}", flush=True)
        results.append({"check": name, "status": status, "ms": ms, "detail": detail})
    counts = {s: sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "SKIP")}
    summary = {
        "target": ctx.base,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_s": round(time.perf_counter() - t_start, 1),
        "passed": counts["PASS"],
        "failed": counts["FAIL"],
        "skipped": counts["SKIP"],
        "total": len(results),
        "ok": counts["FAIL"] == 0,
        "mode": {"compose": bool(ctx.compose), "gate_cli": ctx.gate_cli, "read_only": ctx.read_only},
        "checks": results,
    }
    print(
        f"\nSMOKE {'PASS' if summary['ok'] else 'FAIL'}: {counts['PASS']} passed, {counts['FAIL']} failed, "
        f"{counts['SKIP']} skipped in {summary['duration_s']} s"
    )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
