# Bluesky verification layer: team summary

*Phase 1 prototype, 17 Nov 2026. Scope: Bluesky posts already classified as solutions. No other platforms, no scraper or classifier changes.*

## Bottom line

The verifier works end to end and 50 unit tests pass, but **on the dengue sample nothing reached above Early signal**, and the reason is a data gap, not a rules gap: 34 of the 44 posts that contain a link have a link that Bluesky cut short in the text, and the scraper does not save the full URL. The single highest-value next step is for the scraper to save each post's full link (it is already in the API response the scraper receives).

## How the verifier works

1. **Clean.** Normalize five timestamp formats to UTC, drop exact duplicate rows and empty posts, extract links, mark truncated links and link-only posts, guess language.
2. **Group.** Posts about the same idea become one solution, so the output is a short list of solutions, not posts.
3. **Spot the claim.** Pick the most checkable sentence in each solution (see ClaimBuster below).
4. **Check.** Three pluggable verifiers, each returning `{check_name, score, evidence, confidence, source_url}`:
   - *Source:* account under 30 days old; more than 10 posts per hour; same text from 3+ accounts. Missing account details are noted and never penalized. Bridged, custom-domain and aggregator accounts are labelled as context only.
   - *Citation:* a source counts only if the page loads **and** the claim is on it (a 6+ word quotation must appear verbatim; otherwise every number in the claim and at least 60% of its key words must appear). Failures are recorded: timeout, 404, paywall, forbidden, text not found, truncated link, cap reached. `.gov` and `.edu` are "primary".
   - *Contradiction:* a human-reviewed myth list with exact source quotes, re-checked live; plus a number in the post that is missing from its own source.
5. **Tier, then show.** Only the top 3-5 Strong lead, Promising and Early signal items become tiles. Myth and Flagged stay in `audit_log.json` with reasons.

## Tier definitions (first matching rule wins)

| # | Tier | Testable rule |
|---|---|---|
| R1 | **Myth** (hidden) | A credible source contradicts the claim. |
| R2 | **Strong lead** | At least 1 verified primary source, or 2 verified sources on different domains, and no contradiction. Flags are disclosed but never lower it. If no news source verified, the tile says "possible coverage gap" and stays Strong. |
| R3 | **Flagged** (hidden) | Any account or behavior flag without Strong-level evidence. |
| R4 | **Early signal (capped)** | A partial contradiction, such as a number mismatch, caps the tier here regardless of traction. |
| R5 | **Promising** | 1 verified source; or high traction from an unflagged account, with no data (allowed as a pitch). |
| R6 | **Early signal** | Everything else. The tile says "No verified source found." |

High traction is the 90th percentile of weighted engagement (likes + 2 x reposts + replies) in the run's own data, with a floor of 15. On the dengue sample the threshold is 15 and 6 of 94 posts clear it.

## Results on the dengue sample (100 rows)

