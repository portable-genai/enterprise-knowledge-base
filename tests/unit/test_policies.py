"""Unit tests for the domain policies (SPEC §5) and the bank-owned policy bundle (B4).

* KbReviewPolicy.evaluate(confidence, grounding_tags) -> ReviewOutcome (P-06)
* FreshnessPolicy(ttl_days) -> .expires_at(fetched_at), .is_stale(record), .is_in_region
* KbPolicy.from_mapping(...) -> the `policy:` settings section, defaults == reference

SPEC §5 behaviour after the B3 closure: maker-checker is a FLOOR, so every grounded
answer requires review, and a hard signal (confidence below the floor, a sensitive ACL
grounding tag, an ungrounded or blocked answer) only raises the level from STANDARD to
ENHANCED. A freshness record is stale when its status is not FRESH or it is past the TTL
window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.conftest import load_policy

from enterprise_kb.domain.kernel import ReviewLevel
from enterprise_kb.domain.models import FreshnessRecord, FreshnessStatus
from enterprise_kb.domain.policy import (
    DEFAULT_ANSWER_CONFIDENCE_FLOOR,
    DEFAULT_CORPUS_TTL_DAYS,
    AnswerPolicy,
    KbPolicy,
)


# --------------------------------------------------------------------------- #
# KbReviewPolicy
# --------------------------------------------------------------------------- #
def test_low_confidence_answer_requires_review():
    policy = load_policy("KbReviewPolicy")()
    assert policy.requires_review(confidence=0.1, grounding_tags=set()) is True
    assert policy.is_escalated(confidence=0.1, grounding_tags=set()) is True


def test_sensitive_tag_forces_review_even_when_confident():
    policy = load_policy("KbReviewPolicy")()
    assert (
        policy.requires_review(confidence=0.95, grounding_tags={"classification:restricted"})
        is True
    )


def test_pii_tag_forces_review():
    policy = load_policy("KbReviewPolicy")()
    assert policy.requires_review(confidence=0.95, grounding_tags={"dataset:pii-customers"}) is True


def test_confident_non_sensitive_answer_still_requires_review():
    """B3: maker-checker is a floor. RED before the closure, which returned False here."""
    policy = load_policy("KbReviewPolicy")()
    outcome = policy.evaluate(
        confidence=0.95, grounding_tags={"dept:retail", "classification:internal"}
    )
    assert outcome.requires_human_review is True
    # ...but it is NOT escalated: the bar is standard review, not enhanced.
    assert outcome.level is ReviewLevel.STANDARD
    assert outcome.reasons == ()


def test_no_grounding_tags_and_high_confidence_still_requires_review():
    policy = load_policy("KbReviewPolicy")()
    outcome = policy.evaluate(confidence=0.9, grounding_tags=None)
    assert outcome.requires_human_review is True
    assert outcome.level is ReviewLevel.STANDARD


def test_hard_signals_escalate_and_record_their_reason():
    policy = load_policy("KbReviewPolicy")()
    low = policy.evaluate(confidence=0.1, grounding_tags=set())
    assert low.level is ReviewLevel.ENHANCED
    assert "confidence_below_floor" in low.reasons

    sensitive = policy.evaluate(confidence=0.95, grounding_tags={"classification:restricted"})
    assert sensitive.level is ReviewLevel.ENHANCED
    assert "sensitive_classification" in sensitive.reasons

    blocked = policy.evaluate(confidence=0.9, grounding_tags=set(), blocked=True)
    assert blocked.level is ReviewLevel.ENHANCED
    assert "guardrail_blocked" in blocked.reasons


# --------------------------------------------------------------------------- #
# B4 : bank-owned numbers come from config; defaults reproduce reference behavior
#      AND an override changes behavior.
# --------------------------------------------------------------------------- #
def test_policy_defaults_reproduce_the_reference_constants():
    policy = KbPolicy.from_mapping(None)
    assert policy.answer_confidence_floor == DEFAULT_ANSWER_CONFIDENCE_FLOOR
    assert policy.corpus_ttl_days == DEFAULT_CORPUS_TTL_DAYS
    assert policy.review_all_answers is True
    assert policy.empty_retrieval_raises is True
    review = load_policy("KbReviewPolicy").from_policy(policy)
    assert review == load_policy("KbReviewPolicy")()
    assert load_policy("FreshnessPolicy").from_policy(policy) == load_policy("FreshnessPolicy")()


def test_policy_override_changes_engine_behavior():
    """The same inputs must score differently once the config moves the number."""
    reference = load_policy("KbReviewPolicy").from_policy(KbPolicy.from_mapping(None))
    strict = load_policy("KbReviewPolicy").from_policy(
        KbPolicy.from_mapping({"review": {"answer_confidence_floor": 0.9}})
    )
    assert reference.evaluate(0.8, set()).level is ReviewLevel.STANDARD
    assert strict.evaluate(0.8, set()).level is ReviewLevel.ENHANCED


def test_sensitive_tag_set_is_config_owned():
    policy = KbPolicy.from_mapping({"review": {"sensitive_tags": ["dataset:board-pack"]}})
    engine = load_policy("KbReviewPolicy").from_policy(policy)
    assert engine.evaluate(0.95, {"dataset:board-pack"}).level is ReviewLevel.ENHANCED
    # the shipped tag is no longer sensitive once the bank replaces the list
    assert engine.evaluate(0.95, {"classification:restricted"}).level is ReviewLevel.STANDARD


def test_corpus_ttl_override_changes_expiry():
    policy = KbPolicy.from_mapping({"corpus": {"ttl_days": 30}})
    engine = load_policy("FreshnessPolicy").from_policy(policy)
    fetched = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    assert engine.expires_at(fetched) == fetched + timedelta(days=30)


def test_review_floor_can_be_configured_off_as_a_deliberate_deviation():
    policy = KbPolicy.from_mapping({"review": {"review_all_answers": False}})
    engine = load_policy("KbReviewPolicy").from_policy(policy)
    assert engine.evaluate(0.95, {"dept:retail"}).requires_human_review is False
    # a hard signal still forces review even with the floor off
    assert engine.evaluate(0.1, set()).requires_human_review is True


def test_answer_policy_defaults_and_override():
    assert AnswerPolicy() == KbPolicy.from_mapping(None).answer_policy()
    degraded = KbPolicy.from_mapping({"answer": {"empty_retrieval_raises": False}})
    assert degraded.answer_policy().empty_retrieval_raises is False


def test_citation_policy_defaults_and_override_change_resolution():
    """The anchor match floor is config (B4), and moving it moves the engine's answer."""
    from enterprise_kb.domain.anchors import resolve_anchor
    from enterprise_kb.domain.kernel import BoundingBox
    from enterprise_kb.domain.models import DocumentChunk
    from enterprise_kb.domain.policy import DEFAULT_ANCHOR_MATCH_FLOOR

    reference = KbPolicy.from_mapping(None).citation_policy()
    assert reference.anchor_match_floor == DEFAULT_ANCHOR_MATCH_FLOOR

    strict = KbPolicy.from_mapping({"citation": {"anchor_match_floor": 0.95}}).citation_policy()
    chunks = [
        DocumentChunk(
            document_id="d",
            ordinal=0,
            text="The bank completes a security review before onboarding.",
            page=1,
            anchor="p1#b0",
            bbox=BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=0.1),
        )
    ]
    claim = "The bank completes a security review of every regional subsidiary."
    assert resolve_anchor(claim, chunks, reference.anchor_match_floor) is not None
    assert resolve_anchor(claim, chunks, strict.anchor_match_floor) is None


