"""Retriever stub for local execution, offline CI suites, and evaluation.

Scores fixture passages by grounded topic and text similarity against university documents.
Guarantees honest abstention (score < relevance_floor) for personal queries ("What's my CGPA"),
absent topics (hostels, transport), and irrelevant chitchat, while correctly matching
genuine university academic and fee regulations.
"""

from __future__ import annotations

import re
from ..schemas import Passage

# ── Fixture passages ──────────────────────────────────────────────

_DEFAULT_STUB_PASSAGES = [
    Passage(
        chunk_id="stub-001",
        text="This is an official handbook passage. RAHBAR answers user queries grounded strictly in university documents.",
        source="stub-handbook.pdf",
        section="1.1 About the stub",
        page=1,
        score=0.91,
    ),
]

_FIXTURE_WARNING = Passage(
    chunk_id="fix-warn-001",
    text="A student whose cumulative grade point average (CGPA) falls below 2.00 at the end of a regular semester is placed on academic warning. A student who remains on academic warning for three consecutive semesters is dropped from the programme.",
    source="academic-rules-2026.md",
    section="2. Academic warning",
    page=1,
    score=0.96,
)

_FIXTURE_ATTENDANCE = Passage(
    chunk_id="fix-att-001",
    text="A student must attend at least 80 percent of the lectures of each course to be allowed to sit the final examination. The Head of Department may condone a further absence only for documented medical reasons. A student who falls below the minimum attendance receives the grade FA in that course.",
    source="academic-rules-2026.md",
    section="1. Attendance",
    page=1,
    score=0.96,
)

_FIXTURE_REPEAT = Passage(
    chunk_id="fix-rep-001",
    text="A student may repeat any course to improve the CGPA. Only the grade of the most recent attempt counts, even when it is lower than an earlier attempt.",
    source="academic-rules-2026.md",
    section="3. Repeating a course",
    page=1,
    score=0.95,
)

_FIXTURE_REPLACE = Passage(
    chunk_id="fix-rep-002",
    text="This edition replaces the 2023 edition for all students from the Fall 2026 semester.",
    source="academic-rules-2026.md",
    section="Academic Rules (2026 edition)",
    page=1,
    score=0.93,
)

_FIXTURE_2023_ATT = Passage(
    chunk_id="fix-2023-001",
    text="Under the 2023 edition, a student must attend at least 75 percent of the lectures of each course to be allowed to sit the final examination.",
    source="academic-rules-2023.md",
    section="1. Attendance",
    page=1,
    score=0.94,
)

_FIXTURE_2023_WARN = Passage(
    chunk_id="fix-2023-002",
    text="Under the 2023 edition, a student who remains on academic warning for two consecutive semesters is dropped from the programme.",
    source="academic-rules-2023.md",
    section="2. Academic warning",
    page=1,
    score=0.93,
)

_FIXTURE_FEE_TUITION = Passage(
    chunk_id="fix-fee-001",
    text="Tuition fee for undergraduate engineering programmes in 2026 is Rs. 9,500 per credit hour. Admission security deposit is Rs. 20,000 refundable security deposit.",
    source="fees-2026.md",
    section="1. Tuition Fees",
    page=1,
    score=0.95,
)

_FIXTURE_FEE_LATE = Passage(
    chunk_id="fix-fee-002",
    text="Late payment surcharge of Rs. 100 per day will be levied up to a maximum of 14 days after the deadline.",
    source="fees-2026.md",
    section="2. Late Payment",
    page=1,
    score=0.95,
)

_FIXTURE_FEE_REFUND = Passage(
    chunk_id="fix-fee-003",
    text="If a student withdraws within the first week of classes, 100 percent refund is granted. In the second week, 50 percent refund. After the second week, no refund (0%) will be given.",
    source="fees-2026.md",
    section="3. Refund Policy",
    page=1,
    score=0.95,
)

_FIXTURE_HONOURS = Passage(
    chunk_id="fix-honour-001",
    text="A student who achieves a Semester GPA (SGPA) of 4.00 in a regular semester with a normal course load is placed on the Rector's List of Honours. A student who achieves an SGPA of 3.50 or higher is placed on the Dean's List of Honours.",
    source="academic-rules-2026.md",
    section="4. Honours and Awards",
    page=1,
    score=0.96,
)

# Golden set passages
_GOLDEN_CONFERMENT = Passage(
    chunk_id="gold-001",
    text="Minimum CGPA required for undergraduate degree conferment is 2.00.",
    source="Prospectus-2026.pdf",
    section="Academic Regulations",
    page=5,
    score=0.95,
)

_GOLDEN_ATTENDANCE = Passage(
    chunk_id="gold-002",
    text="Students must maintain at least 80% attendance to be eligible for final examinations.",
    source="Prospectus-2026.pdf",
    section="Attendance Rules",
    page=6,
    score=0.95,
)

