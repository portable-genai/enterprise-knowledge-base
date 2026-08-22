"""Human-in-the-loop (maker-checker) policy : General Principle P-06.

A2 is a *decision-support* knowledge base, never an autonomous approver. P-06 requires
that a human reviews a consequential output before it is relied upon, and a synthesised
answer over governed bank content IS consequential: the reader cannot see the passages
the model dropped, so "the model was confident" is not a control. This module centralises
that gate so every service applies identical rules and the thresholds are auditable in
one place.

Policy (SPEC 5), after the B3 closure:

* **Every** grounded answer sets ``requires_human_review=True``. The policy has no path
  that returns False while ``review_all_answers`` holds (the reference default), so a
  confident answer cannot escape the queue.
* Hard signals only ever RAISE the bar: confidence below ``answer_confidence_floor``, a
  sensitive ACL classification on a grounding passage, an ungrounded answer or a
  guardrail block escalate :class:`~enterprise_kb.domain.kernel.ReviewLevel` from
  STANDARD to ENHANCED, and the reason is recorded for the audit trail and the Hrz7
  review queue.
* The raw ``search`` path (passages only, no synthesis) makes no claim, so it is not
  maker-checker gated here; ACL filtering and audit are its controls.

Numbers come from the ``policy:`` settings section via
:class:`~enterprise_kb.domain.policy.KbPolicy` (B4), never from a constant in an engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .kernel import ReviewLevel, ReviewOutcome
from .policy import (
    DEFAULT_ANSWER_CONFIDENCE_FLOOR,
    DEFAULT_REVIEW_ALL_ANSWERS,
    DEFAULT_SENSITIVE_TAGS,
    KbPolicy,
)

#: Reason codes recorded on an escalation, so the audit trail says WHY, not just that.
REASON_LOW_CONFIDENCE = "confidence_below_floor"
REASON_SENSITIVE_TAG = "sensitive_classification"
REASON_UNGROUNDED = "no_grounding_passages"
REASON_BLOCKED = "guardrail_blocked"


@dataclass(frozen=True, slots=True)
class KbReviewPolicy:
    """Maker-checker gate (P-06) for grounded answers. Pure decision logic.

    Args:
        answer_confidence_floor: answers below this confidence escalate to ENHANCED
            review. Defaults to the reference 0.6 (SPEC 5).
        sensitive_tags: ACL tag labels (matched case-insensitively, as substrings)
            that escalate review when a grounding passage carries one.
        review_all_answers: the maker-checker floor. True (reference) means every
            synthesised answer requires review; an adopter may configure it off, which
            is a recorded deviation, not a default.
    """

    answer_confidence_floor: float = DEFAULT_ANSWER_CONFIDENCE_FLOOR
    sensitive_tags: frozenset[str] = field(
        default_factory=lambda: frozenset(DEFAULT_SENSITIVE_TAGS)
    )
    review_all_answers: bool = DEFAULT_REVIEW_ALL_ANSWERS

    @staticmethod
    def from_policy(policy: KbPolicy) -> KbReviewPolicy:
        """Build the engine from the bank-owned ``policy:`` numbers (B4)."""
        return KbReviewPolicy(
            answer_confidence_floor=policy.answer_confidence_floor,
            sensitive_tags=frozenset(policy.sensitive_tags),
            review_all_answers=policy.review_all_answers,
        )

    # ------------------------------------------------------------------ #
    # Decision
    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        confidence: float,
        grounding_tags: set[str] | None = None,
        *,
        grounded: bool = True,
        blocked: bool = False,
    ) -> ReviewOutcome:
        """Return the maker-checker outcome for one synthesised answer.

        ``requires_human_review`` is the floor and is True whenever
        ``review_all_answers`` holds; the hard signals below only raise ``level``.
        """
        reasons: list[str] = []
        if blocked:
            reasons.append(REASON_BLOCKED)
        if not grounded:
            reasons.append(REASON_UNGROUNDED)
        if confidence < self.answer_confidence_floor:
            reasons.append(REASON_LOW_CONFIDENCE)
        if self._has_sensitive_tag(grounding_tags or set()):
            reasons.append(REASON_SENSITIVE_TAG)

        level = ReviewLevel.ENHANCED if reasons else ReviewLevel.STANDARD
        requires = self.review_all_answers or bool(reasons)
        return ReviewOutcome(
            requires_human_review=requires,
            level=level,
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def requires_review(
        self,
        confidence: float,
        grounding_tags: set[str] | None = None,
    ) -> bool:
        """Whether a human must review before the output is relied upon (P-06 floor)."""
        return self.evaluate(confidence, grounding_tags).requires_human_review

    def is_escalated(
        self,
        confidence: float,
        grounding_tags: set[str] | None = None,
    ) -> bool:
        """Whether a hard signal raises the answer to ENHANCED review."""
        return self.evaluate(confidence, grounding_tags).escalated

    def _has_sensitive_tag(self, tags: set[str]) -> bool:
        lowered = {t.lower() for t in tags}
        for sensitive in self.sensitive_tags:
            s = sensitive.lower()
            if any(s in tag for tag in lowered):
                return True
        return False
