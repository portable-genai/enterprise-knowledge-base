"""``enterprise-knowledge-base`` : the Typer CLI for the A2 Enterprise Knowledge Base.

This is a thin presentation layer over the domain services. It owns no business logic:
every command builds the wiring from :func:`enterprise_kb.config.build_container` and the
factory functions in :mod:`enterprise_kb.api.deps`, invokes one domain service, and
pretty-prints the cited result.

Design constraints honoured here:

* **Import-safe.** Importing this module (the ``[project.scripts]`` entry point, or
  ``--help``) must never pull in FastAPI, uvicorn, or the Google Cloud SDKs. All of those
  are imported *lazily inside command bodies*, so the on-prem/test profile : which installs
  no Google Cloud SDK : can still load the CLI and run ``--help``.
* **Profile-aware.** ``KB_PROFILE`` selects the adapter stack. The ``onprem`` profile binds
  placeholder adapters that raise ``NotImplementedError`` from every method; when a command
  trips one of those, the CLI fails clearly (exit code 2) with a message that names the
  migration target rather than dumping a traceback.
* **Citations are first-class.** Search results and answers are printed with page-level
  provenance, because a knowledge-base answer that cannot be traced to a page is worthless.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime top level
    from ..config import Container
    from ..domain.models import Citation, GroundedAnswer, IngestResult, RetrievedPassage

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "A2 Enterprise Knowledge Base : ACL-aware governed RAG over the bank corpus, on "
        "the Gemini Enterprise Agent Platform (region asia-southeast1)."
    ),
)

corpus_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage the governed corpus (portable ingest + freshness/residency ledger).",
)
app.add_typer(corpus_app, name="corpus")

# Exit codes: 2 == the active profile cannot satisfy this command (e.g. on-prem stub);
# 1 == an unexpected adapter/runtime failure. 0 == success.
_PROFILE_EXIT = 2
_RUNTIME_EXIT = 1

_CLI_ACTOR = "cli:operator"


# --------------------------------------------------------------------------- #
# Wiring helpers (all imports lazy, so this module stays import-safe)
# --------------------------------------------------------------------------- #
def _container() -> Container:
    from ..config import build_container

    return build_container()


def _deps() -> Any:
    try:
        from ..api import deps  # type: ignore[attr-defined]
    except ImportError as exc:  # pragma: no cover - defensive wiring guard
        _fail(
            f"Service factories (enterprise_kb.api.deps) are unavailable: {exc}",
            code=_RUNTIME_EXIT,
        )
    return deps


def _fail(message: str, *, code: int = _RUNTIME_EXIT) -> Any:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _run(action: str, fn: Any) -> Any:
    """Execute ``fn`` and translate adapter failures into clean CLI errors."""
    from ..config import Settings

    profile = Settings.load().profile
    try:
        return fn()
    except NotImplementedError as exc:
        detail = str(exc) or "method not implemented"
        _fail(
            f"'{action}' is not available under profile '{profile}'. "
            f"This profile uses placeholder adapters (on-prem migration target): {detail}",
            code=_PROFILE_EXIT,
        )
    except KeyError as exc:
        _fail(f"'{action}' has no adapter wired for profile '{profile}': {exc}", code=_PROFILE_EXIT)
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI boundary: no tracebacks to operators
        _fail(f"'{action}' failed: {type(exc).__name__}: {exc}", code=_RUNTIME_EXIT)


# --------------------------------------------------------------------------- #
# Pretty-printing
# --------------------------------------------------------------------------- #
def _fmt_citation(c: Citation) -> str:
    page = f" p.{c.page}" if c.page is not None else ""
    version = f" {c.version}" if c.version and c.version != "unknown" else ""
    return f"[{c.document_id}{version}{page}] : {c.uri}"


def _echo_citations(citations: tuple[Citation, ...], indent: str = "  ") -> None:
    if not citations:
        typer.secho(f"{indent}(no citations)", fg=typer.colors.YELLOW)
        return
    typer.secho(f"{indent}Citations:", bold=True)
    for c in citations:
        typer.echo(f"{indent}  - {_fmt_citation(c)}")
        if c.snippet:
            snippet = c.snippet if len(c.snippet) <= 160 else c.snippet[:157] + "..."
            typer.secho(f'{indent}    "{snippet}"', fg=typer.colors.BRIGHT_BLACK)


def _echo_review_banner(requires_review: bool) -> None:
    if requires_review:
        typer.secho(
            "  [HUMAN REVIEW REQUIRED] maker-checker gate (P-06) : do not act on this "
            "output until a second qualified reviewer signs off.",
            fg=typer.colors.YELLOW,
            bold=True,
        )


def _print_passages(passages: list[RetrievedPassage]) -> None:
    typer.secho(f"Passages ({len(passages)})", bold=True, fg=typer.colors.GREEN)
    if not passages:
        typer.secho(
            "  (no permitted passages : check your access entitlements)", fg=typer.colors.YELLOW
        )
        return
    for p in passages:
        tags = ", ".join(t.label for t in p.acl_tags)
        typer.secho(f"  [{p.citation.document_id} p.{p.citation.page}] (acl: {tags})", bold=True)
        text = p.text if len(p.text) <= 240 else p.text[:237] + "..."
        typer.echo(f"    {text}")


def _print_answer(answer: GroundedAnswer) -> None:
    typer.secho("Answer", bold=True, fg=typer.colors.GREEN)
    typer.echo(f"  Q: {answer.query}")
    typer.echo("")
    typer.echo(f"  {answer.answer}")
    typer.echo("")
    typer.echo(f"  confidence: {answer.confidence:.2f}")
    _echo_review_banner(answer.requires_human_review)
    for caveat in answer.caveats:
        typer.secho(f"  caveat: {caveat}", fg=typer.colors.YELLOW)
    _echo_citations(answer.citations)


def _print_ingest(result: IngestResult) -> None:
    mark = (
        typer.style("ok", fg=typer.colors.GREEN)
        if result.ok
        else typer.style("FAILED", fg=typer.colors.RED)
    )
    typer.secho(f"Ingest [{mark}] {result.document_id}", bold=True)
    typer.echo(f"  chunks: {result.chunks}")
    if result.redaction_findings:
        masked = ", ".join(f"{f.info_type}x{f.count}" for f in result.redaction_findings)
        typer.secho(f"  redacted before index: {masked}", fg=typer.colors.CYAN)
    if result.detail:
        typer.echo(f"  detail: {result.detail}")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def _csv(value: str | None) -> tuple[str, ...]:
    """Split a comma-separated option value into a tuple of trimmed, non-empty parts."""
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


@app.command()
def search(
    query: str = typer.Argument(..., help="The query to search the corpus for."),
    principals: str | None = typer.Option(
        None, "--principals", "-p", help="Comma-separated caller principal ids to scope access to."
    ),
    tenant: str = typer.Option(
        # No short flag: ``-t`` is already ``--tags`` on ``ingest``, and one letter meaning
        # two things across sibling commands is how an operator reads a partition wrong.
        "",
        "--tenant",
        help="Tenant partition to read. Omitted reads the shared corpus only (fail-closed).",
    ),
    top_k: int = typer.Option(10, "--top-k", "-k", help="Maximum passages to return."),
) -> None:
    """Return ACL-filtered, page-cited passages for a query."""
    # An empty --tenant is a REFUSAL to guess, not a wildcard: the domain admits the shared
    # corpus for an unnamed tenant and nothing else. An operator who wants a tenant's own
    # documents names it. Before 2026-08-30 the empty value read across every partition,
    # which made the option's absence the widest possible read.

    def _do() -> list[RetrievedPassage]:
        svc = _deps().build_kb_service(_container())
        return svc.search(
            query,
            actor=_CLI_ACTOR,
            acl_principals=_csv(principals),
            tenant=tenant,
            top_k=top_k,
        )

    _print_passages(_run("search", _do))


@app.command()
def answer(
    query: str = typer.Argument(..., help="The query to answer."),
    principals: str | None = typer.Option(
        None, "--principals", "-p", help="Comma-separated caller principal ids to scope access to."
    ),
    tenant: str = typer.Option(
        # No short flag: ``-t`` is already ``--tags`` on ``ingest``, and one letter meaning
        # two things across sibling commands is how an operator reads a partition wrong.
        "",
        "--tenant",
        help="Tenant partition to read. Omitted reads the shared corpus only (fail-closed).",
    ),
) -> None:
    """Synthesise a cited, ACL-grounded answer over the caller's permitted passages."""
    # --tenant behaves exactly as it does for `search`: see the comment there.

    def _do() -> GroundedAnswer:
        svc = _deps().build_kb_service(_container())
        return svc.answer(
            query,
            actor=_CLI_ACTOR,
            acl_principals=_csv(principals),
            tenant=tenant,
        )

    _print_answer(_run("answer", _do))


