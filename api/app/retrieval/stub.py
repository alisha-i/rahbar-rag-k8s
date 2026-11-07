"""Retriever implementation for local execution and offline test suites.

Maintains realistic FAST-NUCES university regulation passages across academic,
fee, grading, and disciplinary policies, while strictly enforcing honest abstention
for out-of-corpus topics (section 3.2, 3.4).
"""

from __future__ import annotations

from ..schemas import Passage

_DEFAULT_STUB_PASSAGES = [
    Passage(
        chunk_id="stub-001",
        text="This is an official handbook passage. RAHBAR answers user queries grounded strictly in university documents.",
        source="stub-handbook.pdf",
        section="1.1 About the stub",
        page=1,
        score=0.91,
    ),
    Passage(
        chunk_id="stub-002",
        text="Undergraduate students must maintain regular academic standing and adhere to course prerequisites.",
        source="stub-handbook.pdf",
        section="1.2 What the stub returns",
        page=1,
        score=0.84,
    ),
    Passage(
        chunk_id="stub-003",
        text="Consult the registrar and academic council regulations for official policy revisions and degree conferment.",
        source="stub-handbook.pdf",
        section="1.3 Switching to Chroma",
        page=2,
        score=0.77,
    ),
]

_KNOWLEDGE_BASE = [
    # 1. Rector's List / Honours & Awards / CGPA
    Passage(
        chunk_id="honours-001",
        text="To be eligible for the Rector's List of Honours, an undergraduate student must achieve a semester GPA of 3.80 or higher with a full academic load (minimum 15 credit hours) and no grade below 'B'. For the Dean's List of Honours, a semester GPA of 3.50 to 3.79 is required under the same full course load conditions.",
        source="Prospectus-2026.pdf",
        section="3.4 Honours and Awards",
        page=22,
        score=0.95,
    ),
    # 2. Attendance Policy (80%)
    Passage(
        chunk_id="attend-001",
        text="Students are required to maintain a minimum of 80% attendance in lectures and practical sessions of each registered course. A student failing to meet the 80% attendance requirement will not be allowed to sit for the final examination and will be awarded an 'F' grade in the respective course.",
        source="Prospectus-2026.pdf",
        section="1.2 Attendance Rules",
        page=14,
        score=0.96,
    ),
    # 3. Fee Structure / Tuition per Credit Hour
    Passage(
        chunk_id="fee-001",
        text="Undergraduate Computer Science (BS CS) tuition fee for Fall 2024 is PKR 9,500 per credit hour. One-time admission fee is PKR 30,000, and refundable security deposit is PKR 20,000 payable at the time of admission.",
        source="Fee-Structure-Fall-2024.pdf",
        section="Tuition Fees",
        page=2,
        score=0.94,
    ),
    # 4. Academic Warning, Probation & Graduation CGPA
    Passage(
        chunk_id="warn-001",
        text="An undergraduate student is placed on Academic Warning if their semester GPA falls below 2.00. If the cumulative CGPA remains below 2.00 for two consecutive semesters, the student is placed on Academic Probation. The minimum CGPA required for undergraduate degree conferment is 2.00.",
        source="Prospectus-2026.pdf",
        section="2.1 Academic Warning and Probation",
        page=16,
        score=0.93,
    ),
    # 5. BS CS Curriculum / Credit Hours
    Passage(
        chunk_id="curriculum-001",
        text="The Bachelor of Science in Computer Science (BS CS) degree program requires completion of a total of 130 credit hours over 8 regular semesters, comprising core computing, general education, domain electives, and a 6-credit Final Year Design Project.",
        source="CS-Curriculum-Structure.html",
        section="Degree Requirements",
        page=1,
        score=0.92,
    ),
    # 6. Course Repeat Policy (2026 current vs 2023 superseded)
    Passage(
        chunk_id="repeat-001",
        text="Under the current 2026 academic regulations, when a student repeats a course, the most recent grade earned replaces the earlier grade in CGPA calculation. In the superseded 2023 regulations, repeated course grades were capped at 'C'.",
        source="Prospectus-2026.pdf",
        section="Course Repeat Policy",
        page=18,
        score=0.91,
    ),
    # 7. MS Degree Duration
    Passage(
        chunk_id="grad-001",
        text="The normal duration for completing an MS degree program is 2 years (4 semesters). The maximum allowable duration for completion of all MS degree requirements is 4 years. For PhD programs, comprehensive examination requires a minimum CGPA of 3.00.",
        source="MS-PhD-Rules-2024.pdf",
        section="Degree Duration",
        page=5,
        score=0.92,
    ),
    # 8. Student Code of Conduct & Disciplinary Action
    Passage(
        chunk_id="conduct-001",
        text="Acts of academic dishonesty including cheating, unauthorized collaboration, and plagiarism are referred to the Campus Disciplinary Committee. Disciplinary penalties range from cancellation of the examination to academic suspension or permanent expulsion from the university.",
        source="Code-of-Conduct-Scanned.pdf",
        section="Disciplinary Actions",
        page=4,
        score=0.90,
    ),
]


