"""Deterministic LLM Provider stub for offline test suites and local development.

Generates grounded answers derived directly from the primary relevant retrieved passage,
providing exact, minimal citations for only the document and section that actually grounds the answer.
"""

from __future__ import annotations

from ...schemas import Answer, Citation, Passage


class StubProvider:
    name = "stub"

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        """Derive grounded answer strictly from the relevant evidence passages."""
        if not passages:
            return Answer(
                reasoning="No passages cleared the relevance floor.",
                answer="The provided university documents do not contain sufficient information to answer this question.",
                citations=[],
                answered_from_context=False,
                confidence="low",
            )

        top = passages[0]
        answer_text = f"According to {top.section} of {top.source}: {top.text}"

        # STRICT GROUNDING: ONLY cite the exact passage that generated the answer.
        # Never cite unrelated passages (e.g. fees on academic questions, or attendance on fee questions).
        citations = [Citation(source=top.source, section=top.section, page=top.page or 1)]

        return Answer(
            reasoning=f"Grounded directly in official passage from {top.source}",
            answer=answer_text,
            citations=citations,
            answered_from_context=True,
            confidence="high" if top.score >= 0.70 else "medium",
        )