@app.command()
def ingest(
    document_id: str = typer.Argument(..., help="Stable id / slug for the document."),
    title: str = typer.Argument(..., help="Human-readable document title."),
    path: str = typer.Argument(..., help="Path to the document file to ingest."),
    tags: str | None = typer.Option(
        None, "--tags", "-t", help="Comma-separated ACL tag labels gating retrieval."
    ),
    mime_type: str = typer.Option("application/pdf", "--mime", help="MIME type of the file."),
) -> None:
    """Redact, parse, index and record freshness for a document into the corpus."""
    from ..domain.models import AclTag, Document

    def _do() -> IngestResult:
        svc = _deps().build_ingestion_service(_container())
        document = Document(
            id=document_id,
            title=title,
            uri=str(path),
            acl_tags=tuple(AclTag(label=t) for t in _csv(tags)),
        )
        content = Path(path).read_bytes()
        return svc.ingest(document, content, mime_type, actor=_CLI_ACTOR)

    _print_ingest(_run("ingest", _do))


@corpus_app.command("refresh")
def corpus_refresh(
    full: bool = typer.Option(False, "--full", help="Re-ingest every document, not just expired."),
) -> None:
    """Re-fetch and re-index expired (or all) corpus documents."""

    def _do() -> Any:
        from ..pipelines import refresh_job

        return refresh_job.run(full=full)

    summary = _run("corpus refresh", _do)
    typer.secho("Corpus refresh", bold=True, fg=typer.colors.GREEN)
    typer.echo(
        f"  total={summary.total} ingested={summary.ingested} "
        f"skipped={summary.skipped} failed={summary.failed}"
    )


