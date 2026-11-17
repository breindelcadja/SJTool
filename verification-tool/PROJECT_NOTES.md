# Project notes: solutions verification tool

(Roles only, no names. Update this file as plans change.)

## What the tool does (journalist's view)
1. Journalist enters a problem, e.g. "wildfires in CA" or "dengue prevention".
2. The tool searches public posts (Bluesky, Reddit; NOT Nextdoor) for that phrase.
3. A classifier keeps posts that describe a solution (not complaints) and groups
   them into a short list of solution ideas (3 to 5).
4. The tool finds data to back each idea and cites sources.
5. Output: a list of verified solutions, graded, each with the sources behind it.

## Due Thursday Nov 12
- Verification process: other ways to verify, other sites to connect Claude to,
  and code parameters that "verify" posts passing the claim-scoring step.
- Combine the Bluesky scraper with classification.
- Front end: combine the web interface with what is already in GitHub.
- Back end: feasibility of the verification process and general parameters.
- Pitches: add the problem, the response, and people to reach out to.

## Workflow owners (by task)
1. Web interface. 2. Improve the Bluesky scraper. 3. Verification tool (code, and
how to think about grading). 4. Better classification (send post title, links and
descriptions to Claude; test accuracy). 5. Turn classifications into pitches
(problem, response, solution, people to contact, data; merge duplicates).
6. Future: website with pitch cards, search and filters.

## Verification: options researched
- Google Fact Check Tools API: free, but only works if a fact-checking
  organization has already covered the claim. Good for the Myth tier.
- ClaimBuster: scores how check-worthy a sentence is. It does NOT say whether a
  claim is true. Used only to decide what to check first.
- InVID / WeVerify: image and video verification, not text claims.
- FactFlowAI: not researched yet.
- Search trusted sources (PubMed, Europe PMC, CDC, WHO, .gov) by keyword, with
  claim-type routing. Designed, not built.

## Prototype prefilter ideas (from the team notes)
- How long has the account existed? How many posts in the last hour?
  (Both are built as source flags.)

## Where the code stands
- Built and tested offline: claim scoring (ClaimBuster API or free local model),
  source flags, citation checks, tier rules, tiles page, usage caps.
- Not tested live: ClaimBuster API calls, Claude evidence search.
- Not built: PubMed/Europe PMC search step, claim-type routing, Google Fact Check
  step, local inference model step, scraper/classifier hookup, front end.
- Open decisions: who pays for the evidence step, ClaimBuster terms, GPL license
  of the claimbuster-spotter repo and its data, accuracy on real posts.

## Build plan v2 (step by step, confirm each step)
1. Input + engagement + social lead (contact info) + search context (place, topic,
   timeframe) + coverage record with search links. BUILT, awaiting confirmation.
2. Filter Myth and Flagged out of the journalist list (keep a hidden log); show only
   Strong lead, Promising, Early signal; shortlist 3 to 5; allow high-engagement
   social leads as pitches. NOT STARTED.
3. Free evidence search (PubMed / Europe PMC / .gov). NOT STARTED.
4. "Not already reported" check for Strong lead. NOT STARTED.
5. Final output: list of verified solutions, tiles, one function for the backend.
Answers so far: engagement = top 20% OR 50+ (fixed number is my assumption);
coverage = journalist records whether it was already reported, tool gives search
links from known place/topic/timeframe; the tool KNOWS place, topic and timeframe.
Still open: evidence sources (PubMed / Europe PMC / .gov), who groups posts into
pitches (assumed: scraper does), whether to drop posts outside the timeframe.
Assumed without confirmation: scraper field names; engagement floor of 5.

## Repos reviewed (Nov 12)
- RealityCheck (MIT): confirms ClaimBuster has extra endpoints with the same key,
  query/fact_matcher and query/knowledge_bases (matches claims to published
  fact-checks). Response shape seen in its 2023 code; untested by us.
- factcheckexplorer (MIT): keyless Python access to Google's Fact Check Explorer via
  an unofficial endpoint. Can fetch claims, ratings, source and date into a CSV.
- financial-misinformation-scraper: no license file, so use the idea only. Its sample
  data is low quality (truncated claims, logo images, India finance focus).
- 24agentic (MIT, Node): ideas only (search the negated claim to find
  contradictions; keyword-overlap support check). Needs an LLM key to do real work.
- factcheckassistant (GPL-3): skip. Asks a 1.1B-parameter model to fact-check from
  memory, with no sources.

## Real scraper output (first look, Nov 12)
Columns: created_at, author, text, likes, reposts, replies, url. 100 rows on "dengue":
5 repeated posts, 1 empty, 10 link-only. No account creation date or posts-per-hour, so
source flags are all "unknown" and Flagged can never trigger yet. The file is NOT
classified: almost all posts are news links, case counts, jokes or personal stories.
Engagement and the claim scorer must run only AFTER the solution classifier.
The claim scorer rates case-count statistics highest (it finds factual claims, not
solutions). Many posts are not in English (Portuguese, Spanish).
The real CSV is kept out of the repo (.gitignore): it holds other people's posts.

## Pushing to GitHub
Use ./push_to_github.sh (new branch + pull request into the team repo, never main).
