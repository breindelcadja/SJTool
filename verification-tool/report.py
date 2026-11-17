"""Turns results into a readable HTML page of tiles you can open in any browser."""

import html

MARK = {"met": "●", "partial": "◐", "not_found": "○"}
STATE_LABEL = {"met": "Met", "partial": "Partial", "not_found": "Not found"}
TIER = {
    "strong_lead": ("Strong lead", "#1F6B4A", "Worth pitching now."),
    "promising": ("Promising", "#1F4E8C", "Needs a closer look before pitching."),
    "early_signal": ("Early signal", "#5A5852", "Treat as a tip and look for proof."),
    "flagged": ("Flagged", "#9A2F1F", "Do not use without checking who is behind it."),
    "myth": ("Myth", "#5B2A86", "Popular idea that credible sources say does not hold up."),
}
RESULT_LABEL = {"supported": "Supported", "contradicted": "Contradicted",
                "unverified": "Not supported"}

CSS = """
body{margin:0;padding:32px;background:#F6F4EF;color:#1B1B1A;font-family:-apple-system,
BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;line-height:1.4}
h1{margin:0 0 4px;font-size:28px} .sub{margin:0 0 24px;color:#55524B}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:20px;
align-items:start}
.tile{background:#fff;border:1px solid #D6D2C8;border-radius:14px;padding:20px}
.badge{display:inline-block;color:#fff;border-radius:999px;padding:5px 14px;
font-weight:600;font-size:14px}
.tile h2{margin:12px 0 4px;font-size:18px} .action{font-weight:600;margin:0 0 12px}
.claim{background:#F6F4EF;border-radius:10px;padding:10px 12px;margin:12px 0}
.label{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;
color:#55524B} .result{border-top:1px solid #D6D2C8;margin-top:8px;padding-top:8px}
.pillars{border-top:1px solid #E6E2D8;margin-top:12px;padding-top:12px}
.pillar{margin:6px 0} .mark{display:inline-block;width:22px}
.meta{color:#55524B;font-size:14px;margin-top:10px}
.flags{margin-top:10px;border:1px solid #9A2F1F;background:#FBEDEA;border-radius:10px;
padding:8px 12px;font-size:14px}
.sources{margin-top:10px;font-size:14px} .sources a{color:#1F4E8C;word-break:break-all}
.dropped{color:#55524B;font-size:13px}
.drops{margin-top:28px;padding-top:16px;border-top:1px solid #D6D2C8}
.posts{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}
.posts th,.posts td{text-align:left;padding:4px 6px;border-top:1px solid #E6E2D8;vertical-align:top}
.legend{margin-top:20px;color:#55524B;font-size:14px}
"""


def _e(value):
    return html.escape(str(value if value is not None else ""))


def _tile(r):
    label, color, action = TIER[r["tier"]]
    claim = r["claim"]
    out = ['<div class="tile">']
    out.append('<span class="badge" style="background:%s">%s</span>' % (color, _e(label)))
    out.append("<h2>%s</h2>" % _e(r.get("solution_summary") or r["pitch_id"]))
    out.append('<p class="action">%s</p>' % _e(action))
    out.append('<div class="claim"><div class="label">Claim checked</div>')
    out.append("<div>%s</div>" % _e(claim["text"]))
    out.append('<div class="result"><strong>%s.</strong> %s</div></div>' % (
        _e(RESULT_LABEL[claim["result"]]), _e(claim["summary"])))
    out.append('<div class="pillars">')
    for name in ("response", "insight", "evidence", "limitations"):
        state = r["pillars"][name]["state"]
        out.append('<div class="pillar"><span class="mark">%s</span><strong>%s</strong> '
                   '&middot; %s</div>' % (MARK[state], _e(name.capitalize()),
                                            _e(STATE_LABEL[state])))
    out.append("</div>")
    s = r["source"]
    out.append('<div class="meta">%d independent poster(s) &middot; %d platform(s)</div>' % (
        s["independent_posters"], s["platforms"]))
    if s["flags"]:
        out.append('<div class="flags"><strong>Flags:</strong> %s</div>' % _e(", ".join(s["flags"])))
    seen, links = set(), []
    for c in r["citations"]:
        if c["url"] not in seen:
            seen.add(c["url"])
            links.append('<div><a href="%s">%s</a> (%s)</div>' % (
                _e(c["url"]), _e(c["url"]), _e(c["source_tier"])))
    if links:
        out.append('<div class="sources"><div class="label">Sources</div>%s</div>' % "".join(links))
    for d in r["dropped_citations"]:
        out.append('<div class="dropped">Dropped citation: %s (%s)</div>' % (
            _e(d["url"]), _e(d["reason"])))
    out.append('<div class="meta">%s &middot; checked %s</div></div>' % (
        _e(r["pitch_id"]), _e(r["checked_at"])))
    return "".join(out)


def _claim_card(r):
    s = r["source"]
    rows = "".join(
        "<tr><td>%.2f</td><td>%s</td><td>%s</td></tr>" % (
            p["claim_score"], _e(p["claim"]), _e(", ".join(p["flags"]) or "none"))
        for p in r.get("posts", []))
    return ('<div class="tile"><span class="badge" style="background:#5A5852">'
            "Check-worthy %.2f</span><h2>%s</h2>"
            '<div class="claim"><div class="label">Best claim</div><div>%s</div></div>'
            '<div class="meta">%d independent poster(s) &middot; %d platform(s) &middot; '
            "flags: %s</div>"
            '<div class="meta">Evidence search not run yet.</div>'
            '<table class="posts"><tr><th>Score</th><th>Sentence</th><th>Flags</th></tr>%s</table>'
            "</div>") % (r["claim"]["score"], _e(r.get("solution_summary") or r["pitch_id"]),
                         _e(r["claim"]["text"]), s["independent_posters"], s["platforms"],
                         _e(", ".join(s["flags"]) or "none"), rows)


def build_html(results, mode_note=""):
    tiles, drops, errors = [], [], []
    for r in results:
        if r.get("error"):
            errors.append("<li>%s: %s</li>" % (_e(r["pitch_id"]), _e(r["error"])))
        elif r.get("next_step") == "ready_for_evidence_search":
            tiles.append(_claim_card(r))
        elif r["tier"] is None:
            drops.append("<li>%s: no checkable claim (score %.2f). Best sentence: %s</li>" % (
                _e(r["pitch_id"]), r["claim"]["score"], _e(r["claim"]["text"] or "none")))
        else:
            tiles.append(_tile(r))
    body = ['<h1>Verification results</h1><p class="sub">%s</p>' % _e(mode_note)]
    body.append('<div class="grid">%s</div>' % "".join(tiles))
    if drops:
        body.append('<div class="drops"><div class="label">Dropped before verification</div>'
                    "<ul>%s</ul></div>" % "".join(drops))
    if errors:
        body.append('<div class="drops"><div class="label">Errors</div><ul>%s</ul></div>'
                    % "".join(errors))
    body.append('<div class="legend">%s Met &nbsp; %s Partial &nbsp; %s Not found '
                "(unchecked, not failed)</div>" % (MARK["met"], MARK["partial"], MARK["not_found"]))
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<title>Verification results</title><style>%s</style></head><body>%s</body></html>"
            % (CSS, "".join(body)))
