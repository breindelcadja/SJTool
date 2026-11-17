/*
  SJTool website. Plain JavaScript, no build step. Two pages share this file:
    index.html  onboarding (design option C) and sign-in
    home.html   the journalist's home page with their ranked pitches (placeholder design)

  It works with whichever backend it finds, in this order:
    1. Supabase, if config.js has its URL and key (supabase_schema.sql).
    2. The team's FastAPI server (solutions_backend/api.py + site_api.py), found
       automatically when that server is running the site (or set apiUrl in config.js).
    3. Demo mode: sample pitches from demo-data.json, sign-ups saved in this browser.

  Places are checked with OpenStreetMap (Photon) and publications with Wikidata,
  the same services onboarding.py uses.
*/
(() => {
  "use strict";

  const CFG = window.SJTOOL_CONFIG || {};
  const LIVE = Boolean(CFG.supabaseUrl && CFG.supabaseAnonKey);
  const PHOTON = CFG.photonUrl || "https://photon.komoot.io/api/";
  const WIKIDATA = CFG.wikidataUrl || "https://www.wikidata.org/w/api.php";
  const PROFILE_KEY = "sjtool_profile";

  const BEATS = ["Public health", "Health care", "Environment", "Climate", "Local government",
    "Politics", "Science", "Education", "Housing", "Criminal justice", "Immigration",
    "Business", "Technology", "Agriculture", "Transportation", "Other"];
  const WIDE_AREAS = { national: "National", nationwide: "National", international: "International",
    global: "Global", worldwide: "Global" };
  const FREELANCE = ["freelance", "freelancer", "independent", "self-employed"];
  const MEDIA_WORDS = ["newspaper", "magazine", "news", "broadcast", "radio", "television",
    "tv ", "journal", "publication", "outlet", "agency", "newsletter", "media", "website",
    "podcast", "network", "daily", "weekly", "periodical", "tabloid"];
  const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  const $ = (id) => document.getElementById(id);
  const state = { beats: [], area: null, publication: null, pitches: null };

  // ---------- Small helpers ----------
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const storage = {
    get() { try { return JSON.parse(localStorage.getItem(PROFILE_KEY)); } catch { return null; } },
    set(v) { try { localStorage.setItem(PROFILE_KEY, JSON.stringify(v)); } catch { /* private mode */ } },
  };
  function showError(id, message) {
    const node = $(id);
    if (!node) return;
    node.replaceChildren(...(Array.isArray(message) ? message : [message || ""]));
    node.hidden = !message;
  }
  async function getJSON(url, params) {
    const res = await fetch(`${url}?${new URLSearchParams(params)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  // ---------- Which backend? ----------
  const API_BASE = (CFG.apiUrl || "").replace(/\/$/, "");
  let modePromise = null;
  function mode() {
    if (!modePromise) modePromise = (async () => {
      if (LIVE) return "supabase";
      try {
        // The FastAPI server answers /health with {"ok": true}; GitHub Pages doesn't.
        const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
        if (res.ok && (await res.json()).ok === true) return "api";
      } catch { /* not there */ }
      return "demo";
    })();
    return modePromise;
  }

  async function api(path, options = {}) {
    const res = await fetch(`${API_BASE}${path}`, { ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      const detail = body && body.detail;
      const message = Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : detail;
      const err = new Error(message || `Server error ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return body;
  }

  // ---------- Supabase ----------
  async function supabase(path, options = {}) {
    const res = await fetch(`${CFG.supabaseUrl.replace(/\/$/, "")}/rest/v1/${path}`, {
      ...options,
      // New "publishable" keys (sb_publishable_...) go only in apikey; old "anon" keys
      // (long, starting eyJ) also go in Authorization. Both work.
      headers: { apikey: CFG.supabaseAnonKey,
        ...(CFG.supabaseAnonKey.startsWith("eyJ") ? { Authorization: `Bearer ${CFG.supabaseAnonKey}` } : {}),
        "Content-Type": "application/json", ...(options.headers || {}) },
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) throw new Error((body && (body.message || body.hint)) || `Server error ${res.status}`);
    return body;
  }

  async function loadPitches() {
    if (state.pitches) return state.pitches;
    const m = await mode();
    if (m === "api") {
      state.pitches = await api("/site/pitches");
    } else if (m === "supabase") {
      state.pitches = await supabase("pitches?select=pitch_id,headline,summary,solution,location,beat," +
        "usefulness_points,fact_checks(confidence,reason),pitch_sources(url,link_url,link_title)" +
        "&order=usefulness_points.desc.nullslast&limit=60");
    } else {
      const res = await fetch("demo-data.json");
      state.pitches = await res.json();
    }
    // Supabase returns the one-to-one fact_checks join as an object or a list.
    for (const p of state.pitches) {
      if (Array.isArray(p.fact_checks)) p.fact_checks = p.fact_checks[0] || null;
    }
    return state.pitches;
  }

  async function saveProfile(profile) {
    const m = await mode();
    if (m === "api") {
      const { id } = await api("/journalists", { method: "POST", body: JSON.stringify({
        name: profile.name, email: profile.email || null, beat: profile.beat,
        area: profile.area, publication: profile.publication,
        area_details: profile.area_details || {}, publication_details: profile.publication_details || {},
      }) });
      return id;
    }
    if (m === "supabase") {
      return supabase("rpc/save_profile", { method: "POST", body: JSON.stringify({
        p_name: profile.name, p_email: profile.email || null, p_beat: profile.beat,
        p_area: profile.area, p_publication: profile.publication,
        p_area_details: profile.area_details, p_publication_details: profile.publication_details,
      }) });
    }
    const old = storage.get();
    return old && old.email && old.email === profile.email ? old.id
      : `demo-${Math.random().toString(36).slice(2, 8)}`;
  }

  // Returns { pitches, profile } — pitches best fit first, each with a reason.
  async function getFeed(who) {
    const m = await mode();
    if (m === "api") {
      const data = await api(`/journalists/${encodeURIComponent(who)}/feed?limit=10`);
      return { pitches: data.pitches || [], profile: data.profile };
    }
    if (m === "supabase") {
      const rows = await supabase("rpc/get_feed", { method: "POST", body: JSON.stringify({ who, max_pitches: 10 }) });
      return { pitches: rows.map((r) => ({ ...r, confidence: r.fact_check_confidence })), profile: null };
    }
    return { pitches: [], profile: null };
  }

  // ---------- Pitch cards ----------
  const CONFIDENCE = { high: "High confidence", medium: "Medium confidence",
    low: "Low confidence", unchecked: "Not fact-checked yet" };

  function pitchCard(p, extra = {}) {
    const confidence = extra.confidence || (p.fact_checks && p.fact_checks.confidence) || "unchecked";
    // The FastAPI pipeline's ClaimBuster check gives a note instead of a confidence level.
    const label = confidence === "unchecked" && p.check_note ? p.check_note : (CONFIDENCE[confidence] || confidence);
    const sources = (p.pitch_sources || []).filter((s) => s && s.url);
    const location = (p.location || "").replace(/\s*\(.*?\)\s*/g, " ").trim();
    return el("article", { class: "pitch" },
      el("div", { class: "tags" },
        p.beat ? el("span", { class: `tag${/government|politics/i.test(p.beat) ? " alt" : ""}` }, p.beat) : null,
        location && !/^not stated/i.test(location) ? el("span", { class: "location" }, location) : null),
      el("h3", {}, p.headline),
      p.summary ? el("p", { class: "summary" }, p.summary) : null,
      extra.reason ? el("p", { class: "why" }, extra.reason) : null,
      el("div", { class: "meta" },
        el("span", { class: `badge ${confidence}` }, el("span", { class: "dot", "aria-hidden": "true" }), label),
        sources.length ? el("details", { class: "sources" },
          el("summary", {}, `${sources.length} source${sources.length === 1 ? "" : "s"}`),
          el("ul", {}, sources.map((s) => el("li", {},
            el("a", { href: s.url, target: "_blank", rel: "noopener" }, "Bluesky post"),
            s.link_url ? [" · ", el("a", { href: s.link_url, target: "_blank", rel: "noopener" },
              s.link_title || "Linked article")] : null)))) : null));
  }

  async function renderSamples() {
    const box = $("sample-pitches");
    try {
      const pitches = await loadPitches();
      box.replaceChildren(...(pitches.length
        ? pitches.slice(0, 3).map((p) => pitchCard(p))
        : [el("p", { class: "muted" }, "No pitches yet. Check back soon.")]));
    } catch (e) {
      box.replaceChildren(el("p", { class: "muted" }, "Couldn't load pitches right now."));
    }
  }

  // ---------- Beats ----------
  function renderBeats() {
    const box = $("beat-chips");
    // Pick as many beats as you like; click again to unpick.
    box.replaceChildren(...BEATS.map((beat) => el("button", {
      type: "button", class: "chip", "aria-pressed": String(state.beats.includes(beat)),
      onclick: () => {
        const on = state.beats.includes(beat);
        state.beats = on ? state.beats.filter((b) => b !== beat) : [...state.beats, beat];
        renderBeats();
        $("other-beat-wrap").hidden = !state.beats.includes("Other");
        if (beat === "Other" && !on) $("f-other-beat").focus();
        showError("err-beat", "");
      },
    }, beat)));
  }

  // ---------- Suggestion lists (area and publication) ----------
  function setupCombo({ input, list, search, onPick, hintId }) {
    let options = [];
    let active = -1;
    let lastQuery = "";

    function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; }
    function render(items, status) {
      options = items;
      active = -1;
      const rows = items.map((opt, i) => el("li", {
        role: "option", id: `${list.id}-${i}`, "aria-selected": "false",
        onmousedown: (e) => { e.preventDefault(); pick(i); },
      }, opt.label, opt.detail ? el("span", { class: "detail" }, opt.detail) : null));
      if (status) rows.push(el("li", { class: "status", role: "presentation" }, status));
      list.replaceChildren(...rows);
      list.hidden = rows.length === 0;
      input.setAttribute("aria-expanded", String(!list.hidden));
    }
    function pick(i) {
      const opt = options[i];
      if (!opt) return;
      input.value = opt.label;
      input.classList.add("verified");
      close();
      onPick(opt);
    }
    function highlight(i) {
      active = (i + options.length) % options.length;
      [...list.querySelectorAll("[role=option]")].forEach((li, j) => li.setAttribute("aria-selected", String(j === active)));
      input.setAttribute("aria-activedescendant", `${list.id}-${active}`);
    }
    const run = debounce(async () => {
      const q = input.value.trim();
      lastQuery = q;
      if (q.length < 2) { close(); return; }
      render([], "Searching…");
      try {
        const results = await search(q);
        if (q !== lastQuery) return; // a newer search is running
        render(results.items, results.items.length ? results.note : results.empty);
      } catch {
        if (q !== lastQuery) return;
        options = [];
        render([], "Couldn't check right now. You can still continue.");
        onPick({ label: q, offline: true });
      }
    }, 300);

    input.addEventListener("input", () => {
      input.classList.remove("verified");
      onPick(null);
      run();
    });
    input.addEventListener("keydown", (e) => {
      if (list.hidden || !options.length) return;
      if (e.key === "ArrowDown") { e.preventDefault(); highlight(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
      else if (e.key === "Enter" && active >= 0) { e.preventDefault(); pick(active); }
      else if (e.key === "Escape") close();
    });
    input.addEventListener("blur", () => setTimeout(close, 150));
    return { pickFirst: () => pick(0), get options() { return options; } };
  }

  async function searchPlaces(q) {
    const wide = WIDE_AREAS[q.toLowerCase()];
    const items = wide ? [{ label: wide, detail: "Your whole coverage area",
      details: { verified: true, source: "scope", type: q.toLowerCase() } }] : [];
    const data = await getJSON(PHOTON, { q, limit: 10, lang: "en" });
    const seen = new Set(items.map((i) => i.label));
    for (const f of data.features || []) {
      const p = f.properties || {};
      if (!["place", "boundary"].includes(p.osm_key)) continue; // skip shops and streets
      const parts = [];
      for (const part of [p.name, p.county, p.state, p.country]) if (part && !parts.includes(part)) parts.push(part);
      const label = parts.join(", ");
      if (!label || seen.has(label)) continue;
      seen.add(label);
      const [lon, lat] = (f.geometry && f.geometry.coordinates) || [null, null];
      items.push({ label, detail: p.osm_value ? p.osm_value.replace(/_/g, " ") : null, details: {
        verified: true, source: "openstreetmap", type: p.osm_value || null,
        country: p.country || null, lat, lon } });
    }
    const typo = items.length && !wide &&
      (items[0].label.split(",")[0].toLowerCase() !== q.split(",")[0].trim().toLowerCase());
    return { items: items.slice(0, 6), note: typo ? "Did you mean one of these?" : null,
      empty: `We couldn't find a place called "${q}". Check the spelling.` };
  }

  function isMedia(desc) {
    const d = `${(desc || "").toLowerCase()} `;
    return MEDIA_WORDS.some((w) => d.includes(w));
  }
  async function wikidataOutlets(q) {
    const data = await getJSON(WIKIDATA, { action: "wbsearchentities", search: q, language: "en",
      uselang: "en", type: "item", limit: 15, format: "json", origin: "*" });
    return (data.search || []).filter((r) => isMedia(r.description)).map((r) => ({
      label: r.label, detail: r.description,
      details: { verified: true, source: "wikidata", id: r.id } }));
  }
  async function websiteExists(text) {
    if (/\s/.test(text) || !text.includes(".")) return null;
    const url = /^https?:\/\//.test(text) ? text : `https://${text}`;
    let host;
    try { host = new URL(url).hostname.replace(/^www\./, ""); } catch { return null; }
    try {
      // The site's answer can't be read from here, but a reply at all means it exists.
      await fetch(url, { mode: "no-cors", cache: "no-store" });
      return host;
    } catch { return null; }
  }
  async function searchPublications(q) {
    if (FREELANCE.includes(q.toLowerCase())) {
      return { items: [{ label: "Freelance", details: { verified: true, source: "freelance" } }] };
    }
    const items = [];
    const site = await websiteExists(q);
    if (site) items.push({ label: site, detail: "Website found",
      details: { verified: true, source: "website", website: site } });
    let outlets = await wikidataOutlets(q);
    let note = null;
    if (!outlets.length) {
      const s = await getJSON(WIKIDATA, { action: "query", list: "search", srsearch: q,
        srinfo: "suggestion", srlimit: 1, format: "json", origin: "*" });
      const suggestion = s.query && s.query.searchinfo && s.query.searchinfo.suggestion;
      if (suggestion && suggestion.toLowerCase() !== q.toLowerCase()) {
        outlets = await wikidataOutlets(suggestion);
        if (outlets.length) note = "Did you mean one of these?";
      }
    }
    items.push(...outlets.slice(0, 5));
    if ("freelance".startsWith(q.toLowerCase())) items.push({ label: "Freelance", details: { verified: true, source: "freelance" } });
    return { items, note, empty: `We couldn't find a publication called "${q}". ` +
      "Check the spelling, or type its website (like dailynorthwestern.com)." };
  }

  // ---------- Onboarding form ----------
  function setupOnboarding() {
    renderBeats();
    const areaCombo = setupCombo({ input: $("f-area"), list: $("area-list"), search: searchPlaces,
      onPick: (opt) => { state.area = opt; if (opt) showError("err-area", ""); } });
    const pubCombo = setupCombo({ input: $("f-pub"), list: $("pub-list"), search: searchPublications,
      onPick: (opt) => { state.publication = opt; if (opt) showError("err-pub", ""); } });

    $("skip-email").addEventListener("click", () => {
      $("f-email").value = "";
      showError("err-email", "");
      $("submit-btn").focus();
    });

    // Fill in a saved profile, for editing.
    const saved = storage.get();
    if (saved) {
      $("f-name").value = saved.name || "";
      $("f-email").value = saved.email || "";
      if (saved.beat) {
        // Beats are saved as one comma-separated line, like "Public health, Climate".
        const parts = saved.beat.split(",").map((b) => b.trim()).filter(Boolean);
        const known = parts.filter((b) => BEATS.includes(b) && b !== "Other");
        const other = parts.filter((b) => !BEATS.includes(b));
        state.beats = other.length ? [...known, "Other"] : known;
        if (other.length) { $("f-other-beat").value = other.join(", "); $("other-beat-wrap").hidden = false; }
        renderBeats();
      }
      if (saved.area) { $("f-area").value = saved.area; state.area = { label: saved.area, details: saved.area_details || {} }; $("f-area").classList.add("verified"); }
      if (saved.publication) { $("f-pub").value = saved.publication; state.publication = { label: saved.publication, details: saved.publication_details || {} }; $("f-pub").classList.add("verified"); }
    }

    $("onboard-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      let ok = true;
      const name = $("f-name").value.trim();
      const email = $("f-email").value.trim().toLowerCase();
      const otherBeat = $("f-other-beat").value.trim();
      const pickedOther = state.beats.includes("Other");
      const beat = [...state.beats.filter((b) => b !== "Other"), ...(pickedOther && otherBeat ? [otherBeat] : [])].join(", ");

      const fail = (id, msg, inputId) => { ok = false; showError(id, msg); if (inputId) $(inputId).setAttribute("aria-invalid", "true"); };
      ["f-name", "f-email", "f-area", "f-pub"].forEach((id) => $(id).removeAttribute("aria-invalid"));
      showError("err-submit", "");

      if (!name) fail("err-name", "Add your name.", "f-name"); else showError("err-name", "");
      if (pickedOther && !otherBeat) fail("err-beat", "Type your other beat, or unpick Other.");
      else if (!beat) fail("err-beat", "Pick at least one beat.");
      else showError("err-beat", "");
      if (email && !EMAIL.test(email)) fail("err-email", "That doesn't look like an email. Fix it, or press Skip.", "f-email"); else showError("err-email", "");

      // Area and publication must be picked from the checked list.
      const needPick = (combo, value, picked, errId, inputId, what) => {
        if (picked) return;
        if (!value) return fail(errId, `Add the ${what}.`, inputId);
        const first = combo.options[0];
        if (first) {
          fail(errId, [`Pick a real ${what} from the list. Did you mean `,
            el("button", { type: "button", onclick: () => combo.pickFirst() }, first.label), "?"], inputId);
        } else {
          fail(errId, `We couldn't confirm that ${what}. Check the spelling and pick from the list.`, inputId);
        }
      };
      needPick(areaCombo, $("f-area").value.trim(), state.area, "err-area", "f-area", "place");
      needPick(pubCombo, $("f-pub").value.trim(), state.publication, "err-pub", "f-pub", "publication");
      if (!ok) { document.querySelector("[aria-invalid=true], .error:not([hidden])")?.scrollIntoView({ block: "center", behavior: "smooth" }); return; }

      const profile = {
        name, email, beat,
        area: state.area.label,
        area_details: state.area.offline ? { verified: false } : state.area.details,
        publication: state.publication.label,
        publication_details: state.publication.offline ? { verified: false } : state.publication.details,
      };
      const btn = $("submit-btn");
      btn.disabled = true; btn.textContent = "Saving…";
      try {
        profile.id = await saveProfile(profile);
        storage.set(profile);
        location.href = "home.html";
      } catch (err) {
        showError("err-submit", `Couldn't save your profile: ${err.message}`);
      } finally {
        btn.disabled = false; btn.textContent = "Rank my pitches";
      }
    });
  }

  // ---------- Home page (home.html) ----------
  // PLACEHOLDER DESIGN: swap home.html's markup for the team's Figma home page.
  // Keep the IDs used here (home-title, home-sub, home-beats, feed-notice,
  // feed-list) or change them here too, and the ranked pitches keep working.
  async function renderHome() {
    let profile = storage.get();
    if (!profile) { location.replace("index.html"); return; }
    const list = $("feed-list");
    const notice = $("feed-notice");
    const fill = (p) => {
      $("home-title").textContent = p && p.name ? `Your pitches, ${p.name.split(" ")[0]}` : "Your pitches";
      $("home-sub").textContent = p && p.area && p.publication ? `${p.area} · ${p.publication}` : "";
      $("home-beats").replaceChildren(...(p && p.beat ? p.beat.split(",").map((b) => b.trim()).filter(Boolean)
        .map((b) => el("span", { class: "tag" }, b)) : []));
    };
    fill(profile);
    list.replaceChildren(el("p", { class: "muted" }, "Loading your pitches…"));
    notice.hidden = true;

    const m = await mode();
    let feed = { pitches: [], profile: null };
    try {
      feed = await getFeed(profile.id || profile.email);
    } catch (err) {
      if (err.status !== 404) {
        list.replaceChildren(el("p", { class: "error" }, `Couldn't load pitches: ${err.message}`));
        return;
      }
      // A profile saved on another backend (or the demo) isn't on this server yet.
      notice.textContent = "We couldn't find your profile on this server. Edit your profile and save it again to get pitches ranked for you.";
      notice.hidden = false;
    }
    if (feed.profile) {
      profile = { ...profile, ...feed.profile };
      storage.set(profile);
      fill(profile);
    }
    if (feed.pitches.length) {
      list.replaceChildren(...feed.pitches.map((p) => pitchCard(p, { reason: p.reason, confidence: p.confidence })));
      return;
    }
    try {
      const all = await loadPitches();
      if (notice.hidden) {
        notice.textContent = m === "demo"
          ? "Demo mode doesn't personalize the order. Here's every sample pitch, most reportable first."
          : m === "supabase"
            ? "We're still ranking pitches for you. That happens each time your team runs the ranking step. Until then, here's every pitch, most reportable first."
            : "There are no pitches yet. Run the pipeline to make some.";
        notice.hidden = false;
      }
      const visible = all.filter((p) => !(p.fact_checks && p.fact_checks.confidence === "low"));
      list.replaceChildren(...visible.map((p) => pitchCard(p)));
    } catch (err) {
      list.replaceChildren(el("p", { class: "error" }, `Couldn't load pitches: ${err.message}`));
    }
  }

  // ---------- Sign in (index.html#signin) ----------
  function setupSignin() {
    $("signin-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const email = $("s-email").value.trim().toLowerCase();
      if (!EMAIL.test(email)) { showError("err-signin", "Enter the email you signed up with."); return; }
      showError("err-signin", "");
      const saved = storage.get();
      if (!saved || saved.email !== email) storage.set({ email });
      location.href = "home.html";
    });
  }

  function setupSignout() {
    const btn = $("sign-out");
    if (!btn) return;
    btn.addEventListener("click", () => {
      try { localStorage.removeItem(PROFILE_KEY); } catch { /* private mode */ }
      location.href = "index.html";
    });
  }

  // ---------- Onboarding page views ----------
  function route() {
    const view = (location.hash || "#onboard").slice(1);
    for (const v of ["onboard", "signin"]) $(`view-${v}`).hidden = v !== (view === "signin" ? "signin" : "onboard");
    $("nav-feed").hidden = !storage.get();
    window.scrollTo(0, 0);
  }

  document.addEventListener("DOMContentLoaded", async () => {
    const page = document.body.dataset.page;
    mode().then((m) => { const b = $("demo-banner"); if (b) b.hidden = m !== "demo"; });
    setupSignout();
    if (page === "home") {
      renderHome();
      return;
    }
    setupOnboarding();
    setupSignin();
    renderSamples();
    window.addEventListener("hashchange", route);
    route();
  });
})();
