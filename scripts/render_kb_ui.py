"""Render the audit-first A2 KB UI from the demo JSON into static HTML pages.

Server-side, dependency-free rendering of the governed-RAG result (one page per persona
plus an ACL matrix overview), faithful to the console palette (slate / regblue, panels,
citation chips, the human-review banner). Used to produce screenshots for slides with the
synthetic seed data.

    KB_PROFILE=local PYTHONPATH=src python scripts/render_kb_ui.py kb_demo.json out_dir/

Output: out_dir/kb-persona-retail.html, kb-persona-risk.html, kb-persona-unknown.html,
kb-acl-matrix.html
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

TAG_HINT = {
    "dept:retail": "Retail line of business",
    "dept:risk": "Risk function",
    "classification:internal": "Internal classification",
    "classification:restricted": "Restricted : sensitive, forces maker-checker review",
}

CSS = """
:root{--ink-50:#f5f7fa;--ink-100:#e6ebf2;--ink-200:#cdd7e4;--ink-300:#a6b6cc;
--ink-400:#7790ae;--ink-500:#546b8b;--ink-600:#3f5470;--ink-700:#33445b;--ink-800:#1f2a3a;
--regblue-50:#eef4ff;--regblue-100:#dbe7ff;--regblue-600:#2945d6;--regblue-700:#2237ad;
--ok:#059669;--ok-bg:#ecfdf5;--warn:#d97706;--warn-bg:#fffbeb;--deny:#b91c1c;--deny-bg:#fef2f2;
--shadow:0 1px 2px rgba(11,16,26,.06),0 8px 24px rgba(11,16,26,.06);}
*{box-sizing:border-box}
body{margin:0;background:var(--ink-50);color:var(--ink-800);
font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
font-size:14px;line-height:1.5;padding:24px 18px}
.wrap{max-width:880px;margin:0 auto}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
h1{font-size:18px;margin:0 0 2px}
.sub{color:var(--ink-500);font-size:13px;margin:0 0 16px}
.sub b{color:var(--ink-800)}
.steps{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 16px}
.step{font-size:12px;padding:4px 10px;border-radius:999px;border:1px solid var(--ink-200);background:#fff;color:var(--ink-500)}
.step.cur{background:var(--regblue-600);border-color:var(--regblue-600);color:#fff;font-weight:600}
.step.done{background:var(--regblue-50);border-color:var(--regblue-100);color:var(--regblue-700)}
.panel{border:1px solid var(--ink-200);background:#fff;border-radius:10px;box-shadow:var(--shadow);margin-bottom:16px}
.panel>h2{border-bottom:1px solid var(--ink-100);padding:11px 16px;margin:0;font-size:13px;font-weight:600;color:var(--ink-800);
display:flex;justify-content:space-between;align-items:center;gap:8px}
.panel>.body{padding:16px}
.statuspill{font-size:11px;font-weight:600;padding:2px 9px;border-radius:999px}
.review{border:1px solid #fcd34d;background:var(--warn-bg);color:#92400e;border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;margin-bottom:14px}
.denied{border:1px solid #fca5a5;background:var(--deny-bg);color:var(--deny);border-radius:8px;padding:8px 12px;font-size:12px;font-weight:600;margin-bottom:14px}
.who{display:flex;gap:18px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.who .fig{font-size:12px;color:var(--ink-500)}
.who .fig b{display:block;font-size:14px;color:var(--ink-800)}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.tag{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--regblue-700);background:var(--regblue-50);border:1px solid var(--regblue-100);border-radius:5px;padding:1px 6px}
.tag.restricted{color:var(--warn);background:var(--warn-bg);border-color:#fcd34d}
.q{font-size:14px;color:var(--ink-800);background:var(--ink-50);border:1px solid var(--ink-100);border-radius:8px;padding:9px 12px;margin:0}
/* answer + confidence */
.ans{font-size:14px;margin:0 0 10px}
.cov{max-width:280px}
.bar{height:12px;border-radius:8px;background:var(--ink-100);overflow:hidden;border:1px solid var(--ink-200)}
.bar>span{display:block;height:100%;background:linear-gradient(90deg,#3a60f0,#2945d6)}
.cov .lbl{display:flex;justify-content:space-between;font-size:12px;color:var(--ink-500);margin-bottom:4px}
.caveat{font-size:12px;color:var(--warn);margin-top:8px}
/* passages */
.passage{border:1px solid var(--ink-200);border-radius:9px;margin-bottom:10px;overflow:hidden}
.passage>.ph{display:flex;align-items:center;gap:10px;padding:9px 12px;background:var(--ink-50)}
.passage .txt{padding:10px 12px;font-size:13px;color:var(--ink-700);border-top:1px dashed var(--ink-200)}
.muted{color:var(--ink-400);font-size:12px}
.chip{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--ink-200);background:var(--ink-50);border-radius:6px;padding:1px 7px;font-size:11px;color:var(--ink-700)}
.chip .ty{font-family:ui-monospace,Menlo,monospace;font-weight:600;color:var(--regblue-700)}
.chip .sr{font-family:ui-monospace,Menlo,monospace}
.empty{color:var(--deny);font-size:13px;background:var(--deny-bg);border:1px solid #fca5a5;border-radius:8px;padding:10px 12px}
.okbox{color:var(--ok);font-size:13px;background:var(--ok-bg);border:1px solid #a7f3d0;border-radius:8px;padding:10px 12px}
.foot{font-size:11px;color:var(--ink-400);text-align:center;margin-top:18px}
/* matrix */
table.tl{width:100%;border-collapse:collapse;font-size:13px}
table.tl th,table.tl td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--ink-100);vertical-align:top}
table.tl th{font-size:11px;text-transform:uppercase;color:var(--ink-400);font-weight:600}
table.tl td.num{font-variant-numeric:tabular-nums}
.yes{color:var(--ok);font-weight:600}.no{color:var(--deny);font-weight:600}
"""


def esc(s: object) -> str:
    return html.escape(str(s if s is not None else ""))


def tag_chip(label: str) -> str:
    cls = "tag restricted" if "restricted" in label else "tag"
    return f'<span class="{cls}" title="{esc(TAG_HINT.get(label, label))}">{esc(label)}</span>'


def locator(c: dict) -> str:
    """Render a citation's locator, at anchor level when the claim resolved to a block.

    ``p.3 block p3#b2`` is what a reviewer opens; a citation that resolved to no block
    still shows its page, which is valid, coarser provenance rather than an error.
    """
    page = f" p.{c['page']}" if c.get("page") is not None else ""
    anchor = f" block {c['anchor']}" if c.get("anchor") else ""
    return f"{page}{anchor}"


def cite_chip(c: dict) -> str:
    box = c.get("bbox") or {}
    title = (
        "highlight box x0={x0} y0={y0} x1={x1} y1={y1}".format(**box)
        if box
        else "page-level citation (no block anchor resolved)"
    )
    return (
        f'<span class="chip" data-citation-id="{esc(c.get("document_id"))}" '
        f'data-anchor="{esc(c.get("anchor") or "")}" title="{esc(title)}">'
        f'<span class="ty">DOC</span>'
        f'<span class="sr">{esc(c.get("document_id"))}{esc(locator(c))}</span></span>'
    )


def steps_bar(cur_key: str) -> str:
    labels = [
        ("retail", "Retail RM"),
        ("risk", "Risk officer"),
        ("unknown", "Unentitled principal"),
    ]
    out = []
    for key, lab in labels:
        cls = "step cur" if key == cur_key else "step"
        out.append(f'<span class="{cls}">{esc(lab)}</span>')
    return '<div class="steps">' + "".join(out) + "</div>"


def page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title>"
        f"<style>{CSS}</style></head><body><div class='wrap'>{body}</div></body></html>"
    )


def render_passages(passages: list[dict]) -> str:
    if not passages:
        return (
            '<section class="panel"><h2>Retrieved passages (after ACL filter)</h2>'
            '<div class="body"><div class="empty">No passages admitted. The caller&rsquo;s '
            "principals resolve to no ACL tags, so the domain filter (P-09) drops everything "
            "(fail-closed). An unentitled principal sees nothing.</div></div></section>"
        )
    rows = []
    for p in passages:
        tags = " ".join(tag_chip(t) for t in p.get("acl_tags", []))
        rows.append(
            f'<div class="passage" data-document-id="{esc(p["document_id"])}">'
            f'<div class="ph">'
            f'<span class="chip" data-anchor="{esc(p.get("anchor") or "")}">'
            f'<span class="ty">DOC</span>'
            f'<span class="sr">{esc(p["document_id"])}{esc(locator(p))}</span></span>'
            f'<span class="muted">{esc(p["title"])} · {esc(p["version"])} · score {esc(p["score"])}</span>'
            f'<span class="tags" style="margin-left:auto">{tags}</span>'
            f'</div><div class="txt">{esc(p["text"])}</div></div>'
        )
    return (
        '<section class="panel"><h2>Retrieved passages '
        f'<span class="muted">{len(passages)} admitted</span></h2>'
        f'<div class="body">{"".join(rows)}</div></section>'
    )


def render_answer(ans: dict) -> str:
    conf = round(float(ans.get("confidence", 0)) * 100)
    cites = ans.get("citations", [])
    cite_html = " ".join(cite_chip(c) for c in cites) or '<span class="muted">no citations</span>'
    caveats = "".join(f'<div class="caveat">caveat: {esc(c)}</div>' for c in ans.get("caveats", []))
    return f"""
    <section class="panel"><h2>Grounded answer
      <span class="statuspill" style="background:var(--regblue-50);color:var(--regblue-700)">never beyond the retrieved set</span>
    </h2><div class="body">
      <p class="ans">{esc(ans.get("answer"))}</p>
      <div class="cov">
        <div class="lbl"><span>Confidence</span><span>{conf}%</span></div>
        <div class="bar"><span style="width:{conf}%"></span></div>
      </div>
      <div style="margin-top:10px">{cite_html}</div>
      {caveats}
    </div></section>"""


def render_persona(data: dict, persona: dict) -> str:
    ans = persona["answer"]
    principals = ", ".join(persona["principals"])
    banner = ""
    if not persona["passages"]:
        outcome = "fail-closed"
        banner = (
            '<div class="denied">ACCESS FAIL-CLOSED (P-09) : no ACL tags resolved for this '
            "caller, so no corpus content is visible.</div>"
        )
    elif ans.get("review_level") == "enhanced":
        outcome = "enhanced-review"
        reasons = ", ".join(ans.get("review_reasons", [])) or "hard signal"
        banner = (
            '<div class="review">ENHANCED REVIEW : maker-checker gate (P-06) raised by '
            f"{esc(reasons)}. A second qualified reviewer must sign off before this "
            "answer is relied upon.</div>"
        )
    else:
        outcome = "standard-review"
        banner = (
            '<div class="okbox">STANDARD REVIEW : every synthesised answer is queued for a '
            "checker (P-06 is a floor, not a switch). No hard signal raised the bar: "
            "grounded over the caller&rsquo;s permitted passages, confidence within "
            "tolerance, no sensitive-ACL source.</div>"
        )
    header = (
        f"<h1>Governed RAG : {esc(persona['label'].split(' :')[0])}</h1>"
        f"<p class='sub'>Region <b>{esc(data['region'])}</b> · profile <b>{esc(data['profile'])}</b> · "
        f"caller <b class='mono'>{esc(principals)}</b></p>" + steps_bar(persona["key"]) + banner
    )
    qpanel = (
        '<section class="panel"><h2>Query (redacted before it touches retrieval)</h2>'
        f'<div class="body"><p class="q">{esc(persona["query"])}</p>'
        f'<p class="muted" style="margin-top:8px">{esc(persona["label"])}</p></div></section>'
    )
    body = (
        f"<main data-persona='{esc(persona['key'])}' data-outcome='{outcome}'>"
        + header
        + qpanel
        + render_passages(persona["passages"])
        + render_answer(ans)
        + "<p class='foot'>Audit-first governed-RAG view · synthetic fictional corpus · "
        "deterministic local stack (SQLite FTS5 + offline LLM)</p></main>"
    )
    return page(f"A2 KB : {persona['key']}", body)


def render_matrix(data: dict) -> str:
    ing = data["ingest"]
    masked = ", ".join(f"{r['info_type']}x{r['count']}" for r in ing["redacted"]) or "none"
    rows = []
    for p in data["personas"]:
        ans = p["answer"]
        admitted = ", ".join(pp["document_id"] for pp in p["passages"]) or "—"
        gate = (
            '<span class="no">enhanced review</span>'
            if ans.get("review_level") == "enhanced"
            else '<span class="yes">standard review</span>'
        )
        visible = (
            '<span class="yes">yes</span>' if p["passages"] else '<span class="no">none</span>'
        )
        rows.append(
            f"<tr><td><b>{esc(p['label'].split(' :')[0])}</b><div class='muted'>"
            f"{esc(', '.join(p['principals']))}</div></td>"
            f"<td>{visible}</td>"
            f"<td>{esc(admitted)}</td>"
            f"<td class='num'>{len(ans['citations'])}</td>"
            f"<td>{gate}</td></tr>"
        )
    body = (
        f"<main data-demo-matrix='acl-visibility'><h1>ACL visibility matrix : "
        f"{esc(data['region'])}</h1>"
        "<p class='sub'>Same seeded corpus, three callers. The headline control is the "
        "<b>ACL filter in the domain</b> (P-09): who can see what, and when a sensitive "
        "source forces maker-checker review (P-06).</p>"
        + steps_bar("")
        + '<section class="panel"><h2>Live ingest : redact-before-index (P-04)</h2>'
        f'<div class="body"><span class="mono">{esc(ing["document_id"])}</span> · '
        f"{esc(ing['chunks'])} chunk(s) indexed · redacted before index: "
        f'<b>{esc(masked)}</b><div class="tags" style="margin-top:6px">'
        + " ".join(tag_chip(t) for t in ing["acl_tags"])
        + "</div></div></section>"
        '<section class="panel"><h2>Per-caller outcome</h2><div class="body">'
        "<table class='tl'><tr><th>Caller</th><th>Corpus visible</th>"
        "<th>Documents admitted</th><th>Citations</th><th>Gate</th></tr>"
        + "".join(rows)
        + "</table></div></section>"
        "<p class='foot'>Audit-first governed-RAG view · synthetic fictional corpus</p></main>"
    )
    return page("A2 KB : ACL matrix", body)


def main(json_path: str, out_dir: str) -> None:
    data = json.loads(Path(json_path).read_text())
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    written = []
    for persona in data["personas"]:
        p = out / f"kb-persona-{persona['key']}.html"
        p.write_text(render_persona(data, persona))
        written.append(p)
    matrix = out / "kb-acl-matrix.html"
    matrix.write_text(render_matrix(data))
    written.append(matrix)
    for p in written:
        print("wrote", p)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
