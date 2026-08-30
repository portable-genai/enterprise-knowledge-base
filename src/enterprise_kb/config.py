"""Configuration and the adapter factory (dependency injection for the hexagon).

The factory reads ``config/settings.yaml`` (with ``${ENV_VAR}`` interpolation) and
binds each port to a concrete adapter by dotted path. Switching the whole system from
the GCP managed stack to an on-prem stack is a one-line change of ``profile`` : proof
of the ports-and-adapters / no-lock-in principle (P-02). Every adapter follows one
construction convention: ``Adapter(settings: Settings)``.

``KB_PROFILE`` is resolved here ONCE, by :func:`resolve_profile`, and an absent variable is
read as NO CHOICE rather than as consent to the ``local`` relaxations. This module is the only
one permitted to read it; ``tests/unit/test_profile_single_source.py`` fails the build if any
other module re-derives it with its own permissive default.
"""

from __future__ import annotations

import importlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from .domain.policy import KbPolicy
from .envread import ConfiguredEmptyError, read_env_setting, setting_or_default

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-(.*?))?\}")

_PROFILE_ENV = "KB_PROFILE"

#: Every profile the adapter table binds. The comparison against it is EXACT and
#: case-sensitive: ``Local`` selects none of the ``local`` relaxations but also none of the
#: restrictions, so normalising the case here would turn a typo into a silent choice.
RUNTIME_PROFILES = frozenset({"local", "gcp", "platform", "onprem"})

#: The profile string handed to every INTERNET-FACING posture decision when ``KB_PROFILE`` was
#: never set. Deliberately NOT a member of :data:`RUNTIME_PROFILES` and never reaching
#: :class:`Settings`: it exists so "no choice was made" is a distinct input to the security
#: layers rather than being indistinguishable from a chosen ``local``.
UNCONSENTED_PROFILE = "unconfigured"


class ResidencyError(ValueError):
    """Raised at app load when ``region`` is outside the residency allowlist (P-03)."""


class ProfileError(ValueError):
    """Raised at app load when the named profile is one nothing binds (including a typo)."""


def _interpolate(value: Any) -> Any:
    """Replace ``${VAR}`` / ``${VAR:-default}`` tokens in strings recursively."""
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            default = m.group(2) or ""
            setting = read_env_setting(name)
            if setting.has_value:
                return setting.value
            if setting.is_configured_empty:
                raise ConfiguredEmptyError(
                    f"{name} is set to an empty value, which names nothing. Unset it to "
                    f"select the documented optional/default value {default!r}, or give it "
                    "a non-empty value."
                )
            return default

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def _validate_profile(profile: str) -> str:
    """Fail closed on a profile string nothing binds, INCLUDING a capitalisation typo."""
    if profile not in RUNTIME_PROFILES:
        expected = ", ".join(sorted(RUNTIME_PROFILES))
        raise ProfileError(f"unknown {_PROFILE_ENV} {profile!r}; expected one of: {expected}")
    return profile


@dataclass(frozen=True, slots=True)
class ProfileChoice:
    """The ONE resolution of ``KB_PROFILE``, and what each consumer must key off.

    The two derived profile strings differ because the two kinds of decision fail closed in
    OPPOSITE directions, so a single "effective profile" string would harden one and weaken
    the other: a RELAXATION keyed off ``local`` must not be granted to an unconsented run,
    while the bind RESTRICTION treats ``local`` as the confined case and must be applied to it.
    """

    #: Which adapter family to bind. Absent consent this is still ``local`` (the SDK-free
    #: adapters), because the alternative would import cloud SDKs that are not installed.
    profile: str = "local"
    #: Was the profile named DELIBERATELY (``KB_PROFILE`` set, or ``profile:`` written into
    #: ``config/settings.yaml``) rather than merely inherited from the fallback?
    explicit: bool = True

    @property
    def exposure_profile(self) -> str:
        """The profile every *relaxation* keys off: CORS origins, HSTS, the S2S opening.

        These grant something extra to ``local``, so an unconsented run must NOT look like
        ``local``: it gets :data:`UNCONSENTED_PROFILE`, which is no origin's allowlist, HSTS
        on, and (via the commons S2S dependency) no zero-secret opening.
        """
        return self.profile if self.explicit else UNCONSENTED_PROFILE

    @property
    def bind_profile(self) -> str:
        """The profile the bind guard keys off, where ``local`` is the RESTRICTIVE case.

        ``resolve_bind_host`` confines ``local`` to loopback and lets fronted profiles take
        ``0.0.0.0``, so here an unconsented run must look like ``local`` and stay on loopback.
        """
        return self.profile if self.explicit else "local"

    @property
    def service_auth_configured(self) -> bool:
        """May S2S callers be authenticated at all, or is the decision unconfigured?

        False means no profile was chosen, so neither S2S scheme has been selected and the
        request cannot be authenticated. The API turns this into a 401 rather than letting the
        shared-secret path's loopback-dev opening apply (see ``api/security.py``).
        """
        return self.explicit


