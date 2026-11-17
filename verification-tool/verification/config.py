"""All settings in one place.

Every threshold below is PROVISIONAL. Tune them after journalists rank a sample
batch of real pitches. The config version is stamped on every result so results
can be re-graded after tuning.
"""

import os

CONFIG_VERSION = "v0-provisional"

# ---- Claude (evidence search) ----------------------------------------------
# Check that this model name is available on your account.
MODEL = os.environ.get("VERIFIER_MODEL", "claude-sonnet-5-5")
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 6}
MAX_CONTINUES = 5  # how many times to continue a paused search turn

# ---- ClaimBuster (claim scoring) -------------------------------------------
CLAIMBUSTER_URL = "https://idir.uta.edu/api/v2/score/text/"
PAUSE_SECONDS = 0.5  # pause between API calls until rate limits are known
RATE_LIMIT_RETRIES = 2  # extra tries after an HTTP 429 (too many requests)
RATE_LIMIT_WAIT_SECONDS = 20  # wait before each retry; doubles every time

# ---- Per-run caps (override on the command line; None = no limit) ------------
# The evidence search is the only paid step, so its default is the tightest.
MAX_PITCHES_PER_RUN = 25
MAX_SCORING_CALLS_PER_RUN = 200
MAX_EVIDENCE_CALLS_PER_RUN = 5

# ---- Thresholds ------------------------------------------------------------
CLAIM_SCORE_CUTOFF = 0.5    # minimum claim score to go on to verification
NEW_ACCOUNT_DAYS = 30       # account younger than this is flagged
BURST_POSTS_PER_HOUR = 10   # more posts than this in an hour is flagged
DUPLICATE_ACCOUNTS = 3      # this many accounts with near-identical text is flagged
FLAG_RATIO_CUTOFF = 0.5     # share of flagged posts that makes a pitch "flagged"

# ---- Source quality (decided by domain, never by the model) ----------------
PRIMARY_SUFFIXES = (".gov", ".edu", ".mil")
PRIMARY_DOMAINS = (
    "who.int", "paho.org", "ecdc.europa.eu", "thelancet.com", "nejm.org",
    "nature.com", "plos.org", "bmj.com", "sciencedirect.com", "springer.com",
)
# Social posts are where an idea comes from. They never count as evidence.
SOCIAL_DOMAINS = (
    "reddit.com", "bsky.app", "bluesky.social", "x.com", "twitter.com",
    "facebook.com", "instagram.com", "threads.net", "tiktok.com",
    "nextdoor.com", "youtube.com", "discord.com", "whatsapp.com",
)

# ---- Daily / monthly caps that carry across runs (tracked in usage_log.json) ----
# The dollar figure is a ROUGH ESTIMATE, not your real bill. Check the Anthropic
# Console for the actual number and set a spending limit there too.
ESTIMATED_COST_PER_EVIDENCE_CALL = 0.15  # dollars; top of my 5-15 cent guess
MAX_DAILY_SCORING_CALLS = 1000
MAX_DAILY_EVIDENCE_CALLS = 20
MAX_DAILY_SPEND_USD = 3.00
MAX_MONTHLY_SPEND_USD = 25.00
USAGE_FILE = os.environ.get("VERIFIER_USAGE_FILE", "usage_log.json")

# ---- Bluesky engagement (what counts as "a lot of engagement") ---------------
# ASSUMED defaults, not confirmed with the team. A post is a "social lead" when it
# is in the top share of its batch by engagement (likes + reposts + replies) AND
# has at least the floor, OR passes the fixed minimum if you set one.
ENGAGEMENT_TOP_SHARE = 0.20
ENGAGEMENT_FLOOR = 5
ENGAGEMENT_FIXED_MIN = 50  # ASSUMED: any post with 50+ engagement also counts (set None to turn off)