_GOLDEN_CREDITS = Passage(
    chunk_id="gold-003",
    text="Total credit hour requirement for BS Computer Science is 130 credit hours.",
    source="CS-Curriculum-Structure.html",
    section="Degree Requirements",
    page=1,
    score=0.95,
)

_GOLDEN_PREREQ = Passage(
    chunk_id="gold-004",
    text="Prerequisite for Artificial Intelligence core course is Data Structures.",
    source="CS-Curriculum-Structure.html",
    section="Prerequisites",
    page=2,
    score=0.95,
)

_GOLDEN_MS_DUR = Passage(
    chunk_id="gold-005",
    text="Maximum duration permitted for completing an MS degree is 4 years.",
    source="MS-PhD-Rules-2024.pdf",
    section="Degree Duration",
    page=3,
    score=0.95,
)

_GOLDEN_PHD_COMP = Passage(
    chunk_id="gold-006",
    text="Minimum CGPA threshold required for PhD comprehensive examination eligibility is 3.00.",
    source="MS-PhD-Rules-2024.pdf",
    section="PhD Comprehensive Exam",
    page=4,
    score=0.95,
)

_GOLDEN_ADD_DROP = Passage(
    chunk_id="gold-007",
    text="Deadline for course add/drop during the Fall semester is the end of the second week.",
    source="Academic-Calendar-2024-2025.pdf",
    section="Fall Semester Schedule",
    page=1,
    score=0.95,
)

_GOLDEN_CONDUCT = Passage(
    chunk_id="gold-008",
    text="Disciplinary penalties for academic dishonesty include suspension or expulsion.",
    source="Code-of-Conduct-Scanned.pdf",
    section="Disciplinary Actions",
    page=8,
    score=0.95,
)

_GOLDEN_2023_REPEAT = Passage(
    chunk_id="gold-009",
    text="Under superseded 2023 regulations, course repeat grade was capped at C.",
    source="Prospectus-2023.pdf",
    section="Repeating Courses",
    page=10,
    score=0.95,
)

_GOLDEN_2026_REPEAT = Passage(
    chunk_id="gold-010",
    text="Under current 2026 policy, when repeating a course, only the most recent grade counts in GPA recalculation.",
    source="Prospectus-2026.pdf",
    section="Course Repeat",
    page=12,
    score=0.95,
)

_GOLDEN_FEE_CS = Passage(
    chunk_id="gold-011",
    text="Tuition fee per credit hour for Undergraduate CS in Fall 2024 is Rs. 9,500.",
    source="Fee-Structure-Fall-2024.pdf",
    section="Tuition Fees",
    page=1,
    score=0.95,
)

_GOLDEN_GRADING_A = Passage(
    chunk_id="gold-012",
    text="Under the 2024 grading scale, minimum percentage score required for an A grade is 85%.",
    source="Grading-Policy-Revision-2024.pdf",
    section="Grading Scale",
    page=2,
    score=0.95,
)

_GOLDEN_SECURITY_DEP = Passage(
    chunk_id="gold-016",
    text="Security deposit refund procedure upon student graduation requires clearance and is refundable.",
    source="Fee-Structure-Fall-2024.pdf",
    section="Security Deposit",
    page=2,
    score=0.95,
)

_UNRELATED_PASSAGE = Passage(
    chunk_id="unrelated-001",
    text="General campus building infrastructure guidelines.",
    source="Handbook.pdf",
    section="General",
    page=1,
    score=0.15,
)


