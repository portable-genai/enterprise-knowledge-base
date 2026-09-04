"""Gen AI evaluation gate adapter : the A4-style promotion gate for system A2.

Backs the domain ``EvaluationGatePort`` with the **Gen AI evaluation service**, accessed
through ``vertexai.Client(project, location).evals``. Over a golden dataset it scores the
knowledge base on the metrics that matter for an ACL-aware governed RAG system: retrieval
recall, ACL correctness, citation accuracy and safety, and maps the result onto an
``EvalReport`` whose ``passed`` flag gates promotion in CI/CD.

The Vertex AI SDK import is lazy so the on-prem and test profiles import without it.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.models import EvalMetricResult, EvalReport

# Promotion thresholds (0..1). A metric passes when its score >= threshold; the report
# passes only when every metric passes. ACL correctness is the non-negotiable bar for a
# governed RAG store : the system must never return a document outside the caller's tags.
_THRESHOLDS: dict[str, float] = {
    "retrieval_recall": 0.80,
    "acl_correctness": 0.99,
    "citation_accuracy": 0.90,
    "safety": 0.99,
}


class GenAiEvalAdapter:
    """Run the Gen AI evaluation service and map results to a domain ``EvalReport``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None

    # ------------------------------------------------------------------ #
    # Lazy SDK plumbing
    # ------------------------------------------------------------------ #
    def _evals(self) -> Any:
        """Return (and cache) the ``evals`` surface of the Vertex AI client."""
        if self._client is None:
            import vertexai  # lazy

            # verify: https://cloud.google.com/vertex-ai/generative-ai/docs/models/evaluation
            self._client = vertexai.Client(
                project=self._settings.project_id,
                # MODEL location, not the compute region.
                location=self._settings.models.location,
            )
        return self._client.evals

    # ------------------------------------------------------------------ #
    # EvaluationGatePort
    # ------------------------------------------------------------------ #
    def evaluate(self, dataset_path: str) -> EvalReport:
        """Refuse a cloud-only verdict that cannot compute ACL and retrieval metrics.

        enterprise-knowledge-base deliberately binds its deterministic local evaluation gate in
        every profile. The
        managed service can score citation/safety, but cannot replace the repo-owned ACL and
        retrieval oracles; presenting its partial result as a promotion verdict is misleading.
        """
        del dataset_path
        raise RuntimeError(
            "GenAiEvalAdapter is evidence-only and cannot issue the enterprise-knowledge-base "
            "promotion verdict; "
            "use the repo-owned LocalOfflineEvalAdapter gate"
        )

    # ------------------------------------------------------------------ #
    # Metric construction + result mapping
    # ------------------------------------------------------------------ #
    def _metrics(self) -> list[Any]:
        """Build the metric objects from the Gen AI eval prebuilt metric library.

        Falls back to plain metric-name strings if the prebuilt ``Metric`` types are not
        importable in the installed SDK version. Recall / ACL correctness are computed by
        the offline scorer; the service evaluates the LLM-judged citation/safety metrics.
        """
        try:
            from vertexai import types as eval_types  # lazy
        except Exception:  # noqa: BLE001
            return list(_THRESHOLDS.keys())
        prebuilt = getattr(eval_types, "PrebuiltMetric", None)
        if prebuilt is None:
            return list(_THRESHOLDS.keys())
        names = {
            "citation_accuracy": "CITATION_ACCURACY",
            "safety": "SAFETY",
        }
        metrics: list[Any] = []
        for key, attr in names.items():
            metric = getattr(prebuilt, attr, None)
            metrics.append(metric if metric is not None else key)
        return metrics

    def _to_report(self, dataset_path: str, result: Any) -> EvalReport:
        """Map the eval service result onto domain ``EvalMetricResult`` rows."""
        scores = _extract_summary_scores(result)
        n_examples = _extract_n_examples(result)
        rows: list[EvalMetricResult] = []
        for metric, threshold in _THRESHOLDS.items():
            score = float(scores.get(metric, 0.0))
            rows.append(
                EvalMetricResult(
                    metric=metric,
                    score=score,
                    threshold=threshold,
                    passed=score >= threshold,
                )
            )
        return EvalReport(
            dataset=dataset_path,
            results=tuple(rows),
            n_examples=n_examples,
        )


# ---------------------------------------------------------------------- #
# Pure mapping helpers (no SDK types in signatures)
# ---------------------------------------------------------------------- #
def _extract_summary_scores(result: Any) -> dict[str, float]:
    """Normalise the eval result's summary metrics into a ``{metric: score}`` dict."""
    raw = getattr(result, "summary_metrics", None)
    if raw is None:
        raw = getattr(result, "metrics", None)
    if raw is None and isinstance(result, dict):
        raw = result.get("summary_metrics") or result.get("metrics")

    scores: dict[str, float] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            scores[_norm_metric(key)] = _coerce_score(value)
        return scores
    for entry in raw or []:
        name = getattr(entry, "name", None) or (
            entry.get("name") if isinstance(entry, dict) else None
        )
        if name is None:
            continue
        value = getattr(entry, "score", None) if not isinstance(entry, dict) else entry.get("score")
        if value is None:
            value = (
                getattr(entry, "mean_score", None)
                if not isinstance(entry, dict)
                else entry.get("mean_score")
            )
        scores[_norm_metric(name)] = _coerce_score(value)
    return scores


def _norm_metric(name: str) -> str:
    key = str(name).lower()
    for suffix in ("/mean", "_mean", "/score", "_score"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
    return key


def _coerce_score(value: Any) -> float:
    if isinstance(value, dict):
        value = value.get("mean") or value.get("score") or value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _extract_n_examples(result: Any) -> int:
    for attr in ("n_examples", "row_count", "num_examples"):
        value = getattr(result, attr, None)
        if value is None and isinstance(result, dict):
            value = result.get(attr)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    metrics_table = getattr(result, "metrics_table", None)
    if metrics_table is not None:
        try:
            return int(len(metrics_table))
        except TypeError:
            return 0
    return 0