def test_unparseable_anchor_floor_falls_back_to_the_reference():
    from enterprise_kb.domain.policy import DEFAULT_ANCHOR_MATCH_FLOOR

    policy = KbPolicy.from_mapping({"citation": {"anchor_match_floor": "not-a-number"}})
    assert policy.anchor_match_floor == DEFAULT_ANCHOR_MATCH_FLOOR


# --------------------------------------------------------------------------- #
# FreshnessPolicy : TTL + residency
# --------------------------------------------------------------------------- #
def _record(
    expires_at: datetime,
    status: FreshnessStatus = FreshnessStatus.FRESH,
    region: str = "asia-southeast1",
) -> FreshnessRecord:
    fetched = expires_at - timedelta(days=7)
    return FreshnessRecord(
        document_id="policy-cloud-onboarding-v3",
        residency_region=region,
        fetched_at=fetched,
        expires_at=expires_at,
        version="v3",
        status=status,
    )


def test_expires_at_is_ttl_days_after_fetch():
    policy = load_policy("FreshnessPolicy")(ttl_days=7)
    fetched = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    assert policy.expires_at(fetched) == fetched + timedelta(days=7)


def test_is_stale_true_for_expired_record():
    policy = load_policy("FreshnessPolicy")(ttl_days=7)
    expired = _record(datetime.now(UTC) - timedelta(days=1))
    assert policy.is_stale(expired) is True


def test_is_stale_false_for_fresh_record():
    policy = load_policy("FreshnessPolicy")(ttl_days=7)
    fresh = _record(datetime.now(UTC) + timedelta(days=3))
    assert policy.is_stale(fresh) is False


def test_is_stale_true_for_non_fresh_status():
    policy = load_policy("FreshnessPolicy")(ttl_days=7)
    failed = _record(datetime.now(UTC) + timedelta(days=3), status=FreshnessStatus.FAILED)
    assert policy.is_stale(failed) is True


def test_residency_check():
    policy = load_policy("FreshnessPolicy")(ttl_days=7, residency_region="asia-southeast1")
    in_region = _record(datetime.now(UTC) + timedelta(days=2))
    out_region = _record(datetime.now(UTC) + timedelta(days=2), region="us-central1")
    assert policy.is_in_region(in_region) is True
    assert policy.is_in_region(out_region) is False


def test_record_is_fresh_helper_consistent_with_policy():
    policy = load_policy("FreshnessPolicy")(ttl_days=7)
    fresh = _record(datetime.now(UTC) + timedelta(days=2))
    assert fresh.is_fresh() is True
    assert policy.is_stale(fresh) is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
