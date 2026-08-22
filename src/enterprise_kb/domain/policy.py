"""Bank-owned policy numbers, parsed from config, never hard-coded in an engine (B4).

Every number a compliance function would want to tune lives here as a frozen dataclass
built from the ``policy:`` section of ``config/settings.yaml``. The defaults in this
module ARE the reference constants (SPEC 5), so a deployment that supplies no ``policy:``
section reproduces reference behavior exactly, and a deployment that overrides one number
changes behavior without a code change. ``tests/unit/test_policies.py`` proves both
directions.

The bundle is deliberately one object: the engines each take the narrow policy they need
(:class:`~enterprise_kb.domain.hitl.KbReviewPolicy`,
:class:`~enterprise_kb.domain.freshness_policy.FreshnessPolicy`, :class:`AnswerPolicy`)
via a ``from_policy`` constructor, so no engine reads settings itself and the domain
stays framework-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Reference constants. These values, and only these, are the shipped defaults.
# --------------------------------------------------------------------------- #
#: Answers below this self-critique confidence escalate to ENHANCED review (SPEC 5).
DEFAULT_ANSWER_CONFIDENCE_FLOOR: float = 0.6

#: ACL tag labels (matched case-insensitively, as substrings) that escalate review.
DEFAULT_SENSITIVE_TAGS: tuple[str, ...] = (
    "classification:restricted",
    "classification:confidential",
    "pii",
    "mnpi",  # material non-public information
    "legal-privileged",
)

#: Corpus freshness window in days before a document must be re-fetched and re-indexed.
DEFAULT_CORPUS_TTL_DAYS: int = 7

#: The single region the governed corpus must stay resident in (P-03).
DEFAULT_RESIDENCY_REGION: str = "asia-southeast1"

#: Minimum share of a claim's content vocabulary a layout block must contain before the
#: claim is anchored to it. Below the floor the citation keeps page-level provenance
#: rather than pointing a reviewer at a block that does not support the claim.
DEFAULT_ANCHOR_MATCH_FLOOR: float = 0.34

#: Reference: an answer with no ACL-admitted passage is a hard error, not a soft answer.
DEFAULT_EMPTY_RETRIEVAL_RAISES: bool = True

#: Reference: maker-checker is a floor; every synthesised answer is reviewed (P-06).
DEFAULT_REVIEW_ALL_ANSWERS: bool = True


def _as_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return fallback


@dataclass(frozen=True, slots=True)
class AnswerPolicy:
    """Grounding rules for the synthesised answer path (B2 / B3).

    Args:
        empty_retrieval_raises: when True (the reference default), the orchestrator
            RAISES :class:`~enterprise_kb.domain.errors.RetrievalEmptyError` rather than
            returning an ungrounded, caveated answer when no permitted passage was
            retrieved. An adopter that wants the degraded envelope instead sets this
            False in ``policy:``; the caller then owns the ungrounded case.
        review_all_answers: when True (the reference default), every synthesised answer
            carries ``requires_human_review=True`` and hard signals only raise the review
            level. Setting it False downgrades the floor to risk-based gating, which is a
            deliberate, configured deviation from the maker-checker baseline.
    """

    empty_retrieval_raises: bool = DEFAULT_EMPTY_RETRIEVAL_RAISES
    review_all_answers: bool = DEFAULT_REVIEW_ALL_ANSWERS


@dataclass(frozen=True, slots=True)
class CitationPolicy:
    """How precise a citation must be before it claims an anchor (B4).

    Args:
        anchor_match_floor: the fraction of a claim's content tokens the candidate layout
            block must contain for the claim to be anchored to it. Raising it makes
            anchors rarer but more certain; lowering it anchors more claims at the cost of
            precision. A bank tunes this in ``policy.citation``; the shipped default is
            the reference value.
    """

    anchor_match_floor: float = DEFAULT_ANCHOR_MATCH_FLOOR


@dataclass(frozen=True, slots=True)
class KbPolicy:
    """The full set of bank-owned numbers, parsed once from ``settings.policy``."""

    answer_confidence_floor: float = DEFAULT_ANSWER_CONFIDENCE_FLOOR
    sensitive_tags: tuple[str, ...] = field(default_factory=lambda: DEFAULT_SENSITIVE_TAGS)
    corpus_ttl_days: int = DEFAULT_CORPUS_TTL_DAYS
    residency_region: str = DEFAULT_RESIDENCY_REGION
    empty_retrieval_raises: bool = DEFAULT_EMPTY_RETRIEVAL_RAISES
    review_all_answers: bool = DEFAULT_REVIEW_ALL_ANSWERS
    anchor_match_floor: float = DEFAULT_ANCHOR_MATCH_FLOOR

    # -- parsing ----------------------------------------------------------- #
    @staticmethod
    def from_mapping(
        raw: dict[str, Any] | None,
        *,
        corpus_ttl_days: int | None = None,
        residency_region: str | None = None,
    ) -> KbPolicy:
        """Build the policy from the raw ``policy:`` mapping; unknown keys are ignored.

        An absent section, an empty section or an unparseable value falls back to the
        reference constant, so a malformed override can only be as strict as the
        shipped default, never looser by accident.

        ``corpus_ttl_days`` and ``residency_region`` carry the values that already have a
        home elsewhere in the settings file (``corpus.ttl_days`` and ``region``) so this
        bundle can hand them to the freshness engine without restating them: the policy
        section may override, but it does not duplicate.
        """
        raw = raw or {}
        review = raw.get("review") or {}
        corpus = raw.get("corpus") or {}
        answer = raw.get("answer") or {}
        citation = raw.get("citation") or {}

        tags = review.get("sensitive_tags")
        sensitive = (
            tuple(str(t) for t in tags if str(t).strip())
            if isinstance(tags, list | tuple) and tags
            else DEFAULT_SENSITIVE_TAGS
        )
        try:
            floor = float(review.get("answer_confidence_floor", DEFAULT_ANSWER_CONFIDENCE_FLOOR))
        except (TypeError, ValueError):
            floor = DEFAULT_ANSWER_CONFIDENCE_FLOOR
        ttl_fallback = corpus_ttl_days if corpus_ttl_days is not None else DEFAULT_CORPUS_TTL_DAYS
        try:
            ttl = int(corpus.get("ttl_days", ttl_fallback))
        except (TypeError, ValueError):
            ttl = ttl_fallback
        region = str(corpus.get("residency_region") or residency_region or DEFAULT_RESIDENCY_REGION)
        try:
            anchor_floor = float(citation.get("anchor_match_floor", DEFAULT_ANCHOR_MATCH_FLOOR))
        except (TypeError, ValueError):
            anchor_floor = DEFAULT_ANCHOR_MATCH_FLOOR
        return KbPolicy(
            answer_confidence_floor=max(0.0, min(1.0, floor)),
            sensitive_tags=sensitive,
            corpus_ttl_days=ttl,
            residency_region=region,
            empty_retrieval_raises=_as_bool(
                answer.get("empty_retrieval_raises"), DEFAULT_EMPTY_RETRIEVAL_RAISES
            ),
            review_all_answers=_as_bool(
                review.get("review_all_answers"), DEFAULT_REVIEW_ALL_ANSWERS
            ),
            anchor_match_floor=max(0.0, min(1.0, anchor_floor)),
        )

    # -- engine constructors ----------------------------------------------- #
    def citation_policy(self) -> CitationPolicy:
        return CitationPolicy(anchor_match_floor=self.anchor_match_floor)

    def answer_policy(self) -> AnswerPolicy:
        return AnswerPolicy(
            empty_retrieval_raises=self.empty_retrieval_raises,
            review_all_answers=self.review_all_answers,
        )
