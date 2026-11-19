/*
  Links Nicole's home page to the onboarding (static/onboarding/).
  Loaded by one <script> line in static/index.html; it doesn't change app.js.

  - No profile yet? Go to onboarding first.
  - Shows "Hi, <name>" with Edit profile and Sign out links above the search.
  - Adds search suggestions from the journalist's beats and area; clicking one
    fills Nicole's search box and starts the search.
*/
(() => {
  "use strict";
  const ONBOARDING = "/static/onboarding/index.html";
  let profile = null;
  try { profile = JSON.parse(localStorage.getItem("sjtool_profile")); } catch { /* private mode */ }
  if (!profile) { location.replace(ONBOARDING); return; }

  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "/static/profile.css";
  document.head.append(css);

  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function link(text, href) {
    const a = node("a", "", text);
    a.href = href;
    return a;
  }

  function build() {
    const hero = document.querySelector(".hero");
    const form = document.querySelector("#search-form");
    const topic = document.querySelector("#topic");
    if (!hero || !form || !topic) return;

    // Greeting bar
    const bar = node("nav", "profile-bar");
    bar.setAttribute("aria-label", "Your profile");
    const first = (profile.name || "").trim().split(/\s+/)[0];
    bar.append(node("span", "profile-hi", first ? `Hi, ${first}` : "Welcome back"));
    const signOut = node("button", "profile-link", "Sign out");
    signOut.type = "button";
    signOut.addEventListener("click", () => {
      try { localStorage.removeItem("sjtool_profile"); } catch { /* private mode */ }
      location.href = ONBOARDING;
    });
    const edit = link("Edit profile", `${ONBOARDING}#onboard`);
    edit.className = "profile-link";
    bar.append(edit, signOut);
    hero.prepend(bar);

    // Suggestions: each beat, paired with the city they cover (if any).
    const beats = (profile.beat || "").split(",").map((b) => b.trim()).filter(Boolean);
    const area = (profile.area || "").trim();
    const wide = /^(national|international|global)$/i.test(area);
    const place = wide ? "" : area.split(",")[0].trim();
    const ideas = [...new Set(beats.map((b) => (place ? `${b} in ${place}` : b)))].slice(0, 4);
    if (!ideas.length) return;

    const box = node("div", "profile-suggestions");
    box.append(node("span", "profile-suggest-label", "For your beat:"));
    for (const idea of ideas) {
      const chip = node("button", "profile-chip", idea);
      chip.type = "button";
      chip.addEventListener("click", () => {
        topic.value = idea;
        form.requestSubmit();
      });
      box.append(chip);
    }
    form.after(box);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
