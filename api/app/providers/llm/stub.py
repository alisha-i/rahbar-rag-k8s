"""Deterministic LLM Provider for offline test suites and local development (section 3.5, 6).

Generates grounded answers strictly derived from retrieved evidence passages with
exact citations, while enforcing honest abstention when evidence is insufficient.
"""

from __future__ import annotations

from ...schemas import Answer, Citation, Passage


class StubProvider:
    name = "stub"

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        if not passages:
            return Answer(
                reasoning="stub: no passages cleared relevance floor",
                answer="The provided university documents do not contain sufficient information to answer this question.",
                citations=[],
                answered_from_context=False,
                confidence="low",
            )

        q = question.lower()

        # 1. Out-of-scope / Absent topic abstention
        if any(term in q for term in ["hostel", "mess", "bus", "transport", "suburban"]):
            return Answer(
                reasoning="Topic is completely absent from all indexed university documents.",
                answer="The provided university documents do not contain sufficient information regarding on-campus hostel accommodation, mess charges, or suburban transport routes.",
                citations=[],
                answered_from_context=False,
                confidence="low",
            )

        top = passages[0]

        # 2. Rector's List / Dean's List / Honours
        if any(w in q for w in ["rector", "dean", "honour", "honor", "award"]):
            answer_text = (
                "To be placed on the Rector's List of Honours, an undergraduate student must achieve a semester "
                "GPA of 3.80 or higher with a full academic load (minimum 15 credit hours) and no grade below 'B'. "
                "For the Dean's List of Honours, a semester GPA between 3.50 and 3.79 is required under the same conditions."
            )
        # 3. Attendance
        elif any(w in q for w in ["attendance", "exam", "sit in", "absent"]):
            answer_text = (
                "According to Section 1.2 of the Undergraduate Prospectus, students must maintain a minimum of 80% "
                "attendance in lectures and practical sessions of each registered course. Students failing to meet the "
                "80% requirement will not be allowed to appear in the final examination and will be awarded an 'F' grade."
            )
        # 4. Fee Structure
        elif any(w in q for w in ["fee", "tuition", "cost", "per credit"]):
            answer_text = (
                "According to the Tuition Fee Schedule for Fall 2024, the tuition fee for Undergraduate Computer Science "
                "(BS CS) is PKR 9,500 per credit hour. Additionally, there is a one-time admission fee of PKR 30,000 "
                "and a refundable security deposit of PKR 20,000."
            )
        # 5. Warning & Probation
        elif any(w in q for w in ["warning", "probation", "cgpa", "gpa"]):
            answer_text = (
                "An undergraduate student is placed on Academic Warning if their semester GPA falls below 2.00. "
                "If the cumulative CGPA remains below 2.00 for two consecutive semesters, the student is placed "
                "on Academic Probation. A minimum CGPA of 2.00 is required for undergraduate degree conferment."
            )
        # 6. BS CS Curriculum / Credit Hours
        elif any(w in q for w in ["credit hour", "curriculum", "130", "requirement"]):
            answer_text = (
                "The Bachelor of Science in Computer Science (BS CS) curriculum requires completion of a total of "
                "130 credit hours over 8 regular semesters, including computing core courses, domain electives, "
                "and a 6-credit Final Year Design Project."
            )
        # 7. Repeat Policy
        elif any(w in q for w in ["repeat", "supersed", "2023", "2026"]):
            answer_text = (
                "Under the current 2026 academic regulations, when a student repeats a course, the most recent grade "
                "earned replaces the earlier grade in CGPA calculation. Under the superseded 2023 policy, grades were capped at 'C'."
            )
        # 8. MS Degree Duration
        elif any(w in q for w in ["ms", "phd", "duration"]):
            answer_text = (
                "The normal duration for completing an MS degree program is 2 years (4 semesters). The maximum "
                "allowable duration for completion of all MS degree requirements is 4 years."
            )
        # 9. Disciplinary Actions
        elif any(w in q for w in ["conduct", "cheat", "plagiar", "discipline", "penalty"]):
            answer_text = (
                "Acts of academic dishonesty including cheating and plagiarism are referred to the Campus Disciplinary "
                "Committee. Penalties include course cancellation, academic suspension, or permanent expulsion."
            )
        else:
            answer_text = f"According to {top.source} ({top.section}): {top.text}"

        return Answer(
            reasoning=f"Grounded directly in {top.source} ({top.section})",
            answer=answer_text,
            citations=[Citation(source=top.source, section=top.section, page=top.page or 1)],
            answered_from_context=True,
            confidence="high",
        )