- **Dropped 6 rows:** 5 exact duplicates, 1 empty post. 94 posts remain; 71 English, 7 other languages, 16 too short to tell.
- **13 posts were labelled as solutions** by a stand-in label file I wrote by hand (the sample is raw, and the classifier is Josie's). 81 were not solutions. 4 Portuguese solution posts were held for a later phase, including a Wolbachia release in Paraná.
- **8 solutions checked:**

| Solution | Tier | Why |
|---|---|---|
| Wolbachia in Singapore | Early signal | Post has no link. Author is an aggregator. |
| SMASH 2.0 Act | Early signal | Its one link (patimes.org) is truncated. |
| St. Petersburg tire-shop injunction (2 posts) | Early signal | Its one link (tampabay.com) is truncated. |
| Tax cut vs. mosquito control | Early signal | Headline text with no link. |
| Health-department contact tracing | Early signal | Anecdote, no link. |
| Bite prevention (not in top 5) | Early signal | Both links truncated (one is cdc.gov). |
| CDC Level 3 response (not in top 5) | Early signal | Full link, but the fetch was blocked where I ran it. |
| Garlic prevents dengue | **Myth, hidden** | UW-Madison Entomology: "Studies of garlic and vitamin B did not find evidence that these substances could reduce mosquito attraction." |

**Where the system is wrong or unsure**

- **It under-rates real leads.** I checked three by hand, outside the pipeline: a 2023 Senate press release names the "Strengthening Mosquito Abatement for Safety and Health (SMASH) 2.0 Act" ([king.senate.gov](https://www.king.senate.gov/newsroom/press-releases/senator-king-introduces-bipartisan-bill-to-strengthen-fight-against-mosquito-borne-diseases)); a 1 Nov 2026 article reports Project Wolbachia reaching "53 per cent of all homes in Singapore" ([littlebigreddot.com](https://littlebigreddot.com/project-wolbachia-10-years-53-per-cent-households-singapore/)); and WLRN reported that property-tax cuts could hit mosquito control ([wlrn.org](https://www.wlrn.org/environment/2026-06-29/cutting-florida-property-taxes-could-take-a-bite-out-of-mosquito-control)). I did not find independent confirmation of the tire-shop injunction in one search. None of this is in the tiles, because the pipeline did not find it.
- **Page fetching was not exercised live.** My workspace blocks outside sites, so the citation logic is proven only against synthetic test pages. Run it on a normal machine before trusting it.
- **The garlic Myth rests on my reading of the source**, since the live re-check was blocked. It needs Sydney's sign-off. The post is also a joke, which the system cannot tell.
- **The flag rules did not fire on any labelled solution.** They do fire on the sample's "Bangladesh death toll" headline (7 accounts, same text). The "485 locally acquired cases" posts are paraphrases, so they are correctly not called copies, but they are also not independent confirmation, and the system does not yet trace them to one origin.
- **Account age is unknown for every post** (no profile data), so that rule is untested on real data.
- **Ranking among Early signals** uses claim checkability, which is a judgment call.

## ClaimBuster: what it really provides

The repository contains training code and labelled data for check-worthiness scoring (SVM, BiLSTM, adversarial BERT). It is a claim *spotter*, not a fact-checker. **It ships no trained weights.** The transformer's README is marked deprecated and its requirements file, byte-identical to the `requirements.txt` you sent, pins TensorFlow 2.2, which does not install on Python 3.13. I did not attempt a Python 3.8 container. What runs today: a TF-IDF + linear SVM I trained on the repo's own 7,976 labelled sentences with scikit-learn 1.9, scoring 80% accuracy on its 702-sentence test set (precision 0.92, recall 0.66 on check-worthy sentences). It is trained on political debates, so it is blended 60/40 with the rule-based scorer, which is also the automatic fallback. The repo is GPL-3.0; confirm before redistributing the model file.

## Phase 1 vs. Phase 2

Phase 1 (built): no APIs, no paid calls, plain page fetches only. Caps: 40 fetches per run, 400 per day, 4,000 per month, all configurable; the run finishes and reports when a cap is hit. Estimated cost per run: $0.

| Phase 2 option | Replaces | Access, cost, limits, terms (as read on 17 Nov 2026) |
|---|---|---|
| Bluesky post/profile lookup | Truncated links, account age | I could not load the official rate-limit page. **Unconfirmed.** |
| [Google Fact Check Tools](https://developers.google.com/fact-check/tools/api) | Curated myth list | "You must set up an API key." Subject to Google's API Terms of Service. Cost and quota are **not stated** on the pages I read. |
| [ClaimBuster API](https://idir.uta.edu/claimbuster/api) | Local claim spotter | "You'll need to request a key." Cost, rate limit and terms **not stated**. |
| [InVID plugin](https://www.invid-project.eu/tools-and-services/invid-verification-plugin/) | Nothing yet (image/video) | Free browser plugin, MIT licence. No public API stated; its back-end services "are not open-sourced". Low fit: our posts are text. |
| FactFlowAI | Unknown | **Could not verify** that it exists or offers an API; its site refused automated reading. Need a link from whoever suggested it. |
| [Brave Search API](https://brave.com/search/api/) | Coverage-gap and independent-source search | "$5 per 1,000 requests", "$5 in free credits every month", 50 queries per second. Storing results needs a custom plan. At about 3 searches per solution and 8 solutions, roughly $0.12 per run. |

## Update: SJTool evidence code merged

The three evidence files (`evidence_scoring.py`, `evidence_sources.py`, `find_evidence.py`) are now part of the package. Their scoring and trusted-source registry are kept; their OpenAlex search is a verifier check that is **off by default** (it is an outside API). When on, results appear on the tile as "related research" and do not change the tier, because a topic match does not show the paper supports the claim and the original assigned every hit a fixed 0.80 relevance. Verified sources also get an informational SJTool-style score. This live search has not been run against OpenAlex (blocked in my workspace); it is tested with a fake.

## Known limitations

English only. No PDF reading (many .gov reports are PDFs). Keyword matching can be fooled by a page that mentions the words but says the opposite. Posting rate is a lower bound, since we only see posts matching the search. Paywalled and JavaScript-only pages fail. The myth list has one entry.

## Next steps and owners

| Owner | Next step |
|---|---|
| **Breindel** (scraper) | Save each post's full link URL, plus language and reply/quote fields. **Revoke and reissue the Bluesky app password that is written in `bskysearch.py`**, and load it from an environment variable only. |
| **Josie** (classification) | Output `url, is_solution, solution_key, summary` to replace my stand-in labels; review my 13 labels. |
| **Sydney** (verification design) | Sign off on tier rules, the traction floor, the 6-word quote rule, and the garlic entry; add myths. |
| **Inaaya** (verification code) | Run on a normal network; add link resolution once approved; tune thresholds on a second issue. |
| **Sam** (pitches) | Confirm the tile fields are enough to draft a pitch; define what a "pitch without data" must say. |
| **Nicole** (web interface) | Call `verify_solutions(posts, issue)` and style tiles from `tiles.json`; `results.html` is a reference layout. |

**Decision needed from Inaaya: what is the new deadline?** The 12 Nov date in the notes has passed.
