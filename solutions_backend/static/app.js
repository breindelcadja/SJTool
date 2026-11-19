const form = document.querySelector("#search-form");
const topicInput = document.querySelector("#topic");
const submitButton = document.querySelector(".search-button");
const maxResultsInput = document.querySelector("#max-results");
const stageLimitInput = document.querySelector("#stage-limit");
const statusPanel = document.querySelector("#run-status");
const statusTitle = document.querySelector("#status-title");
const statusDetail = document.querySelector("#status-detail");
const progressTrack = document.querySelector("#progress-track");
const progressFill = document.querySelector("#progress-fill");
const stats = document.querySelector("#run-stats");
const message = document.querySelector("#message");
const ideaList = document.querySelector("#idea-list");
const ideaCount = document.querySelector("#idea-count");

const stages = ["search", "classify", "verify", "pitch", "factcheck"];
const stageLabels = {
  search: "Searching Bluesky",
  classify: "Reviewing posts",
  verify: "Checking accounts and sources",
  pitch: "Drafting story ideas",
  factcheck: "Finding claims to check",
};
let requestGeneration = 0;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.error || `Request failed (${response.status})`);
  }
  return data;
}

function showMessage(text, isError = false) {
  message.hidden = !text;
  message.classList.toggle("error", isError);
  message.textContent = text;
}

function setBusy(busy) {
  submitButton.disabled = busy;
  submitButton.querySelector("span").textContent = busy ? "Searching" : "Search";
  form.setAttribute("aria-busy", String(busy));
}

function updateRunStatus(run) {
  statusPanel.hidden = false;
  const stage = run.stage;
  const counts = run.counts || {};
  statusTitle.textContent = run.status === "done"
    ? "Search complete"
    : run.status === "partial"
      ? "Search finished with some items to retry"
      : run.status === "failed"
        ? "Search stopped"
        : stageLabels[stage] || "Preparing your search";
  statusDetail.textContent = run.topic ? `“${run.topic}”` : "";

  const stageIndex = Math.max(0, stages.indexOf(stage));
  const progress = run.status === "done" ? 100
    : run.status === "partial" || run.status === "failed" ? 100
      : Math.round((stageIndex / stages.length) * 100);
  progressFill.style.width = `${progress}%`;
  progressTrack.classList.toggle("indeterminate", ["queued", "running"].includes(run.status));

  stats.replaceChildren();
  const values = [
    [counts.posts, "posts found"],
    [counts.classified, "reviewed"],
    [counts.solutions, "possible solutions"],
    [counts.hidden || undefined, "set aside by the verifier"],
    [counts.pitches, "drafts"],
  ];
  for (const [value, label] of values) {
    if (value !== undefined) stats.append(node("span", "", `${value} ${label}`));
  }
}

function appendSource(card, url, label, index) {
  if (!url) return;
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol)) return;
  } catch {
    return;
  }
  const link = node("a", "source-link", label || `Source ${index}`);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = label || url;
  card.append(link);
}

function excerpt(value, max = 180) {
  const text = (value || "").replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text;
}

function addDetailLine(parent, label, value) {
  if (!value) return;
  const p = node("p");
  const strong = node("strong", "", `${label}: `);
  p.append(strong, document.createTextNode(value));
  parent.append(p);
}

// The Bluesky verifier's tiers. Flagged and Myth posts never reach this page.
const TIER_CLASS = { "Strong lead": "strong", "Promising": "promising", "Early signal": "early" };

function addLinkLine(parent, label, links) {
  const usable = links.filter((l) => {
    try { return ["http:", "https:"].includes(new URL(l.url).protocol); } catch { return false; }
  });
  if (!usable.length) return;
  const p = node("p");
  p.append(node("strong", "", `${label}: `));
  usable.forEach((l, i) => {
    if (i) p.append(document.createTextNode(" · "));
    const a = node("a", "", l.text || l.url);
    a.href = l.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    p.append(a);
    if (l.note) p.append(document.createTextNode(` (${l.note})`));
  });
  parent.append(p);
}

function addVerification(detail, check) {
  if (!check) return;
  addDetailLine(detail, "Verifier", `${check.tier}. ${check.tier_reason || ""}`.trim());
  const tile = check.tile;
  if (!tile) return;
  addLinkLine(detail, "Sources checked", (tile.evidence || []).map((e) => ({
    url: e.link, text: e.label ? `${e.label} source` : "source", note: e.status,
  })));
  if (tile.related_research && tile.related_research.works) {
    addLinkLine(detail, "Related research (not checked against this claim)",
      tile.related_research.works.map((w) => ({ url: w.url, text: w.title })));
  }
  if (tile.author) addDetailLine(detail, "How to reach the author", tile.author.how_to_reach);
  if (Array.isArray(tile.notes) && tile.notes.length) addDetailLine(detail, "Notes", tile.notes.join(" "));
}