def resolve_profile(
    environ: Mapping[str, str] | None = None, *, configured: str | None = None
) -> ProfileChoice:
    """Read ``KB_PROFILE`` once, treating absent/blank as NO CHOICE rather than ``local``.

    Two sources, in order, and either one counts as a deliberate choice: the ``KB_PROFILE``
    environment variable, then a ``profile:`` key someone wrote into ``config/settings.yaml``.
    The shipped settings file interpolates ``${KB_PROFILE}`` with no ``:-local`` default
    precisely so an unset variable arrives here as blank rather than as a forged choice.

    A value that IS present is validated here, not later: ``api/app.py`` resolves the profile
    at import, so an unknown or mis-capitalised value becomes a BOOT failure rather than a
    first-request failure from an app that has already picked its CORS, HSTS and S2S postures
    from a string nothing binds.
    """
    env = os.environ if environ is None else environ
    env_raw = env.get(_PROFILE_ENV)
    if env_raw is not None and not env_raw.strip():
        raise ConfiguredEmptyError(
            f"{_PROFILE_ENV} is configured but empty; unset it for the confined "
            "unconfigured posture, or name an explicit runtime profile"
        )
    raw = (env_raw or "").strip() or (configured or "").strip()
    if raw:
        _validate_profile(raw)
    return ProfileChoice(profile=raw or "local", explicit=bool(raw))


#: The profiles whose runtime is a managed cloud, for :attr:`Settings.runtime`. ``onprem`` is
#: NOT one -- running on the adopter's own iron is its entire point, and "on GCP" is the one
#: sentence that deployment must never print at the top of a page.
_MANAGED_PROFILES: frozenset[str] = frozenset({"gcp", "platform"})


@dataclass(frozen=True)
class ModelSettings:
    #: The Vertex location the model client calls, NOT the compute region. Gemini 3
    #: serves the `us` and `eu` multi-regions only; `global` carries no residency
    #: guarantee. See models.location in config/settings.yaml.
    location: str = "us"
    reasoning: str = "gemini-3.5-flash"
    triage: str = "gemini-3.5-flash"  # shares required Singapore single-zone PT
    hard_reasoning: str = "gemini-3.5-flash"  # Preview : feature-flagged off by default
    use_hard_reasoning: bool = False


@dataclass(frozen=True)
class StorageSettings:
    # Governed projection only: already-redacted text.
    corpus_bucket: str = "enterprise-knowledge-base-corpus"  # CMEK-encrypted, single-region
    # Reviewed registry/ACL authority artifacts are isolated from pipeline writes.
    control_bucket: str = ""
    # Raw reviewed inputs are isolated from the governed projection and are read-only to the
    # pipeline. Local profiles do not need either bucket.
    raw_source_bucket: str = ""


@dataclass(frozen=True)
class AlloyDBSettings:
    instance_uri: str = ""  # projects/.../locations/.../clusters/.../instances/...
    database: str = "enterprise_kb"
    # Terraform creates one IAM database user per workload; deployment supplies the matching id.
    user: str = ""
    ip_type: str = "PRIVATE"
    table: str = "document_freshness"
    vector_table: str = "document_chunks"
    acl_table: str = "principal_acl_tags"