class StubRetriever:
    name = "stub"

    def retrieve(self, question: str, k: int) -> list[Passage]:
        q = question.lower()

        # Out-of-scope topics (Hostel / Mess / Transport) -> Low scores (< 0.50 floor) to trigger honest abstention
        if any(term in q for term in ["hostel", "mess", "bus", "transport", "route", "suburban"]):
            return [
                Passage(
                    chunk_id="unrelated-001",
                    text="General campus building infrastructure guidelines.",
                    source="Handbook.pdf",
                    section="General",
                    page=1,
                    score=0.20,  # Below 0.50 floor
                ),
                Passage(
                    chunk_id="unrelated-002",
                    text="Sports complex hours and cafeteria timings.",
                    source="Handbook.pdf",
                    section="Facilities",
                    page=2,
                    score=0.15,
                ),
                Passage(
                    chunk_id="unrelated-003",
                    text="Campus security registration details.",
                    source="Handbook.pdf",
                    section="Security",
                    page=3,
                    score=0.10,
                ),
            ]

        # Match specific policy inquiries
        matched: list[Passage] = []
        # Check personal queries first so they don't accidentally match "cgpa" or "gpa"
        personal_terms = ["my gpa", "mera gpa", "mera cgpa", "my cgpa", "my marks", "my roll", "my grade", "my attendance", "mera record"]
        is_personal = any(term in q for term in personal_terms)
        is_greeting = q.strip().rstrip("!?. ") in ["hi", "hii", "hiii", "hiiii", "hello", "hey", "salam", "assalam", "assalam o alaikum", "kese ho"]

        if not is_personal and not is_greeting:
            if any(w in q for w in ["rector", "dean", "honour", "honor", "award"]):
                matched.append(_KNOWLEDGE_BASE[0])
            if any(w in q for w in ["attendance", "exam", "sit in", "absent"]):
                matched.append(_KNOWLEDGE_BASE[1])
            if any(w in q for w in ["fee", "tuition", "cost", "per credit"]):
                matched.append(_KNOWLEDGE_BASE[2])
            if any(w in q for w in ["warning", "probation", "cgpa", "gpa"]):
                matched.append(_KNOWLEDGE_BASE[3])
            if any(w in q for w in ["credit hour", "curriculum", "130", "requirement"]):
                matched.append(_KNOWLEDGE_BASE[4])
            if any(w in q for w in ["repeat", "supersed", "2023", "2026"]):
                matched.append(_KNOWLEDGE_BASE[5])
            if any(w in q for w in ["ms", "phd", "duration"]):
                matched.append(_KNOWLEDGE_BASE[6])
            if any(w in q for w in ["conduct", "cheat", "plagiar", "discipline", "penalty"]):
                matched.append(_KNOWLEDGE_BASE[7])

        # Fill remaining slots up to k=3 so test suites expecting 3 passages pass
        for p in _DEFAULT_STUB_PASSAGES:
            if len(matched) >= k:
                break
            if p not in matched:
                matched.append(p)

        return [p.model_copy() for p in matched[:k]]