async function loadPitchDetails(runId, pitchId, detail) {
  if (!pitchId) {
    detail.textContent = "A draft pitch is not available for this lead yet.";
    return;
  }
  detail.textContent = "Loading pitch details…";
  try {
    const item = await api(`/runs/${runId}/pitches/${encodeURIComponent(pitchId)}`);
    const pitch = item.pitch || {};
    detail.replaceChildren();
    addDetailLine(detail, "Problem", pitch.problem);
    addDetailLine(detail, "Response", pitch.response);
    addDetailLine(detail, "Solution to investigate", pitch.solution);
    addDetailLine(detail, "Why now", pitch.why_now);
    addDetailLine(detail, "Questions", Array.isArray(pitch.questions) ? pitch.questions.join(" · ") : "");
    addDetailLine(detail, "People to contact", Array.isArray(pitch.people_to_contact) ? pitch.people_to_contact.join(" · ") : "");
    addDetailLine(detail, "Data that would show if it works", Array.isArray(pitch.data_needed) ? pitch.data_needed.join(" · ") : "");
    addDetailLine(detail, "Claims to verify", Array.isArray(pitch.to_verify) ? pitch.to_verify.join(" · ") : "");
    addVerification(detail, item.verification);
    if (item.factcheck_summary) {
      const summary = item.factcheck_summary;
      addDetailLine(detail, "ClaimBuster", `${summary.check_worthy} of ${summary.claims} claims flagged as worth checking. This is not a truth verdict.`);
    }
    if (!detail.childElementCount) detail.textContent = "No additional pitch details were returned.";
  } catch (error) {
    detail.replaceChildren(node("p", "detail-error", `Could not load pitch details: ${error.message}`));
  }
}

function makeCard(post, pitch, runId, index) {
  const card = node("article", "idea-card");
  const header = node("div", "idea-card-header");
  const title = pitch?.headline || post.link_title || "A solution to explore";
  header.append(node("h3", "idea-title", title));
  const tier = pitch?.tier || post.tier;
  if (TIER_CLASS[tier]) {
    const badge = node("span", `tier-badge ${TIER_CLASS[tier]}`, tier);
    badge.title = "Bluesky verifier's rating, from the accounts and the sources they cite";
    header.append(badge);
  }

  const confidence = ["high", "medium", "low"].includes(post.confidence)
    ? post.confidence : "low";
  const dot = node("span", `confidence-dot ${confidence}`);
  dot.title = `AI confidence: ${confidence}`;
  dot.setAttribute("aria-label", `AI confidence: ${confidence}`);
  header.append(dot);
  card.append(header);

  const summary = pitch?.summary || post.link_description || post.text || post.reason || "Open this lead to review its details.";
  card.append(node("p", "idea-summary", excerpt(summary, 170)));

  const sourceList = node("div", "source-list");
  const sourceUrls = pitch?.source_urls?.length ? pitch.source_urls : [post.url];
  sourceUrls.forEach((url, i) => appendSource(sourceList, url,
    sourceUrls.length > 1 ? `Post ${i + 1}` : "Bluesky post", i + 1));
  if (post.link_url && post.link_url !== post.url) {
    appendSource(sourceList, post.link_url, "Linked article", 2);
  }
  card.append(sourceList);

  const toggle = node("button", "card-toggle");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-label", `Show details for ${title}`);
  const chevron = node("span", "chevron");
  const upper = document.createElement("img");
  upper.src = "/static/assets/chevron-line-a.svg";
  upper.alt = "";
  const lower = document.createElement("img");
  lower.src = "/static/assets/chevron-line-b.svg";
  lower.alt = "";
  chevron.append(upper, lower);
  toggle.append(chevron);

  const detail = node("div", "idea-detail");
  detail.hidden = true;
  addDetailLine(detail, "Post", post.text);
  addDetailLine(detail, "Why it was surfaced", post.reason);
  addDetailLine(detail, "Author", post.author ? `@${post.author}` : "");
  if (post.created_at) addDetailLine(detail, "Posted", new Date(post.created_at).toLocaleDateString());
  toggle.addEventListener("click", async () => {
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!expanded));
    toggle.setAttribute("aria-label", `${expanded ? "Show" : "Hide"} details for ${title}`);
    card.classList.toggle("expanded", !expanded);
    detail.hidden = expanded;
    if (!expanded && !detail.dataset.pitchLoaded) {
      detail.dataset.pitchLoaded = "true";
      const pitchDetail = node("div", "pitch-detail");
      detail.append(pitchDetail);
      await loadPitchDetails(runId, pitch?.pitch_id, pitchDetail);
    }
  });
  card.append(toggle, detail);
  return card;
}

