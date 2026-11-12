#!/usr/bin/env python3
"""
Onboarding: saves a journalist's name, email, beat, area and publication to
journalists.json, so rank_pitches.py can put the pitches that matter most to
them first. Later, the website's sign-up form will write to the same file in
the same format; this lets you onboard people before the site exists.

Usage, from inside the "solutions or not" folder (no API key needed):
    python onboarding.py          # walks through the questions
    python onboarding.py --list   # shows everyone onboarded so far

Email is optional. It's how a profile is recognized: onboarding again with the
same email updates that person's profile instead of making a second one.
Without an email, each onboarding makes a new profile.

REAL PLACES AND PUBLICATIONS ONLY
- Area is checked against OpenStreetMap (through the free Photon service), which
  also catches typos: "San Fransisco" offers "San Francisco, California, United
  States". "National", "international" and "global" are also accepted.
- Publication is checked against Wikidata, a free public database that lists
  most news outlets. "Freelance" is always accepted. An outlet that isn't listed
  can be entered by its website instead (like dailynorthwestern.com), which is
  accepted if the site really exists.
If the internet is down, the answer is saved as typed and marked unchecked.

journalists.json holds people's emails, so it's left out of GitHub (see
.gitignore). Keep it on your computer, or in the website's database later.

Each profile in journalists.json looks like:
    {
      "id": "jane-doe",
      "name": "Jane Doe",
      "email": "jane@example.com",
      "beat": "public health",
      "area": "Tampa, Florida, United States",
      "area_details": {"verified": true, "type": "city", "country": "United States", ...},
      "publication": "Tampa Bay Times",
      "publication_details": {"verified": true, "source": "wikidata", "id": "Q...", ...},
      "added_on": "2026-11-12",
      "updated_on": "2026-11-12"
    }
"""

import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOURNALISTS = HERE / "journalists.json"
PHOTON = os.environ.get("PHOTON_URL", "https://photon.komoot.io/api/")
WIKIDATA = os.environ.get("WIKIDATA_URL", "https://www.wikidata.org/w/api.php")
USER_AGENT = "SJTool onboarding (student journalism project)"

WIDE_AREAS = {"national": "National", "nationwide": "National",
              "international": "International", "global": "Global",
              "worldwide": "Global"}
FREELANCE = {"freelance", "freelancer", "independent", "self-employed", "none"}
# A Wikidata result counts as a news outlet if its description has one of these.
MEDIA_WORDS = ("newspaper", "magazine", "news", "broadcast", "radio", "television",
               "tv ", "tv,", "journal", "publication", "outlet", "agency",
               "newsletter", "media", "website", "podcast", "network", "daily",
               "weekly", "periodical", "tabloid", "wire service")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Colors only when the output is a real Terminal window.
COLOR = sys.stdout.isatty()


def style(text, *codes):
    return f"\033[{';'.join(codes)}m{text}\033[0m" if COLOR else text


def bold(t): return style(t, "1")
def dim(t): return style(t, "2")
def teal(t): return style(t, "36")
def green(t): return style(t, "32")
def warn(t): return style(t, "33")


def rule():
    print(dim("─" * min(shutil.get_terminal_size((72, 20)).columns, 72)))


class Offline(Exception):
    """The lookup service couldn't be reached."""


def get_json(url, params):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        raise Offline(str(e))


# ---------- Places ----------

def find_places(text):
    """Real places matching the text, best first, typos included."""
    data = get_json(PHOTON, {"q": text, "limit": 10, "lang": "en"})
    places, seen = [], set()
    for feature in data.get("features", []):
        p = feature.get("properties", {})
        if p.get("osm_key") not in ("place", "boundary"):
            continue  # skip shops, streets and buildings
        parts = []
        for part in (p.get("name"), p.get("county"), p.get("state"), p.get("country")):
            if part and part not in parts:
                parts.append(part)
        label = ", ".join(parts)
        if not label or label in seen:
            continue
        seen.add(label)
        lon, lat = (feature.get("geometry", {}).get("coordinates") or [None, None])[:2]
        places.append({"label": label, "details": {
            "verified": True, "source": "openstreetmap", "name": p.get("name", ""),
            "type": p.get("osm_value", ""), "state": p.get("state", ""),
            "country": p.get("country", ""), "country_code": p.get("countrycode", ""),
            "lat": lat, "lon": lon}})
    return places[:5]