@dataclass(frozen=True)
class ModelArmorSettings:
    template_id: str = ""


@dataclass(frozen=True)
class LoggingSettings:
    log_name: str = "enterprise-knowledge-base-audit"
    bucket: str = "enterprise-knowledge-base-worm"
    retention_days: int = 2557  # ~7 years


@dataclass(frozen=True)
class AgentEngineSettings:
    resource_name: str = ""  # reasoningEngine resource id, set after deploy
    display_name: str = "enterprise-knowledge-base"


@dataclass(frozen=True)
class CorpusSettings:
    ttl_days: int = 7
    registry_path: str = "src/enterprise_kb/pipelines/sources/registry.yaml"


@dataclass(frozen=True)
class AclSyncSettings:
    bindings_uri: str = ""


@dataclass(frozen=True)
class IapSettings:
    """Reviewed verified-service-email to tenant mapping for IAP S2S principals."""

    service_tenants: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PiiSettings:
    """Jurisdiction selection for the shared `pii-kit` patterns (C4 / E2).

    ``jurisdictions`` are ISO-3166 alpha-2 codes. The SAME selection feeds the runtime
    redactor (the local regex adapter and the GCP DLP custom info types) and the eval
    ``pii_safety`` metric, so a fork that changes market cannot keep a green gate while
    its redactor stops masking the identifiers it actually handles.
    """

    jurisdictions: tuple[str, ...] = ("SG",)


@dataclass(frozen=True)
class ResidencySettings:
    """Deploy-time residency allowlist, validated at app load (D5, P-03).

    ``allowed_regions`` mirrors the Terraform ``allowed_regions`` variable, which is the
    same allowlist the ``gcp.resourceLocations`` Org Policy is generated from. Keeping
    the list in config (not in code) is what makes a second enterprise or region a
    tfvars/settings change rather than a repo fork; validating it at load is what stops a
    mis-set ``region`` from reaching a running service.
    """

    allowed_regions: tuple[str, ...] = ("asia-southeast1",)


@dataclass(frozen=True)
class LocalSettings:
    """Paths for the SDK-free ``local`` profile stores (SQLite FTS5 + append-only audit).

    Empty strings select the per-package default under ``~/.enterprise_kb/``; tests pass
    ``:memory:`` for ephemeral, deterministic stores. No Google Cloud here.
    """

    db_path: str = ""  # SQLite FTS5 retrieval index; "" => ~/.enterprise_kb/local.db
    audit_path: str = ""  # append-only audit store;   "" => ~/.enterprise_kb/audit.db
    ledger_path: str = ""  # freshness ledger;          "" => ~/.enterprise_kb/ledger.db


