#!/usr/bin/env python3
"""Parts B and C: the three-level offline test suite (RAGOps 4.2.5, Table 1) plus the release gate.

One command runs everything against a LIVE pipeline and writes a timestamped result file and a
summary table to eval/results/:

    python eval/run_eval_v2.py --api http://localhost:8000 --label baseline
    python eval/run_eval_v2.py --api http://localhost:8001 --label candidate        # gate vs baseline

On the cluster the same command runs as a Job (k8s/eval-job.yaml).

Levels
  1. Module (embedding)   paraphrase vs unrelated cosine, gap, ms/chunk, determinism
                          -> calls the pinned EMBED_MODEL on Ollama directly
  2. Component (retrieval) recall@3, recall@5, MRR, nDCG@5, plus a typo-noise run
                          -> POST /api/retrieve (no LLM, so generation cannot hide retrieval)
  3. End-to-end (answers) faithfulness (LLM judge), human/judge agreement, abstention
                          correctness, citation validity, latency p50/p95
                          -> POST /api/ask with Cache-Control: no-cache
Stochasticity: the whole suite runs twice with fixed seeds; every metric's difference is reported.

There is also an in-process stub mode (--offline-stub) for CI plumbing only. Its numbers come from a
keyword-routed stub retriever and mean nothing, so it refuses to write into eval/results/ unless
--allow-stub is passed, and the file is labelled "stub-ci".
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "indexer"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from eval.gate import evaluate_gate, load_thresholds  # noqa: E402

AskFn = Callable[[str], dict[str, Any]]


def _cosine(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1)) or 1.0
    n2 = math.sqrt(sum(b * b for b in v2)) or 1.0
    return dot / (n1 * n2)


def _pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, math.ceil(q * len(s)) - 1)
    return s[min(k, len(s) - 1)]


# =============================================================================
# Level 1: module (embedding)
# =============================================================================

def run_module_level_test(benchmark_path: Path) -> dict[str, Any]:
    from pipeline.config import get_settings
    from pipeline.embed import embed_batch, embedder_in_use

    bench = json.loads(benchmark_path.read_text(encoding="utf-8"))
    para = bench.get("paraphrase_pairs", [])
    unrel = bench.get("unrelated_pairs", [])
    det_text = bench.get("determinism_text", "Sample policy text for determinism testing.")
    settings = get_settings()

    texts = [t for pair in para + unrel for t in pair]
    embed_batch([texts[0]], settings)  # warm-up: loads the model, so its load time is not timed

    t0 = time.perf_counter()
    vecs: list[list[float]] = []
    for t in texts:  # one call per text: we want time per chunk, not per batch
        vecs.extend(embed_batch([t], settings))
    ms_per_chunk = (time.perf_counter() - t0) * 1000.0 / max(1, len(texts))

    sims = [_cosine(vecs[2 * i], vecs[2 * i + 1]) for i in range(len(para) + len(unrel))]
    para_sims, unrel_sims = sims[: len(para)], sims[len(para):]

    v1 = embed_batch([det_text], settings)[0]
    v2 = embed_batch([det_text], settings)[0]
    max_diff = max((abs(a - b) for a, b in zip(v1, v2)), default=0.0)

    mean_p = statistics.fmean(para_sims) if para_sims else 0.0
    mean_u = statistics.fmean(unrel_sims) if unrel_sims else 0.0
    return {
        "embed_model": settings.embed_model,
        "embedder": embedder_in_use(),
        "paraphrase_pairs": len(para),
        "unrelated_pairs": len(unrel),
        "paraphrase_mean_sim": round(mean_p, 4),
        "unrelated_mean_sim": round(mean_u, 4),
        "cosine_gap": round(mean_p - mean_u, 4),
        "paraphrase_min_sim": round(min(para_sims), 4) if para_sims else None,
        "unrelated_max_sim": round(max(unrel_sims), 4) if unrel_sims else None,
        "embedding_ms_per_chunk": round(ms_per_chunk, 2),
        "determinism_max_diff": max_diff,
        "dimension": len(v1),
    }


# =============================================================================
# Level 2: component (retrieval)
# =============================================================================

def add_typos(query: str, seed: int = 7) -> str:
    """Deterministic noise: swap two inner letters in every second word longer than 4 letters."""
    rng = random.Random(f"{seed}:{query}")
    words = query.split()
    out = []
    for i, w in enumerate(words):
        if len(w) > 4 and w.isalpha() and i % 2 == 0:
            j = rng.randint(1, len(w) - 3)
            w = w[:j] + w[j + 1] + w[j] + w[j + 2:]
        out.append(w)
    return " ".join(out)


def _is_relevant(hit: dict[str, Any], q: dict[str, Any]) -> bool:
    """A hit is relevant if it comes from the ground-truth document (and page, when the golden
    question gives one and the hit carries one)."""
    doc = (q.get("ground_truth_doc") or "").lower()
    if not doc or doc not in str(hit.get("source", "")).lower():
        return False
    gt_page = q.get("ground_truth_page")
    if gt_page is not None and hit.get("page") is not None:
        return int(hit["page"]) == int(gt_page)
    return True


def _rank_metrics(hits: list[dict[str, Any]], q: dict[str, Any]) -> dict[str, float]:
    ranks = [i for i, h in enumerate(hits, start=1) if _is_relevant(h, q)]
    first = ranks[0] if ranks else None
    return {
        "recall_at_3": 1.0 if first and first <= 3 else 0.0,
        "recall_at_5": 1.0 if first and first <= 5 else 0.0,
        "mrr": 1.0 / first if first else 0.0,
        # one relevant item (the ground-truth document): ideal DCG = 1
        "ndcg_at_5": 1.0 / math.log2(first + 1) if first and first <= 5 else 0.0,
    }


def run_component_level_test(golden: list[dict[str, Any]], retrieve: AskFn, noise: bool = True) -> dict[str, Any]:
    answerable = [q for q in golden if not q.get("expected_abstain") and q.get("ground_truth_doc")]

    def _run(transform: Callable[[str], str]) -> tuple[dict[str, float], list[dict[str, Any]]]:
        rows = []
        for q in answerable:
            resp = retrieve(transform(q["question"]))
            hits = resp.get("retrieved", [])
            m = _rank_metrics(hits, q)
            rows.append({"id": q["id"], "category": q.get("category"),
                         "top_sources": [h.get("source") for h in hits[:5]],
                         "top1_score": hits[0].get("score") if hits else None, **m})
        agg = {k: round(statistics.fmean(r[k] for r in rows), 4) if rows else 0.0
               for k in ("recall_at_3", "recall_at_5", "mrr", "ndcg_at_5")}
        return agg, rows

    clean, rows = _run(lambda s: s)
    by_cat: dict[str, dict[str, float]] = {}
    for cat in sorted({str(r["category"]) for r in rows}):
        sub = [r for r in rows if str(r["category"]) == cat]
        by_cat[cat] = {"n": len(sub), "recall_at_5": round(statistics.fmean(r["recall_at_5"] for r in sub), 4)}

    out: dict[str, Any] = {**clean, "questions": len(rows), "by_category": by_cat, "per_question": rows}
    if noise:
        noisy, _ = _run(add_typos)
        out["noise"] = {"type": "typos (deterministic letter swaps)", **noisy,
                        "delta": {k: round(noisy[k] - clean[k], 4) for k in noisy}}
    return out


# =============================================================================
# Level 3: end to end (answers)
# =============================================================================

JUDGE_PROMPT = """You are grading a retrieval-augmented answer.
QUESTION: {question}
CONTEXT (the passages the system cited):
<<<
{context}
>>>
ANSWER:
<<<
{answer}
>>>
Read ANSWER as the answer to QUESTION. Is every fact it states (numbers, dates, rules) written in
CONTEXT? Ignore facts in CONTEXT that the answer does not use. A short answer such as "80 percent"
is faithful if CONTEXT states it for this question.
Reply with JSON only: {{"faithful": true|false, "reason": "<one short sentence>"}}"""


def _lexical_support(answer: str, context: str) -> float:
    """Fallback judge: share of the answer's content words (and all its numbers) found in the context."""
    stop = {"the", "a", "an", "of", "to", "in", "is", "are", "and", "or", "for", "on", "at", "be",
            "by", "with", "that", "this", "it", "as", "from", "according", "student", "students"}
    words = [w for w in re.findall(r"[a-z0-9.%]+", answer.lower()) if w not in stop and len(w) > 2]
    if not words:
        return 0.0
    ctx = context.lower()
    return sum(1 for w in words if w in ctx) / len(words)