# ---------- Publications ----------

def is_media(description):
    d = (description or "").lower() + " "
    return any(word in d for word in MEDIA_WORDS)


def search_wikidata(text):
    data = get_json(WIKIDATA, {"action": "wbsearchentities", "search": text,
                               "language": "en", "uselang": "en", "type": "item",
                               "limit": 15, "format": "json"})
    return [{"label": r.get("label", ""), "details": {
                "verified": True, "source": "wikidata", "id": r.get("id", ""),
                "description": r.get("description", "")}}
            for r in data.get("search", []) if is_media(r.get("description"))]


def find_publications(text):
    """Real news outlets matching the text. Tries a spelling suggestion if the
    exact words find nothing."""
    results = search_wikidata(text)
    if results:
        return results[:5], None
    data = get_json(WIKIDATA, {"action": "query", "list": "search", "srsearch": text,
                               "srinfo": "suggestion", "srlimit": 1, "format": "json"})
    suggestion = data.get("query", {}).get("searchinfo", {}).get("suggestion")
    if suggestion and suggestion.lower() != text.lower():
        return search_wikidata(suggestion)[:5], suggestion
    return [], None


def check_website(text):
    """If the text looks like a web address, return it if the site responds."""
    if " " in text or "." not in text:
        return None
    url = text if re.match(r"^https?://", text) else f"https://{text}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status < 400:
                host = urllib.parse.urlparse(response.geturl()).netloc
                return host[4:] if host.startswith("www.") else host
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        pass
    return None


# ---------- Questions ----------

def prompt():
    return input(f"       {teal('›')} ").strip()


def choose(options, heading):
    """Show numbered matches. Returns the chosen one, or None to retype."""
    print(f"       {heading}")
    for n, option in enumerate(options, 1):
        extra = option["details"].get("description", "")
        print(f"         {teal(str(n))}  {option['label']}" + (dim(f"  ({extra})") if extra else ""))
    print(dim("       Press Enter for 1, type a number, or 0 to type it again."))
    while True:
        answer = prompt()
        if answer == "":
            return options[0]
        if answer.isdigit() and 0 <= int(answer) <= len(options):
            return None if answer == "0" else options[int(answer) - 1]
        print(warn(f"       Type a number from 0 to {len(options)}."))


def header():
    print()
    rule()
    print(bold(teal("  SJTool")) + dim("  ·  solutions journalism pitches"))
    rule()
    print("  Tell us what you cover, and we'll put the solutions")
    print("  stories that matter most to you at the top of your list.")
    print(dim("  Five quick questions (email is optional). Press Ctrl+C to quit."))
    print()


def ask_text(number, question, example):
    print(f"  {teal(f'{number}/5')}  {bold(question)}")
    print(dim(f"       e.g. {example}"))
    while True:
        answer = prompt()
        if answer:
            print()
            return answer
        print(warn("       This one is required."))


def ask_email(number):
    print(f"  {teal(f'{number}/5')}  {bold('What is your email? (optional)')}")
    print(dim("       e.g. jane@example.com, or press Enter to skip"))
    while True:
        answer = prompt()
        if not answer or EMAIL.match(answer):
            print()
            return answer.lower()
        print(warn("       That doesn't look like an email. Try again, or press Enter to skip."))


def ask_area(number):
    print(f"  {teal(f'{number}/5')}  {bold('What area do you cover?')}")
    print(dim("       e.g. Tampa, Florida  or  Brazil  or  national"))
    while True:
        text = prompt()
        if not text:
            print(warn("       This one is required."))
            continue
        if text.lower() in WIDE_AREAS:
            print()
            return WIDE_AREAS[text.lower()], {"verified": True, "source": "scope",
                                              "type": text.lower()}
        try:
            places = find_places(text)
        except Offline:
            print(warn("       Couldn't check that place right now, so it's saved as typed."))
            print()
            return text, {"verified": False}
        if not places:
            print(warn(f"       We couldn't find a place called \"{text}\". "
                       "Check the spelling and try again."))
            continue
        exact = places[0]["details"]["name"].lower() == text.split(",")[0].strip().lower()
        pick = choose(places, "Which one do you mean?" if exact else "Did you mean:")
        if pick:
            print()
            return pick["label"], pick["details"]


