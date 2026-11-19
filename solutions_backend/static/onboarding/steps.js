/*
  Onboarding, one question per screen (like a dating-app sign-up).

  index.html has the screens (.step, numbered by data-step). This file only shows
  one at a time, checks each answer before moving on and runs the progress bar.
  app.js still does the real work: the place and publication lookups, the final
  check and saving the profile. So this file can be removed and the form still
  works as one long page (show every .step).
*/
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);

  document.addEventListener("DOMContentLoaded", () => {
    const form = $("onboard-form");
    if (!form) return;
    const steps = [...form.querySelectorAll(".step")];
    const last = steps.length - 1;
    const back = $("step-back");
    const next = $("step-next");
    let current = 0;

    function error(id, message) {
      const box = $(id);
      box.textContent = message;
      box.hidden = !message;
    }

    function show(i, focus = true) {
      current = Math.max(0, Math.min(last, i));
      steps.forEach((step, j) => { step.hidden = j !== current; });
      back.hidden = current === 0;
      next.hidden = current === last;
      next.textContent = current === 0 ? "Get started" : "Next";
      $("step-fill").style.width = `${Math.round((current / last) * 100)}%`;
      $("step-count").textContent = current ? `${current} of ${last}` : "";
      window.scrollTo(0, 0);
      if (focus && current) {
        const target = steps[current].querySelector("input, .chip");
        if (target) target.focus({ preventScroll: true });
      }
    }

    // A place or publication counts once it's picked from the list (app.js marks
    // the box "verified"), or if the lookup service is down.
    function picked(inputId, listId, errId, what) {
      const input = $(inputId);
      const value = input.value.trim();
      if (input.classList.contains("verified")) { error(errId, ""); return true; }
      const status = document.querySelector(`#${listId} .status`);
      if (value && status && /couldn't check/i.test(status.textContent)) { error(errId, ""); return true; }
      if (!value) error(errId, `Add the ${what}.`);
      else if (document.querySelector(`#${listId} [role=option]`)) error(errId, `Pick your ${what} from the list.`);
      else error(errId, `We couldn't confirm that ${what}. Check the spelling and pick from the list.`);
      input.focus();
      return false;
    }

    function ready(i) {
      if (i === 1) {
        if (!$("f-name").value.trim()) { error("err-name", "Add your name."); $("f-name").focus(); return false; }
        error("err-name", "");
      }
      if (i === 2) {
        const beats = [...document.querySelectorAll("#beat-chips .chip[aria-pressed=true]")].map((b) => b.textContent);
        if (!beats.length) { error("err-beat", "Pick at least one beat."); return false; }
        if (beats.includes("Other") && !$("f-other-beat").value.trim()) {
          error("err-beat", "Type your other beat, or unpick Other.");
          $("f-other-beat").focus();
          return false;
        }
        error("err-beat", "");
      }
      if (i === 3) return picked("f-area", "area-list", "err-area", "place");
      if (i === 4) return picked("f-pub", "pub-list", "err-pub", "publication");
      return true;
    }

    next.addEventListener("click", () => { if (ready(current)) show(current + 1); });
    back.addEventListener("click", () => show(current - 1));

    // Pressing Enter before the last screen means Next, not "save the profile".
    document.addEventListener("submit", (e) => {
      if (e.target !== form || current === last) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (ready(current)) show(current + 1);
    }, true);

    // If app.js's final check finds a problem on an earlier screen, go back to it.
    form.addEventListener("submit", () => setTimeout(() => {
      const bad = steps.findIndex((step, j) => j < last && step.querySelector(".error:not([hidden])"));
      if (bad > 0) show(bad);
    }, 0));

    // Someone editing a saved profile skips the welcome screen.
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem("sjtool_profile")); } catch { /* private mode */ }
    show(saved && saved.name ? 1 : 0, false);
  });
})();