def _parse_verdict(content: str) -> bool | None:
    """Small models do not always return exactly {"faithful": true}: accept "true"/"yes"/1,
    any key that contains "faithful", or a bare yes/no."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        for k, v in data.items():
            if "faithful" in str(k).lower() or str(k).lower() in {"supported", "verdict", "answer"}:
                if isinstance(v, bool):
                    return v
                sv = str(v).strip().lower()
                if sv in {"true", "yes", "1", "faithful", "supported"}:
                    return True
                if sv in {"false", "no", "0", "unfaithful", "not supported", "unsupported"}:
                    return False
    low = content.lower()
    if re.search(r"\b(not faithful|unfaithful|unsupported|false|no)\b", low):
        return False
    if re.search(r"\b(faithful|supported|true|yes)\b", low):
        return True
    return None


class Judge:
    """LLM-as-judge through Ollama (temperature 0, JSON mode). Falls back to a lexical support
    score if the judge model cannot be reached; the result file records which one was used."""

    def __init__(self, ollama_url: str, model: str, seed: int) -> None:
        self.url = ollama_url.rstrip("/") + "/api/chat"
        self.model = model
        self.seed = seed
        self.kind = f"llm:{model}"
        self._down = False

    def __call__(self, answer: str, context: str, question: str = "") -> tuple[float, str]:
        if not self._down:
            try:
                import httpx

                r = httpx.post(self.url, timeout=120.0, json={
                    "model": self.model, "stream": False, "format": "json",
                    "options": {"temperature": 0, "seed": self.seed},
                    "messages": [{"role": "user", "content": JUDGE_PROMPT.format(question=question, context=context[:4000], answer=answer)}],
                })
                r.raise_for_status()
                content = r.json()["message"]["content"]
                verdict = _parse_verdict(content)
                if verdict is None:
                    return 0.0, f"unparseable judge reply: {content[:160]}"
                return (1.0 if verdict else 0.0), content[:200]
            except Exception as exc:
                print(f"[warn] LLM judge unavailable ({exc}); falling back to lexical support", file=sys.stderr)
                self._down = True
                self.kind = "lexical-fallback"
        score = _lexical_support(answer, context)
        return (1.0 if score >= 0.8 else 0.0), f"lexical support {score:.2f}"


def load_human_labels(path: Path) -> dict[str, bool]:
    """eval/human_labels.jsonl: one line per answer you graded BY HAND,
    {"id": "q01", "faithful": true, "note": "..."}. At least 8 lines (A3 Part B)."""
    if not path.exists():
        return {}
    labels: dict[str, bool] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            if isinstance(rec.get("faithful"), bool):
                labels[str(rec["id"])] = rec["faithful"]
    return labels


def run_end_to_end_test(golden: list[dict[str, Any]], ask: AskFn, judge: Judge,
                        human: dict[str, bool]) -> dict[str, Any]:
    lat: list[float] = []
    faith: list[float] = []
    abst_ok: list[float] = []
    false_abst: list[float] = []
    cit_valid: list[float] = []
    contains: list[float] = []
    agree: list[float] = []
    rows: list[dict[str, Any]] = []

    for q in golden:
        t0 = time.perf_counter()
        resp = ask(q["question"])
        ms = (time.perf_counter() - t0) * 1000.0
        lat.append(ms)
        answered = bool(resp.get("answered_from_context"))
        cites = resp.get("citations", []) or []
        retrieved = resp.get("retrieved", []) or []
        row: dict[str, Any] = {"id": q["id"], "category": q.get("category"), "question": q["question"],
                               "answered_from_context": answered, "answer": resp.get("answer", ""),
                               "citations": cites, "trace_id": resp.get("trace_id"), "latency_ms": round(ms, 1)}

        if q.get("expected_abstain"):
            ok = (not answered) and not cites
            abst_ok.append(1.0 if ok else 0.0)
            row["abstention_correct"] = ok
        else:
            false_abst.append(0.0 if answered else 1.0)
            if answered:
                # Judge against the passages the answer CITES (top 3 retrieved if it cites none):
                # a 3B judge gets lost in five unrelated chunks.
                cited = {(str(c.get("source")), str(c.get("section"))) for c in cites}
                used = [r for r in retrieved if (str(r.get("source")), str(r.get("section"))) in cited] or retrieved[:3]
                context = "\n---\n".join(str(r.get("text") or "") for r in used)
                f, reason = judge(row["answer"], context, q["question"])
                faith.append(f)
                row.update(faithful_judge=bool(f), judge_reason=reason)
                if q["id"] in human:
                    agree.append(1.0 if bool(f) == human[q["id"]] else 0.0)
                    row["faithful_human"] = human[q["id"]]
                keys = {(str(r.get("source")), str(r.get("section"))) for r in retrieved}
                if cites:
                    cit_valid.append(sum(1 for c in cites if (str(c.get("source")), str(c.get("section"))) in keys) / len(cites))
                gt = str(q.get("ground_truth_answer") or "").lower()
                if gt:
                    contains.append(1.0 if gt in row["answer"].lower() else 0.0)
        rows.append(row)

    mean = lambda xs: round(statistics.fmean(xs), 4) if xs else None  # noqa: E731
    return {
        "judge": judge.kind,
        "faithfulness": mean(faith),
        "faithfulness_n": len(faith),
        "human_labelled": len(agree),
        "human_judge_agreement": mean(agree),
        "abstention_correctness": mean(abst_ok),
        "unanswerable_n": len(abst_ok),
        "false_abstention_rate": mean(false_abst),
        "citation_validity": mean(cit_valid) if cit_valid else 1.0,
        "answer_contains_ground_truth": mean(contains),
        "latency_p50_ms": round(_pct(lat, 0.50), 1),
        "latency_p95_ms": round(_pct(lat, 0.95), 1),
        "total_questions": len(golden),
        "per_question": rows,
    }


# =============================================================================
# Clients
# =============================================================================

def http_clients(api: str) -> tuple[AskFn, AskFn]:
    import httpx

    # No keep-alive: uvicorn closes idle connections after 5 s, and the LLM judge runs between
    # questions, so a pooled connection is often already closed ("Server disconnected").
    client = httpx.Client(timeout=300.0, headers={"Cache-Control": "no-cache"},
                          limits=httpx.Limits(max_keepalive_connections=0))
    base = api.rstrip("/")

    def _post(path: str, q: str) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = client.post(f"{base}{path}", json={"question": q})
                r.raise_for_status()
                return dict(r.json())
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last = exc
                print(f"[warn] {path} attempt {attempt + 1} failed: {exc!r}; retrying", file=sys.stderr)
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"{path} failed 3 times for {q!r}") from last

    def ask(q: str) -> dict[str, Any]:
        return _post("/api/ask", q)

    def retrieve(q: str) -> dict[str, Any]:
        return _post("/api/retrieve", q)

    return ask, retrieve


def stub_clients() -> tuple[AskFn, AskFn]:
    """CI only: in-process API with the stub retriever and provider."""
    sys.path.insert(0, str(ROOT / "api"))
    from app.config import Settings
    from app.providers.llm.stub import StubProvider
    from app.retrieval.stub import StubRetriever
    from app.services.answer import answer_question, retrieve_only

    settings = Settings(retriever="stub", llm_provider="stub", top_k=5, trace_file=os.devnull)
    retr, prov = StubRetriever(), StubProvider()

    def ask(q: str) -> dict[str, Any]:
        return answer_question(q, retr, prov, settings, use_cache=False).model_dump()

    def retrieve(q: str) -> dict[str, Any]:
        passages, _ = retrieve_only(q, retr, settings)
        return {"retrieved": [p.model_dump() for p in passages]}

    return ask, retrieve


# =============================================================================
# Suite
# =============================================================================

def _flat(d: dict[str, Any], prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in d.items():
        if k in ("per_question", "by_category"):
            continue
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flat(v, key + "."))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = float(v)
    return out


def run_once(golden: list[dict[str, Any]], bench: Path, ask: AskFn, retrieve: AskFn,
             judge: Judge, human: dict[str, bool], seed: int) -> dict[str, Any]:
    random.seed(seed)
    return {
        "module_level": run_module_level_test(bench),
        "component_level": run_component_level_test(golden, retrieve),
        "end_to_end_level": run_end_to_end_test(golden, ask, judge, human),
    }


def summary_table(rep: dict[str, Any]) -> str:
    m, c, e = rep["module_level"], rep["component_level"], rep["end_to_end_level"]
    n = c.get("noise", {})
    lines = [
        f"# Evaluation: {rep['label']} ({rep['timestamp']})",
        "",
        f"API: `{rep['api']}`  collection: `{rep.get('collection')}`  index: `{rep.get('embedding_version')}`  judge: `{e['judge']}`",
        "",
        "| Level | Metric | Run 1 | Run 2 delta |",
        "| --- | --- | ---: | ---: |",
    ]
    d = rep["stochasticity"]["deltas"]

    def row(level: str, name: str, key: str, val: Any) -> None:
        lines.append(f"| {level} | {name} | {val} | {d.get(key, 0.0):+.4g} |")

    row("Module", f"paraphrase mean cos ({m['paraphrase_pairs']} pairs)", "module_level.paraphrase_mean_sim", m["paraphrase_mean_sim"])
    row("Module", f"unrelated mean cos ({m['unrelated_pairs']} pairs)", "module_level.unrelated_mean_sim", m["unrelated_mean_sim"])
    row("Module", "gap", "module_level.cosine_gap", m["cosine_gap"])
    row("Module", "embedding ms / chunk", "module_level.embedding_ms_per_chunk", m["embedding_ms_per_chunk"])
    row("Module", "determinism max diff", "module_level.determinism_max_diff", m["determinism_max_diff"])
    for k in ("recall_at_3", "recall_at_5", "mrr", "ndcg_at_5"):
        row("Component", k, f"component_level.{k}", c[k])
    for k in ("recall_at_3", "recall_at_5", "mrr", "ndcg_at_5"):
        if k in n:
            row("Component", f"{k} with typos (delta {n['delta'][k]:+.4f})", f"component_level.noise.{k}", n[k])
    row("End-to-end", f"faithfulness (n={e['faithfulness_n']})", "end_to_end_level.faithfulness", e["faithfulness"])
    row("End-to-end", f"judge vs human agreement (n={e['human_labelled']})", "end_to_end_level.human_judge_agreement", e["human_judge_agreement"])
    row("End-to-end", f"abstention correctness (n={e['unanswerable_n']})", "end_to_end_level.abstention_correctness", e["abstention_correctness"])
    row("End-to-end", "false abstention on answerable", "end_to_end_level.false_abstention_rate", e["false_abstention_rate"])
    row("End-to-end", "citation validity", "end_to_end_level.citation_validity", e["citation_validity"])
    row("End-to-end", "answer contains ground truth", "end_to_end_level.answer_contains_ground_truth", e["answer_contains_ground_truth"])
    row("End-to-end", "latency p50 ms", "end_to_end_level.latency_p50_ms", e["latency_p50_ms"])
    row("End-to-end", "latency p95 ms", "end_to_end_level.latency_p95_ms", e["latency_p95_ms"])
    lines += ["", f"Gate: **{rep['gate']['decision']}**"]
    lines += [f"- {x}" for x in rep["gate"]["checks"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Parts B and C: three-level evaluation and release gate.")
    ap.add_argument("--api", default=os.environ.get("EVAL_API_URL"), help="Base URL of the RAHBAR API under test")
    ap.add_argument("--label", "--mode", dest="label", default=os.environ.get("EVAL_LABEL", "baseline"),
                    help="Name of this run: baseline, candidate, failing_candidate, after_batch_b, ...")
    ap.add_argument("--golden", default="eval/golden.jsonl")
    ap.add_argument("--benchmark", default="eval/fixtures/embedding_benchmark.json")
    ap.add_argument("--human-labels", default="eval/human_labels.jsonl")
    ap.add_argument("--gate-config", default=os.environ.get("GATE_CONFIG", "eval/gate_config.yaml"))
    ap.add_argument("--baseline", default=None, help="Baseline result file (default: <output-dir>/latest_baseline.json)")
    ap.add_argument("--output-dir", default=os.environ.get("RESULTS_DIR", "eval/results"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--runs", type=int, default=2, choices=[1, 2],
                    help="2 (default) measures stochasticity for the baseline (Part B); "
                         "candidates are compared once against that baseline")
    ap.add_argument("--offline-stub", action="store_true", help="CI only: in-process stub pipeline")
    ap.add_argument("--allow-stub", action="store_true", help="Let --offline-stub write a result file")
    args = ap.parse_args()

    if args.offline_stub:
        os.environ.setdefault("ALLOW_FAKE_EMBEDDINGS", "1")
        ask, retrieve = stub_clients()
        api_name = "in-process stub (CI only; numbers are meaningless)"
    elif args.api:
        ask, retrieve = http_clients(args.api)
        api_name = args.api
    else:
        ap.error("give --api http://... (the live pipeline). --offline-stub is for CI plumbing only.")

    out_dir = Path(args.output_dir)
    golden = [json.loads(x) for x in Path(args.golden).read_text(encoding="utf-8").splitlines() if x.strip()]
    human = load_human_labels(Path(args.human_labels))
    judge = Judge(os.environ.get("OLLAMA_URL", "http://localhost:11434"),
                  os.environ.get("JUDGE_MODEL", os.environ.get("LLM_MODEL", "llama3.2:3b")), args.seed)

    info: dict[str, Any] = {}
    try:
        info = retrieve("index info probe")
    except Exception as exc:
        print(f"[error] cannot reach the API at {api_name}: {exc}", file=sys.stderr)
        return 2

    run1 = run_once(golden, Path(args.benchmark), ask, retrieve, judge, human, args.seed)
    if args.runs == 2:
        run2 = run_once(golden, Path(args.benchmark), ask, retrieve, judge, human, args.seed)
        f1, f2 = _flat(run1), _flat(run2)
        deltas = {k: round(f2[k] - f1[k], 6) for k in f1 if k in f2}
    else:
        deltas = {}

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report: dict[str, Any] = {
        "timestamp": ts,
        "label": args.label,
        "pipeline_mode": "stub-ci" if args.offline_stub else "live",
        "api": api_name,
        "collection": info.get("collection"),
        "embedding_version": info.get("embedding_version"),
        "golden_file": args.golden,
        "golden_questions": len(golden),
        "seed": args.seed,
        **run1,
        "stochasticity": {
            "runs": args.runs,
            "deltas": deltas,
            "max_abs_delta": max((abs(v) for v in deltas.values()), default=0.0),
            "note": "Seeds fixed and temperature low; remaining differences come from latency jitter, "
                    "LLM sampling on the server, and float non-determinism in the embedding runtime.",
        },
    }

    baseline = None
    b_path = Path(args.baseline) if args.baseline else out_dir / "latest_baseline.json"
    if args.label != "baseline" and b_path.exists():
        baseline = json.loads(b_path.read_text(encoding="utf-8"))
    thresholds = load_thresholds(Path(args.gate_config))
    passed, violations, checks = evaluate_gate(report, baseline, thresholds)
    report["gate"] = {"decision": "PASS" if passed else "FAIL", "violations": violations, "checks": checks,
                      "baseline_file": str(b_path) if baseline else None, "thresholds": thresholds}

    table = summary_table(report)
    print(table)

    if args.offline_stub and not args.allow_stub:
        print("[stub] not writing results: stub numbers must never be reported (pass --allow-stub for CI).")
        return 0 if passed else 1

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = ts.replace(":", "-")
    run_file = out_dir / f"eval_{args.label}_{stamp}.json"
    run_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / f"eval_{args.label}_{stamp}.md").write_text(table, encoding="utf-8")
    (out_dir / f"latest_{args.label}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    labelling = out_dir / f"answers_to_label_{args.label}_{stamp}.jsonl"
    with labelling.open("w", encoding="utf-8") as fh:
        for r in report["end_to_end_level"]["per_question"]:
            if r.get("answered_from_context"):
                fh.write(json.dumps({"id": r["id"], "question": r["question"], "answer": r["answer"],
                                     "judge_said_faithful": r.get("faithful_judge"), "faithful": None}) + "\n")
    print(f"[+] wrote {run_file} (+ .md summary, latest_{args.label}.json, {labelling.name})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