@dataclass(frozen=True)
class Settings:
    project_id: str = "your-gcp-project"
    region: str = "asia-southeast1"
    # gcp | local | platform | onprem. Which ADAPTER FAMILY to bind; absent any choice this
    # stays "local" because the alternative imports cloud SDKs that are not installed. Which
    # posture to serve under is a different question: read ``choice`` below, never this field.
    profile: str = "local"
    kms_key: str = ""  # projects/.../cryptoKeys/... (regional)
    grounding_enabled: bool = False
    models: ModelSettings = field(default_factory=ModelSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    alloydb: AlloyDBSettings = field(default_factory=AlloyDBSettings)
    model_armor: ModelArmorSettings = field(default_factory=ModelArmorSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    agent_engine: AgentEngineSettings = field(default_factory=AgentEngineSettings)
    corpus: CorpusSettings = field(default_factory=CorpusSettings)
    acl_sync: AclSyncSettings = field(default_factory=AclSyncSettings)
    iap: IapSettings = field(default_factory=IapSettings)
    local: LocalSettings = field(default_factory=LocalSettings)
    residency: ResidencySettings = field(default_factory=ResidencySettings)
    pii: PiiSettings = field(default_factory=PiiSettings)
    # Bank-owned policy numbers (B4): thresholds, cadences and the maker-checker floor.
    policy: KbPolicy = field(default_factory=KbPolicy)
    # port_name -> { profile -> "module.path:ClassName" }
    adapters: dict[str, dict[str, str]] = field(default_factory=dict)
    # Was the profile chosen DELIBERATELY, or merely inherited from the fallback? ``load``
    # sets this False when neither KB_PROFILE nor a settings-file ``profile:`` named one.
    # Direct construction is deliberate by definition (a caller named the profile in code),
    # so the default is True and every unit test keeps the posture it asks for.
    profile_explicit: bool = True

    @property
    def runtime(self) -> str:
        """Where this process is running, as the UI banner states it: ``gcp`` or ``local``.

        Derived from the profile, never sniffed from the environment. A console that read
        its runtime from ``window.location`` would be right until the deployment served
        through a proxy and wrong silently after that, so the service is the one asked.
        """
        return "gcp" if self.profile in _MANAGED_PROFILES else "local"

    @property
    def generator_model(self) -> str:
        """Which model answers, for the UI banner (org decision, 2026-08-30).

        Read off the LLM binding the container will actually build, not from a second
        field someone has to remember to update. A repo that rebinds ``llm`` for a profile
        changes what the banner says in the same edit, which is the only way the two stay
        true to each other: a settings string would be a claim ABOUT the binding rather
        than the binding.
        """
        binding = self.adapters.get("llm", {}).get(self.profile, "")
        _, _, class_name = binding.partition(":")
        if class_name == "GeminiLLMAdapter":
            return self.models.reasoning
        if class_name == "OnPremLLMAdapter":
            # The on-prem adapter is a fail-fast migration placeholder: it raises rather
            # than generating. Naming a model here would advertise one that never answers.
            return "onprem-not-implemented"
        return "deterministic-offline-stub"

    @property
    def choice(self) -> ProfileChoice:
        """The resolved profile, split into the relaxation and restriction views.

        Every posture decision reads one of :class:`ProfileChoice`'s members rather than
        :attr:`profile`, so an unset ``KB_PROFILE`` cannot be read as consent to ``local``.
        """
        return ProfileChoice(profile=self.profile, explicit=self.profile_explicit)

    @property
    def model_armor_host(self) -> str:
        """Regional Model Armor REP derived from the single selected runtime region."""
        return f"modelarmor.{self.region}.rep.googleapis.com"

    @staticmethod
    def load(path: str | os.PathLike[str] | None = None) -> Settings:
        configured_path = (
            str(path)
            if path is not None
            else setting_or_default("KB_SETTINGS", "config/settings.yaml")
        )
        path = Path(configured_path)
        raw = _interpolate(yaml.safe_load(path.read_text())) if path.exists() else {}
        raw = raw or {}
        nested: dict[str, Any] = {
            "models": ModelSettings(**(raw.pop("models", {}) or {})),
            "storage": StorageSettings(**(raw.pop("storage", {}) or {})),
            "alloydb": AlloyDBSettings(**(raw.pop("alloydb", {}) or {})),
            "model_armor": ModelArmorSettings(**(raw.pop("model_armor", {}) or {})),
            "logging": LoggingSettings(**(raw.pop("logging", {}) or {})),
            "agent_engine": AgentEngineSettings(**(raw.pop("agent_engine", {}) or {})),
            "corpus": CorpusSettings(**(raw.pop("corpus", {}) or {})),
            "acl_sync": AclSyncSettings(**(raw.pop("acl_sync", {}) or {})),
            "iap": _iap(raw.pop("iap", {}) or {}),
            "local": LocalSettings(**(raw.pop("local", {}) or {})),
            "residency": _residency(raw.pop("residency", {}) or {}),
            "pii": _pii(raw.pop("pii", {}) or {}),
        }
        # The corpus TTL and the region already have a home above; the policy bundle
        # borrows them rather than restating them (B4: one number, one home).
        nested["policy"] = KbPolicy.from_mapping(
            raw.pop("policy", {}) or {},
            corpus_ttl_days=nested["corpus"].ttl_days,
            residency_region=str(raw.get("region") or Settings.region),
        )
        choice = resolve_profile(configured=str(raw.pop("profile", "") or ""))
        # ``profile_explicit`` is DERIVED, never configured: a settings file that could set it
        # would be able to forge consent, which is the whole point of the resolution above.
        raw.pop("profile_explicit", None)
        known = {f for f in Settings.__dataclass_fields__ if f not in nested}
        flat: dict[str, Any] = {k: v for k, v in raw.items() if k in known}
        if "grounding_enabled" in flat:
            flat["grounding_enabled"] = _strict_bool(
                flat["grounding_enabled"], name="KB_GROUNDING_ENABLED"
            )
        settings = Settings(
            profile=choice.profile, profile_explicit=choice.explicit, **flat, **nested
        )
        _validate_residency(settings)
        return settings


def _csv_tuple(value: Any) -> tuple[str, ...]:
    """Accept either a YAML list or a comma-separated string (env interpolation)."""
    if isinstance(value, str):
        parts: list[str] = [p.strip() for p in value.split(",")]
    elif isinstance(value, list | tuple):
        parts = [str(p).strip() for p in value]
    else:
        return ()
    return tuple(p for p in parts if p)


def _strict_bool(value: Any, *, name: str) -> bool:
    """Accept only an actual bool or the exact strings true/false; never bool('false')."""
    if isinstance(value, bool):
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{name} must be exactly 'true' or 'false', got {value!r}")


def _iap(raw: dict[str, Any]) -> IapSettings:
    value = raw.get("service_tenants", {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("KB_IAP_SERVICE_TENANTS must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("KB_IAP_SERVICE_TENANTS must be a JSON object")
    pairs: list[tuple[str, str]] = []
    for email, tenant in value.items():
        clean_email = str(email).strip().lower()
        clean_tenant = str(tenant).strip()
        if not clean_email.endswith(".iam.gserviceaccount.com") or not clean_tenant:
            raise ValueError(
                "KB_IAP_SERVICE_TENANTS requires non-empty tenant values keyed by "
                "service-account email"
            )
        pairs.append((clean_email, clean_tenant))
    return IapSettings(service_tenants=tuple(sorted(pairs)))


def _pii(raw: dict[str, Any]) -> PiiSettings:
    """Parse the jurisdiction selection; an empty/absent value keeps the shipped default."""
    codes = tuple(c.upper() for c in _csv_tuple(raw.get("jurisdictions")))
    return PiiSettings(jurisdictions=codes or PiiSettings().jurisdictions)


def _residency(raw: dict[str, Any]) -> ResidencySettings:
    """Parse the residency allowlist; an empty/absent list keeps the shipped default."""
    cleaned = _csv_tuple(raw.get("allowed_regions"))
    return ResidencySettings(allowed_regions=cleaned or ResidencySettings().allowed_regions)


def _validate_residency(settings: Settings) -> None:
    """Fail fast at app load when the configured region is outside the allowlist (D5).

    The same allowlist is validated at ``terraform plan`` (``var.allowed_regions``), so a
    residency mistake is caught in the pipeline AND in the process, and neither check can
    silently pass while the other is edited.
    """
    allowed = settings.residency.allowed_regions
    if settings.region not in allowed:
        raise ResidencyError(
            f"region {settings.region!r} is not in the configured residency allowlist "
            f"{list(allowed)!r}; add it to residency.allowed_regions (and to the "
            f"Terraform allowed_regions variable) before deploying there (P-03)."
        )


def instantiate(dotted: str, settings: Settings) -> Any:
    """Import ``module.path:ClassName`` and construct it with ``settings``."""
    module_path, _, class_name = dotted.partition(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(settings)


class Container:
    """Lazily-built registry of port -> adapter instances.

    Adapters are imported only on first access so that, e.g., a unit test using the
    on-prem profile never needs the Google Cloud SDKs installed.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _bind(self, port_name: str) -> Any:
        binding = self.settings.adapters.get(port_name, {})
        dotted = binding.get(self.settings.profile)
        if not dotted:
            raise KeyError(
                f"No adapter configured for port '{port_name}' "
                f"under profile '{self.settings.profile}'."
            )
        return instantiate(dotted, self.settings)

    # One cached_property per port keeps wiring declarative and type-greppable.
    @cached_property
    def retrieval(self) -> Any:
        return self._bind("retrieval")

    @cached_property
    def access_control(self) -> Any:
        return self._bind("access_control")

    @cached_property
    def ingestion(self) -> Any:
        return self._bind("ingestion")

    @cached_property
    def citation_store(self) -> Any:
        return self._bind("citation_store")

    @cached_property
    def ledger(self) -> Any:
        return self._bind("ledger")

    @cached_property
    def llm(self) -> Any:
        return self._bind("llm")

    @cached_property
    def grounding(self) -> Any:
        return self._bind("grounding")

    @cached_property
    def guardrail(self) -> Any:
        return self._bind("guardrail")

    @cached_property
    def redaction(self) -> Any:
        return self._bind("redaction")

    @cached_property
    def agent_runtime(self) -> Any:
        return self._bind("agent_runtime")

    @cached_property
    def session(self) -> Any:
        return self._bind("session")

    @cached_property
    def memory(self) -> Any:
        return self._bind("memory")

    @cached_property
    def audit(self) -> Any:
        return self._bind("audit")

    @cached_property
    def tracer(self) -> Any:
        return self._bind("tracer")

    @cached_property
    def evaluation(self) -> Any:
        return self._bind("evaluation")

    @cached_property
    def registry(self) -> Any:
        return self._bind("registry")

    @cached_property
    def tool_catalog(self) -> Any:
        return self._bind("tool_catalog")

    @cached_property
    def identity(self) -> Any:
        return self._bind("identity")


def build_container(settings: Settings | None = None) -> Container:
    return Container(settings or Settings.load())


def identity_adapter_class(settings: Settings) -> type:
    """The identity adapter CLASS the active binding names, resolved WITHOUT constructing it.

    Reads the SAME ``settings.adapters['identity']`` table :meth:`Container._bind` binds from,
    so a deployment that rebound the identity port (the documented on-premises path: swap the
    placeholder for the client's own IdP adapter, ``docs/onprem-migration.md``) is answered
    about the adapter it ACTUALLY runs, not about the one the profile name suggests.

    Constructing is deliberately avoided: the seeded-persona adapter REFUSES to construct under
    an inherited profile, so a posture computed from an instance would be unobtainable in one of
    the exact cases it has to describe.
    """
    dotted = settings.adapters.get("identity", {}).get(settings.profile)
    if not dotted:
        raise KeyError(f"No identity adapter configured under profile {settings.profile!r}.")
    module_path, _, class_name = dotted.partition(":")
    resolved = getattr(importlib.import_module(module_path), class_name)
    if not isinstance(resolved, type):
        raise TypeError(f"identity binding {dotted!r} does not name a class")
    return resolved


def end_user_auth_kind(settings: Settings | None = None) -> str:
    """What the BOUND identity adapter declares it does for end-user authentication.

    This is the one question "are this service's end-user routes authenticated?" reduces to.
    See :mod:`enterprise_kb.ports.identity` for why neither the profile string on its own nor
    the presence of a service-to-service credential can answer it.

    Any failure to establish the answer resolves to
    :data:`~enterprise_kb.ports.identity.CLIENT_ASSERTED`. A guard that switches OFF because a
    lookup raised is a guard that fails open, and nothing is lost by failing closed here: the
    same failure surfaces loudly at the first request, when the container resolves the identical
    binding for real.
    """
    from .ports.identity import CLIENT_ASSERTED, declared_end_user_auth

    try:
        return declared_end_user_auth(identity_adapter_class(settings or Settings.load()))
    except Exception:  # noqa: BLE001 - a guard that fails open on a lookup error is no guard
        return CLIENT_ASSERTED