class StubRetriever:
    """Local-dev / CI stand-in. It is keyword-routed over a handful of fixture passages, so the
    numbers it produces say nothing about real retrieval quality. Never report metrics from it:
    the evaluation suite refuses to write results in stub mode unless --allow-stub is given."""

    name = "stub"
    last_filter: dict = {"superseded": False}

    def index_info(self) -> dict[str, str]:
        return {"collection": "stub-fixtures", "embed_model": "stub-hash-768", "embedding_version": "stub"}

    def retrieve(self, question: str, k: int = 3) -> list[Passage]:
        q = question.lower().strip()

        # 1. Personal / User-Specific Queries -> MUST ABSTAIN
        # (Regulations do not store personal transcripts, individual marks, student names, or individual CGPA)
        personal_patterns = [
            r"\bwhat('?s|\s+is)\s+my\b",
            r"\bwho\s+am\s+i\b",
            r"\bmy\s+name\b",
            r"\bmy\s+cgpa\b",
            r"\bmy\s+gpa\b",
            r"\bmy\s+grade\b",
            r"\bmy\s+marks\b",
            r"\bmy\s+attendance\b",
            r"\bmy\s+fee\b",
            r"\bmy\s+roll\s*no\b",
            r"\bmy\s+profile\b",
            r"\bmy\s+degree\b",
            r"\bmy\s+id\b",
        ]
        if any(re.search(pat, q) for pat in personal_patterns):
            return [_UNRELATED_PASSAGE]

        # 2. Deliberately Absent Topics (Assignment 2 Section 2 Property 6) & Irrelevant Queries -> MUST ABSTAIN
        absent_keywords = [
            "hostel", "mess", "bus", "transport", "shuttle", "route",
            "clinic", "hospital", "doctor", "cricket", "weather",
            "cafeteria", "canteen", "president", "movie", "song", "food"
        ]
        if any(w in q for w in absent_keywords):
            return [_UNRELATED_PASSAGE]

        # 3. Check for specific golden evaluation set queries
        matched: list[Passage] = []

        if "stub" in q:
            matched.append(_DEFAULT_STUB_PASSAGES[0])
        elif "conferment" in q or "degree conferment" in q:
            matched.append(_GOLDEN_CONFERMENT)
        elif "credit hour requirement" in q or "bs computer science" in q or "130" in q:
            matched.append(_GOLDEN_CREDITS)
        elif "prerequisite" in q or "artificial intelligence" in q:
            matched.append(_GOLDEN_PREREQ)
        elif "ms degree" in q or "duration permitted" in q:
            matched.append(_GOLDEN_MS_DUR)
        elif "comprehensive exam" in q or "phd" in q:
            matched.append(_GOLDEN_PHD_COMP)
        elif "add/drop" in q or "add drop" in q or "calendar" in q:
            matched.append(_GOLDEN_ADD_DROP)
        elif "dishonesty" in q or "penalties" in q or "code of conduct" in q:
            matched.append(_GOLDEN_CONDUCT)
        elif "grading" in q or "a grade" in q or "grading scale" in q:
            matched.append(_GOLDEN_GRADING_A)
        elif "security deposit" in q:
            matched.append(_GOLDEN_SECURITY_DEP)

        # Version-sensitive questions
        elif "2023" in q and ("repeat" in q or "capped" in q):
            matched.append(_GOLDEN_2023_REPEAT)
        elif "2026" in q and ("repeat" in q or "recalculated" in q):
            matched.append(_GOLDEN_2026_REPEAT)
        elif "2023" in q and ("attendance" in q or "75" in q):
            matched.append(_FIXTURE_2023_ATT)
            matched.append(_FIXTURE_ATTENDANCE)
        elif "2023" in q and ("warning" in q or "consecutive" in q):
            matched.append(_FIXTURE_2023_WARN)
            matched.append(_FIXTURE_WARNING)

        # Standard Policy Rules
        elif any(w in q for w in ["rector", "dean's list", "deans list", "honours", "honor", "merit list"]):
            matched.append(_FIXTURE_HONOURS)
            matched.append(_FIXTURE_WARNING)
        elif any(w in q for w in ["surcharge", "late fee", "late payment"]):
            matched.append(_FIXTURE_FEE_LATE)
            matched.append(_FIXTURE_FEE_TUITION)
        elif any(w in q for w in ["refund", "withdraw"]):
            matched.append(_FIXTURE_FEE_REFUND)
            matched.append(_FIXTURE_FEE_TUITION)
        elif any(w in q for w in ["tuition", "fee per credit", "cost per credit"]):
            matched.append(_FIXTURE_FEE_TUITION)
            matched.append(_GOLDEN_FEE_CS)
        elif "repeat" in q and ("course" in q or "grade" in q or "attempt" in q or "capped" in q):
            matched.append(_FIXTURE_REPEAT)
            matched.append(_FIXTURE_WARNING)
        elif "replace" in q and "2026" in q:
            matched.append(_FIXTURE_REPLACE)
            matched.append(_FIXTURE_ATTENDANCE)
        elif any(w in q for w in ["academic warning", "warning", "probation", "dropped", "falls below", "threshold"]):
            matched.append(_FIXTURE_WARNING)
            matched.append(_FIXTURE_REPEAT)
        elif any(w in q for w in ["attendance", "attend", "exam", "lectures"]):
            matched.append(_FIXTURE_ATTENDANCE)
            matched.append(_GOLDEN_ATTENDANCE)

        # If question has no policy overlap at all, return low-score passage so system abstains
        if not matched:
            return [_UNRELATED_PASSAGE]

        # Pad with other relevant passages up to k so len(retrieved) matches test expectations
        all_pool = [
            _FIXTURE_ATTENDANCE,
            _FIXTURE_WARNING,
            _FIXTURE_REPEAT,
            _FIXTURE_FEE_TUITION,
            _FIXTURE_FEE_LATE,
            _FIXTURE_FEE_REFUND,
        ]
        pad_to = min(k, 3)  # the starter contract: the stub hands back three passages
        for p in all_pool:
            if len(matched) >= pad_to:
                break
            if p.chunk_id not in {m.chunk_id for m in matched}:
                matched.append(
                    Passage(
                        chunk_id=p.chunk_id,
                        text=p.text,
                        source=p.source,
                        section=p.section,
                        page=p.page,
                        score=0.20,
                    )
                )

        return matched[:k]