def ask_publication(number):
    print(f"  {teal(f'{number}/5')}  {bold('Where do you publish?')}")
    print(dim("       e.g. Tampa Bay Times, or freelance. Not listed? Type its website."))
    while True:
        text = prompt()
        if not text:
            print(warn("       This one is required."))
            continue
        if text.lower() in FREELANCE:
            print()
            return "Freelance", {"verified": True, "source": "freelance"}
        try:
            site = check_website(text)
            if site:
                print(green(f"       ✓ Found the website {site}."))
                print()
                return site, {"verified": True, "source": "website", "website": site}
            outlets, suggestion = find_publications(text)
        except Offline:
            print(warn("       Couldn't check that publication right now, so it's saved as typed."))
            print()
            return text, {"verified": False}
        if not outlets:
            print(warn(f"       We couldn't find a publication called \"{text}\"."))
            print(dim("       Check the spelling, or type the outlet's website (like dailynorthwestern.com)."))
            continue
        heading = (f"Did you mean \"{suggestion}\"?" if suggestion else
                   "Which one?" if outlets[0]["label"].lower() == text.lower() else
                   "Did you mean:")
        pick = choose(outlets, heading)
        if pick:
            print()
            return pick["label"], pick["details"]


def summary(profile):
    rule()
    print(bold("  Here's what we'll save:"))
    print()
    for key, label in [("name", "Name"), ("email", "Email"), ("beat", "Beat"),
                       ("area", "Area"), ("publication", "Publication")]:
        value = profile[key] or dim("(skipped)")
        details = profile.get(f"{key}_details")
        if details and not details.get("verified"):
            value = f"{value} {warn('(not checked)')}"
        print(f"    {dim(label.ljust(12))} {value}")
    print()


# ---------- Saving ----------

def load():
    if not JOURNALISTS.exists():
        return []
    return json.loads(JOURNALISTS.read_text(encoding="utf-8"))


def save(journalists):
    tmp = JOURNALISTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(journalists, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(JOURNALISTS)


def make_id(name, taken):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "journalist"
    new_id, n = base, 2
    while new_id in taken:
        new_id, n = f"{base}-{n}", n + 1
    return new_id


def show_list(journalists):
    print()
    if not journalists:
        print("  No one has been onboarded yet. Run: python onboarding.py")
        print()
        return
    print(bold(f"  {len(journalists)} journalist{'s' if len(journalists) != 1 else ''}"))
    rule()
    for j in journalists:
        print(f"  {bold(j['name'])}  {dim(j.get('email', ''))}")
        print(dim(f"    {j['beat']}  ·  {j['area']}  ·  {j['publication']}"))
    print()


def main():
    ap = argparse.ArgumentParser(description="Onboard a journalist.")
    ap.add_argument("--list", action="store_true", help="Show everyone onboarded")
    args = ap.parse_args()

    journalists = load()
    if args.list:
        show_list(journalists)
        return

    try:
        header()
        profile = {"name": ask_text(1, "What's your name?", "Jane Doe")}
        profile["email"] = ask_email(2)
        profile["beat"] = ask_text(3, "What's your beat?",
                                   "public health, environment, local government")
        profile["area"], profile["area_details"] = ask_area(4)
        profile["publication"], profile["publication_details"] = ask_publication(5)

        summary(profile)
        confirm = input(f"  Save this? {dim('(Y/n)')} {teal('›')} ").strip().lower()
        if confirm in ("n", "no"):
            print(dim("\n  Not saved. Run python onboarding.py to start over.\n"))
            return
    except (KeyboardInterrupt, EOFError):
        print(dim("\n\n  Stopped. Nothing was saved.\n"))
        return

    today = date.today().isoformat()
    existing = None
    if profile["email"]:
        existing = next((j for j in journalists
                         if j.get("email", "").lower() == profile["email"]), None)
    if existing:
        # Same email: update their profile, keeping their ID and start date.
        existing.update(profile, updated_on=today)
        message = f"  ✓ Welcome back, {profile['name']}! Your profile is updated."
    else:
        taken = {j["id"] for j in journalists}
        journalists.append({"id": make_id(profile["name"], taken), **profile,
                            "added_on": today, "updated_on": today})
        message = f"  ✓ Welcome, {profile['name']}! Your profile is saved."
    save(journalists)

    print()
    print(green(message))
    print(dim("  Next: python rank_pitches.py"))
    print()


if __name__ == "__main__":
    main()