@corpus_app.command("status")
def corpus_status() -> None:
    """Show the freshness + residency ledger."""

    def _do() -> Any:
        return _container().ledger.all()

    records = _run("corpus status", _do)
    typer.secho("Corpus freshness ledger", bold=True, fg=typer.colors.GREEN)
    items = list(records) if records is not None else []
    if not items:
        typer.secho(
            "  (ledger is empty : run 'enterprise-knowledge-base corpus refresh')",
            fg=typer.colors.YELLOW,
        )
        return
    for rec in items:
        status_obj = getattr(rec, "status", "unknown")
        status = status_obj.value if hasattr(status_obj, "value") else str(status_obj)
        typer.echo(
            f"  {status.upper():<8} {rec.document_id}  region={rec.residency_region}  "
            f"expires={rec.expires_at}"
        )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address for the API server."),
    port: int = typer.Option(8082, help="TCP port for the API server."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change (dev only)."),
) -> None:
    """Run the FastAPI app (A2A card, REST endpoints) under uvicorn."""

    def _do() -> None:
        import uvicorn

        typer.secho(
            f"serving enterprise-knowledge-base on http://{host}:{port} "
            f"(profile={_profile_label()})",
            fg=typer.colors.GREEN,
        )
        uvicorn.run(
            "enterprise_kb.api.deps:create_app",
            factory=True,
            host=host,
            port=port,
            reload=reload,
        )

    _run("serve", _do)


@app.command()
def eval(  # noqa: A001 - "eval" is the documented command name
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        "-d",
        help="Path to the golden eval dataset (defaults to the bundled set).",
    ),
) -> None:
    """Run the offline eval gate (retrieval recall / ACL correctness / citation / safety)."""

    def _do() -> int:
        import subprocess
        import sys

        repo_root = Path(__file__).resolve().parents[3]
        script = repo_root / "eval" / "run_eval.py"
        if not script.exists():
            _fail(f"eval gate script not found at {script}", code=_RUNTIME_EXIT)
        cmd = [sys.executable, str(script)]
        if dataset:
            cmd += ["--dataset", dataset]
        typer.secho(f"running eval gate: {script}", fg=typer.colors.CYAN)
        completed = subprocess.run(cmd, check=False)  # noqa: S603 - trusted local script
        return completed.returncode

    code = _run("eval", _do)
    if code == 0:
        typer.secho("eval gate: PASS", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho("eval gate: FAIL", fg=typer.colors.RED, bold=True)
    raise typer.Exit(int(code))


def _profile_label() -> str:
    from ..config import Settings

    return Settings.load().profile


if __name__ == "__main__":  # pragma: no cover
    app()
