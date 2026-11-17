"""
Turn a pipeline report into a self-contained HTML results page.

No template engine: plain string building, with every value escaped, so
students can read it top to bottom. The page shows ONLY the tiles
(Strong lead / Promising / Early signal). Myth and Flagged items are never
rendered here; they live in the audit log JSON.
"""

from __future__ import annotations

from html import escape

TIER_CLASS = {"Strong lead": "strong", "Promising": "promising", "Early signal": "early"}

CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1c1f23;--muted:#5d6570;--line:#dedbd2;
--strong:#0f6b4f;--strong-bg:#e2f3ec;--promising:#1f5fa8;--promising-bg:#e4eefa;
--early:#8a5a00;--early-bg:#fbf0d9;--bad:#a23b2a;--link:#1a56a6}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15171a;--card:#1e2126;
--ink:#eceae4;--muted:#a2a9b3;--line:#33383f;--strong:#6fd3b0;--strong-bg:#123a2e;
--promising:#8dbcf2;--promising-bg:#16304f;--early:#f0c36a;--early-bg:#3d2f10;--bad:#f09a8a;--link:#8dbcf2}}
:root[data-theme="dark"]{--bg:#15171a;--card:#1e2126;--ink:#eceae4;--muted:#a2a9b3;--line:#33383f;
--strong:#6fd3b0;--strong-bg:#123a2e;--promising:#8dbcf2;--promising-bg:#16304f;--early:#f0c36a;
--early-bg:#3d2f10;--bad:#f09a8a;--link:#8dbcf2}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:920px;margin:0 auto;padding:32px 16px 64px}
h1{font:600 28px/1.2 Georgia,"Times New Roman",serif;margin:0 0 6px}
.sub{color:var(--muted);margin:0 0 24px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;margin:0 0 18px}
.tile h2{font:600 20px/1.3 Georgia,"Times New Roman",serif;margin:10px 0 8px}
.badge{display:inline-block;font-size:13px;font-weight:600;padding:3px 10px;border-radius:999px}
.strong{color:var(--strong);background:var(--strong-bg)}
.promising{color:var(--promising);background:var(--promising-bg)}
.early{color:var(--early);background:var(--early-bg)}
.gap{margin-left:6px;color:var(--muted);font-size:13px}
.label{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);margin:16px 0 4px}
.why{margin:0}
blockquote{margin:0;padding:10px 14px;border-left:3px solid var(--line);background:var(--bg);border-radius:0 6px 6px 0;overflow-wrap:anywhere}
ul{margin:0;padding-left:20px}li{margin:4px 0;overflow-wrap:anywhere}
a{color:var(--link)}
.tag{font-size:12px;border:1px solid var(--line);border-radius:4px;padding:0 5px;margin-right:6px;color:var(--muted)}
.ok{color:var(--strong);font-weight:600}.no{color:var(--bad);font-weight:600}
.none{color:var(--bad);margin:0}
.meta{color:var(--muted);font-size:14px}
footer{color:var(--muted);font-size:14px;border-top:1px solid var(--line);margin-top:28px;padding-top:14px}
"""


def _link(url, text=None):
    if not url:
        return escape(text or "")
    if not str(url).startswith("http"):
        return escape(str(url))               # truncated display link: not clickable
    return f'<a href="{escape(url, quote=True)}" rel="noopener">{escape(text or url)}</a>'


def related_html(t: dict) -> str:
    r = t.get("related_research")
    if not r:
        return ""
    items = "".join(f"<li>{_link(w['url'], w['title'])}</li>" for w in r["works"])
    return (f'<div class="label">Related research</div><p class="meta">{escape(r["note"])}</p>'
            f"<ul>{items}</ul>")


def render_tile(t: dict) -> str:
    cls = TIER_CLASS.get(t["tier"], "early")
    ev = ""
    for e in t["evidence"]:
        ok = e["status"] == "verified"
        ev += (f'<li><span class="tag">{escape(e["label"] or "source")}</span>'
               f'{_link(e["link"])} <span class="{"ok" if ok else "no"}">{escape(e["status"])}</span>'
               f'<br><span class="meta">{escape(e["reason"])}</span></li>')
    if not ev:
        ev = "<li>The post cites no links.</li>"
    none = f'<p class="none">{escape(t["evidence_note"])}</p>' if t["evidence_note"] else ""
    notes = "".join(f"<li>{escape(n)}</li>" for n in t["notes"])
    bp, au = t["bluesky_post"], t["author"]
    gap = '<span class="gap">possible coverage gap</span>' if t["coverage_gap"] else ""
    others = (f' <span class="meta">+ {len(t["other_posts"])} more post(s) on this</span>'
              if t["other_posts"] else "")
    return f"""<article class="tile">
<span class="badge {cls}">{escape(t["tier"])}</span>{gap}
<h2>{escape(t["solution_summary"])}</h2>
<div class="label">Why this tier</div><p class="why">{escape(t["tier_reason"])}</p>
<div class="label">Evidence</div>{none}<ul>{ev}</ul>
<div class="label">Originating Bluesky post</div>
<blockquote>{escape(bp["text"])}</blockquote>
<p class="meta">{_link(bp["url"], "Open post")} · {escape(str(bp["created_at"])[:10])} ·
{bp["likes"]} likes, {bp["reposts"]} reposts, {bp["replies"]} replies{others}</p>
<div class="label">How to reach the author</div>
<p class="why">{_link(au["profile_url"], au["handle"])}<br><span class="meta">{escape(au["how_to_reach"])}</span></p>
{related_html(t)}
{f'<div class="label">Good to know</div><ul class="meta">{notes}</ul>' if notes else ''}
</article>"""


def render_page(report: dict, title: str = "Solution leads") -> str:
    tiles = "".join(render_tile(t) for t in report["tiles"]) or \
        "<p>No solutions passed verification for this issue.</p>"
    u, c = report["usage"], report["counts"]
    stop = (" Run stopped early: " + "; ".join(u["cap_events"]) + ".") if u["cap_events"] else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head>
<body><main>
<h1>Solution leads: {escape(report["issue"])}</h1>
<p class="sub">{c["tiles"]} lead(s) from Bluesky, checked against the sources they cite.
Engagement is treated as a weak signal, never as proof.</p>
{tiles}
<footer>Checked {c["solutions"]} solution idea(s) from {c["input_rows_after_dedupe"]} posts.
Page fetches this run: {u["page_fetches_this_run"]} (cap {u["caps"]["max_page_fetches_per_run"]});
paid calls: {u["paid_api_calls_this_run"]}; estimated cost ${u["estimated_cost_usd"]:.2f}.{escape(stop)}
Claim spotter: {escape(report["claim_spotter"]["name"])}.</footer>
</main></body></html>"""
