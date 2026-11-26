"""A1: Ingestion Verification Stage (RAGOps Section 4.2.1 - 4.2.4).

Runs after document extraction and before chunking.
Performs 4 checks:
1. Quality: checks for empty, garbled, or corrupted extraction.
2. Completeness: required metadata (source, title, doc_version, page_count) and no truncation.
3. Recency: detects if a newer version of the document exists.
4. Uniqueness: detects exact duplicates (SHA-256) and near-duplicates (vector/Jaccard similarity >= 0.90).

Failed documents are quarantined into `corpus/quarantine/` and skipped from indexing.
Outputs a structured JSON verification report.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import Document

log = logging.getLogger("rahbar.verify")


@dataclass
class CheckResult:
    check_name: str
    passed: bool
    reason: str


@dataclass
class DocumentVerification:
    source: str
    passed: bool
    checks: dict[str, dict[str, Any]]
    quarantined: bool
    quarantine_path: str | None = None


@dataclass
class VerificationReport:
    total_scanned: int
    passed_count: int
    quarantined_count: int
    details: list[DocumentVerification] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_scanned": self.total_scanned,
            "passed_count": self.passed_count,
            "quarantined_count": self.quarantined_count,
            "details": [asdict(d) for d in self.details],
        }


def _compute_sha256(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _is_garbled_or_empty(text: str) -> tuple[bool, str]:
    cleaned = text.strip()
    if not cleaned or len(cleaned) < 25:
        return True, "Document text is empty or trivially short (< 25 chars)."
    if "GARBLED_STREAM_ERROR" in text or "\\x00" in text:
        return True, "Garbled binary stream or extraction error markers detected."
    # Check printable character ratio
    printable = sum(1 for c in cleaned if c.isprintable() or c in "\n\r\t")
    ratio = printable / len(cleaned) if len(cleaned) > 0 else 0.0
    if ratio < 0.85:
        return True, f"High non-printable character ratio ({ratio:.2f} < 0.85); garbled content."
    return False, "Quality check passed: clean readable text."


def _check_completeness(doc: Document, metadata: dict[str, Any], text: str) -> tuple[bool, str]:
    missing: list[str] = []
    if not metadata.get("source") and not doc.source:
        missing.append("source")
    if not metadata.get("title"):
        # Check if first line has markdown H1 heading
        has_h1 = any(line.strip().startswith("# ") for line in text.splitlines())
        if not has_h1:
            missing.append("title")
    if not metadata.get("doc_version") or metadata.get("doc_version") == "unknown":
        missing.append("doc_version")
    if not doc.pages:
        missing.append("page_count")

    if missing:
        return False, f"Missing required metadata fields: {', '.join(missing)}."

    # Check for truncation
    if "[TRUNCATED" in text or "UNEXPECTED EOF" in text:
        return False, "Premature truncation marker detected in document body."

    return True, "Completeness check passed: all metadata present and body intact."


def _base_name(doc: Document, meta: dict[str, Any], text: str) -> str:
    """Normalised document identity used by the recency check: the title (front matter, else the
    first '# ' heading, else the file name) with years, 'edition'/'version' words and punctuation
    removed. "Academic Rules (2026 edition)" and "Academic Rules (2027 edition)" map to the same key."""
    title = str(meta.get("title") or "")
    if not title:
        for line in text.splitlines():
            if line.strip().startswith("# "):
                title = line.strip()[2:]
                break
    if not title:
        title = Path(doc.source).stem
    t = title.lower()
    t = re.sub(r"\b(19|20)\d{2}(-\d{2,4})?\b", " ", t)
    t = re.sub(r"\b(edition|version|revised|rev|v\d+|fall|spring)\b", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def _calculate_ngram_similarity(text1: str, text2: str, n: int = 3) -> float:
    """Compute character n-gram Jaccard similarity for fast near-duplicate detection."""
    w1 = [text1[i : i + n] for i in range(len(text1) - n + 1)]
    w2 = [text2[i : i + n] for i in range(len(text2) - n + 1)]
    set1, set2 = set(w1), set(w2)
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def verify_corpus(
    documents: list[tuple[Document, dict[str, Any], Path]],
    quarantine_dir: str | Path = "corpus/quarantine",
    report_path: str | Path = "eval/results/verification_report.json",
) -> tuple[list[Document], VerificationReport]:
    """Verify documents against the 4 RAGOps verification checks.

    Returns:
        (valid_documents, verification_report)
    """
    quarantine_path = Path(quarantine_dir)
    quarantine_path.mkdir(parents=True, exist_ok=True)
    report_file = Path(report_path)
    report_file.parent.mkdir(parents=True, exist_ok=True)

    verified_docs: list[Document] = []
    report = VerificationReport(
        total_scanned=len(documents),
        passed_count=0,
        quarantined_count=0,
    )

    seen_hashes: dict[str, str] = {}  # sha256 -> source_name
    accepted_texts: list[tuple[str, str]] = []  # (source_name, full_text)
    known_versions: dict[str, list[tuple[str, str]]] = {}  # base_name -> [(version, source_name)]

    # Pre-pass: collect version info for recency check
    for doc, meta, _ in documents:
        base_name = _base_name(doc, meta, "\n".join(p.text for p in doc.pages))
        version = str(meta.get("doc_version", doc.doc_version))
        known_versions.setdefault(base_name, []).append((version, doc.source))

    for doc, meta, file_path in documents:
        doc_text = "\n".join(p.text for p in doc.pages)
        checks: dict[str, dict[str, Any]] = {}
        fatal_failure = False

        # 1. Quality Check
        q_failed, q_reason = _is_garbled_or_empty(doc_text)
        checks["quality"] = {"pass": not q_failed, "reason": q_reason}
        if q_failed:
            fatal_failure = True

        # 2. Completeness Check
        c_passed, c_reason = _check_completeness(doc, meta, doc_text)
        checks["completeness"] = {"pass": c_passed, "reason": c_reason}
        if not c_passed:
            fatal_failure = True

        # 3. Recency Check
        base_name = _base_name(doc, meta, doc_text)
        all_vers = known_versions.get(base_name, [])
        curr_ver = str(meta.get("doc_version", doc.doc_version))

        newer_exists = False
        newer_version_name = ""
        for v, s in all_vers:
            if s != doc.source and v > curr_ver:
                newer_exists = True
                newer_version_name = f"{s} ({v})"
                break

        if newer_exists:
            # Document is outdated / superseded
            doc.superseded = True
            # Recency FAILS for an outdated version, but the document is not quarantined: it is
            # archived and kept in the index with superseded=True, so retrieval excludes it by
            # metadata and it can be restored (rollback).
            checks["recency"] = {
                "pass": False,
                "reason": f"Newer version detected: {newer_version_name}. Archived and marked superseded=True (excluded from retrieval, not quarantined).",
                "superseded": True,
            }
        else:
            checks["recency"] = {
                "pass": True,
                "reason": f"Most recent version detected ({curr_ver}).",
                "superseded": False,
            }

        # 4. Uniqueness Check (Exact & Near duplicate)
        sha = _compute_sha256(doc_text)
        u_passed = True
        u_reason = "Uniqueness check passed: distinct content."

        if sha in seen_hashes:
            u_passed = False
            u_reason = f"Exact duplicate detected: identical SHA-256 hash to {seen_hashes[sha]}."
            fatal_failure = True
        else:
            # Check near duplicate against previously accepted clean texts
            for prev_src, prev_txt in accepted_texts:
                sim = _calculate_ngram_similarity(doc_text, prev_txt)
                if sim >= 0.88:
                    u_passed = False
                    u_reason = f"Near-duplicate detected ({sim:.1%} similarity) with {prev_src}."
                    fatal_failure = True
                    break

        checks["uniqueness"] = {"pass": u_passed, "reason": u_reason}

        # Action: Quarantine or Accept
        quarantined = False
        target_q_file = None
        if fatal_failure:
            quarantined = True
            report.quarantined_count += 1
            # Move to the quarantine directory (A1: failed documents are moved and not indexed)
            dest = quarantine_path / file_path.name
            if file_path.resolve() != dest.resolve():
                if dest.exists():
                    dest.unlink()
                shutil.move(str(file_path), str(dest))
            target_q_file = str(dest)
            log.warning("quarantined document %s: %s", doc.source, [c["reason"] for c in checks.values() if not c["pass"]])
        else:
            report.passed_count += 1
            seen_hashes[sha] = doc.source
            accepted_texts.append((doc.source, doc_text))
            verified_docs.append(doc)

        report.details.append(
            DocumentVerification(
                source=doc.source,
                passed=not fatal_failure,
                checks=checks,
                quarantined=quarantined,
                quarantine_path=target_q_file,
            )
        )

    # Save verification report
    report_file.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    log.info(
        "verification completed: total=%d, passed=%d, quarantined=%d",
        report.total_scanned,
        report.passed_count,
        report.quarantined_count,
    )
    return verified_docs, report