async function loadResults(runId, run) {
  const [posts, pitches] = await Promise.all([
    api(`/runs/${runId}/posts?label=solution`),
    api(`/runs/${runId}/pitches`),
  ]);
  const postsByUrl = new Map(posts.map((post) => [post.url, post]));
  // One card per pitch (a pitch can combine several posts about the same
  // response), then any solution posts that have no pitch yet. Posts the
  // verifier called Flagged or a Myth are left out.
  const hidden = new Set(["Flagged", "Myth"]);
  const cards = pitches.map((pitch) => [postsByUrl.get(pitch.source_url) || { url: pitch.source_url }, pitch]);
  for (const post of posts) {
    if (!post.pitch_url && !hidden.has(post.tier)) cards.push([post, null]);
  }
  ideaList.replaceChildren();
  ideaCount.textContent = cards.length ? `${cards.length} found` : "";

  if (!cards.length) {
    const setAside = posts.filter((post) => hidden.has(post.tier)).length;
    showMessage(run.status === "failed"
      ? "The run stopped before finding any solution posts. Check the API keys and run status, then try again."
      : setAside
        ? `The verifier set aside all ${setAside} possible solutions as myths or flagged accounts. Try a broader topic.`
        : "No posts were classified as possible solutions in this run. Try a broader topic or search phrase.");
    return;
  }

  showMessage("");
  cards.forEach(([post, pitch], index) => {
    ideaList.append(makeCard(post, pitch, runId, index));
  });
}

async function followRun(runId, generation) {
  localStorage.setItem("sjtool-last-run", String(runId));
  let run;
  while (generation === requestGeneration) {
    run = await api(`/runs/${runId}`);
    updateRunStatus(run);
    if (["done", "partial", "failed"].includes(run.status)) break;
    await new Promise((resolve) => window.setTimeout(resolve, 1500));
  }
  if (generation !== requestGeneration) return;
  setBusy(false);
  try {
    await loadResults(runId, run);
  } catch (error) {
    showMessage(`The run finished, but its results could not be loaded: ${error.message}`, true);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const topic = topicInput.value.trim();
  if (!topic) return;

  requestGeneration += 1;
  const generation = requestGeneration;
  ideaList.replaceChildren();
  ideaCount.textContent = "";
  showMessage("Starting your search…");
  statusPanel.hidden = false;
  statusTitle.textContent = "Preparing your search";
  statusDetail.textContent = `“${topic}”`;
  stats.replaceChildren();
  progressFill.style.width = "0%";
  progressTrack.classList.add("indeterminate");
  setBusy(true);

  const request = {
    topic,
    query: topic,
    max_results: Number(maxResultsInput.value) || 50,
  };
  const limit = Number(stageLimitInput.value);
  if (Number.isFinite(limit) && limit > 0) request.limit = limit;

  try {
    const result = await api("/runs", {
      method: "POST",
      body: JSON.stringify(request),
    });
    await followRun(result.run_id, generation);
  } catch (error) {
    if (generation !== requestGeneration) return;
    setBusy(false);
    progressTrack.classList.remove("indeterminate");
    statusTitle.textContent = "Could not start the search";
    showMessage(`${error.message}. Make sure the backend is running and configured.`, true);
  }
});

async function restoreLastRun() {
  const saved = localStorage.getItem("sjtool-last-run");
  if (!saved || !/^\d+$/.test(saved)) return;
  try {
    const run = await api(`/runs/${saved}`);
    topicInput.value = run.topic || "";
    updateRunStatus(run);
    if (["done", "partial", "failed"].includes(run.status)) {
      await loadResults(saved, run);
    } else {
      setBusy(true);
      showMessage("Your previous search is still running. This page will keep checking its progress.");
      requestGeneration += 1;
      followRun(Number(saved), requestGeneration);
    }
  } catch {
    localStorage.removeItem("sjtool-last-run");
  }
}

restoreLastRun();
