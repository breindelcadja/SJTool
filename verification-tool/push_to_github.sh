#!/usr/bin/env bash
# Push this project to your team's GitHub repo on a NEW BRANCH, then open a pull request.
#
#   bash push_to_github.sh                      asks for the repo address
#   bash push_to_github.sh --repo https://github.com/owner/repo
#   bash push_to_github.sh --dry-run            do everything except the push
#
# This file can sit ANYWHERE (your Desktop is fine). It finds the project by:
#   1. --project PATH, if you give one
#   2. the folder it is in, if that folder holds the project
#   3. --zip PATH, or else the newest verification-tool*.zip in Downloads or Desktop
#
# Options: --repo URL   --subfolder NAME (default verification-tool)
#          --branch NAME   --project PATH   --zip PATH   --yes   --dry-run
#
# It never touches your project folder. It clones the team repo to a temporary
# folder, copies the project in on a new branch, scans for keys, shows you what
# will be sent, and only pushes after you say yes. Keys, usage logs, trained
# models, your scraper CSV and result files are always left out.
set -u

REPO=""; SUBFOLDER="verification-tool"; BRANCH=""; ASSUME_YES=0; DRY=0; PROJECT=""; ZIP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2;;
    --subfolder) SUBFOLDER="${2:-}"; shift 2;;
    --branch) BRANCH="${2:-}"; shift 2;;
    --project) PROJECT="${2:-}"; shift 2;;
    --zip) ZIP="${2:-}"; shift 2;;
    --yes) ASSUME_YES=1; shift;;
    --dry-run) DRY=1; shift;;
    -h|--help) sed -n '2,17p' "$0"; exit 0;;
    *) echo "Unknown option: $1 (try --help)"; exit 1;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC=""; WORK=""; ZIPDIR=""
cleanup() {
  [ -n "$WORK" ] && [ -d "$WORK" ] && rm -rf "$WORK"
  [ -n "$ZIPDIR" ] && [ -d "$ZIPDIR" ] && rm -rf "$ZIPDIR"
}
trap cleanup EXIT
die() { echo; echo "STOPPED: $1"; exit 1; }
ask() {  # ask "question"  -> returns 0 for yes
  [ "$ASSUME_YES" -eq 1 ] && return 0
  read -r -p "$1 [y/N] " reply
  case "$reply" in y|Y|yes|YES) return 0;; *) return 1;; esac
}

command -v git >/dev/null 2>&1 || die "git is not installed. On a Mac, run: xcode-select --install"

# find the project
if [ -n "$PROJECT" ]; then
  SRC="$(cd "$PROJECT" 2>/dev/null && pwd)" || die "Can't open the project folder: $PROJECT"
elif [ -f "$SCRIPT_DIR/verification/pipeline.py" ]; then
  SRC="$SCRIPT_DIR"
else
  if [ -z "$ZIP" ]; then
    ZIP="$(ls -t "$HOME"/Downloads/verification-tool*.zip "$HOME"/Desktop/verification-tool*.zip 2>/dev/null | head -1)"
  fi
  [ -n "$ZIP" ] && [ -f "$ZIP" ] || die "Couldn't find the project. Put verification-tool.zip in your Downloads folder, or run with --zip PATH or --project PATH."
  echo "Using the project inside: $ZIP"
  ask "Is that the newest zip?" || die "Run again with --zip PATH pointing at the right one."
  command -v unzip >/dev/null 2>&1 || die "unzip is not installed."
  ZIPDIR="$(mktemp -d)"
  unzip -q "$ZIP" -d "$ZIPDIR" || die "Couldn't unzip $ZIP."
  PIPE="$(find "$ZIPDIR" -maxdepth 3 -type f -path '*/verification/pipeline.py' | head -1)"
  [ -n "$PIPE" ] || die "That zip doesn't contain the verification tool."
  SRC="$(dirname "$(dirname "$PIPE")")"
fi
[ -f "$SRC/verification/pipeline.py" ] || die "No verification tool found in $SRC."
# never copy a whole home or Desktop folder (it holds personal files)
case "$SRC" in
  "$HOME"|"$HOME/"|"$HOME/Desktop"|"$HOME/Desktop/"|"$HOME/Documents"|"$HOME/Downloads") \
    die "Refusing to push $SRC, it holds your other files. Put the project in its own folder, or use --zip.";;
esac

if [ -z "$REPO" ]; then
  read -r -p "Team repo address (like https://github.com/owner/repo): " REPO
fi
REPO="$(echo "$REPO" | tr -d '[:space:]')"
[ -n "$REPO" ] || die "No repo address given."
[ -n "$SUBFOLDER" ] || die "Subfolder name can't be empty."
[ -n "$BRANCH" ] || BRANCH="add-verification-tool-$(date +%Y%m%d-%H%M)"

echo "Project folder : $SRC"
echo "Team repo      : $REPO"
echo "New branch     : $BRANCH"
echo "Goes into      : $SUBFOLDER/"
echo

# 1. run the tests, if Python is available
if command -v python3 >/dev/null 2>&1; then
  if (cd "$SRC" && python3 -m unittest discover -s tests -t . >/tmp/vt_tests.log 2>&1); then
    echo "Tests: passed"
  else
    echo "Tests: FAILED (see /tmp/vt_tests.log)"
    ask "Push anyway?" || die "Fix the tests first."
  fi
fi

# 2. clone the team repo into a temporary folder
WORK="$(mktemp -d)"
echo "Cloning the team repo..."
git clone "$REPO" "$WORK/repo" 2>&1 | tail -3
[ -d "$WORK/repo/.git" ] || die "Could not clone $REPO. Check the address and that you have access (sign in to GitHub, or ask a teammate to add you)."
cd "$WORK/repo" || die "Could not enter the clone."
BASE="$(git symbolic-ref --short HEAD 2>/dev/null || echo main)"
git checkout -b "$BRANCH" >/dev/null 2>&1 || die "Could not create branch $BRANCH."

if [ -d "$SUBFOLDER" ] && [ -n "$(ls -A "$SUBFOLDER" 2>/dev/null)" ]; then
  echo "Note: $SUBFOLDER/ already exists in the repo. Files will be added or updated on your new branch only."
  ask "Continue?" || die "Cancelled. Pick another folder with --subfolder NAME."
fi

# 3. copy the project in, leaving private and generated files out
STAGE="$WORK/stage"
mkdir -p "$STAGE"
cp -R "$SRC"/. "$STAGE"/
rm -rf "$STAGE/.git"
find "$STAGE" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null
for pattern in '*.pyc' '.DS_Store' '.env' '*.key' '*.zip' 'results.json' 'results.html' \
               'usage_log.json' 'usage_log.json.tmp' 'prepared_posts.json' '*.joblib' \
               'local_claim_model*' 'bluesky_results_real.csv'; do
  find "$STAGE" -type f -name "$pattern" -delete
done
mkdir -p "$SUBFOLDER"
cp -R "$STAGE"/. "$SUBFOLDER"/

# 4. scan for keys and tokens
HITS="$(grep -rEn --binary-files=without-match \
  -e 'sk-ant-[A-Za-z0-9_-]{10,}' -e 'ghp_[A-Za-z0-9]{20,}' -e 'github_pat_[A-Za-z0-9_]{20,}' \
  -e 'AKIA[0-9A-Z]{16}' -e 'AIza[0-9A-Za-z_-]{30,}' \
  -e '(api[_-]?key|API_KEY|token|secret)["'"'"' :=]+["'"'"'][A-Za-z0-9_./+-]{24,}["'"'"']' \
  "$SUBFOLDER" 2>/dev/null | head -10)"
if [ -n "$HITS" ]; then
  echo
  echo "$HITS"
  die "Something that looks like a key or token is in the files above. Remove it, then run this again. Nothing was pushed."
fi
echo "Key scan: nothing found"

# 5. make sure git knows who you are
if [ -z "$(git config user.name)" ] || [ -z "$(git config user.email)" ]; then
  if [ "$ASSUME_YES" -eq 1 ]; then
    die "git doesn't know your name. Run: git config --global user.name \"Your Name\" and git config --global user.email \"you@example.com\""
  fi
  read -r -p "Your name for the commit: " GN
  read -r -p "Your email for the commit: " GE
  git config user.name "$GN"; git config user.email "$GE"
fi

# 6. show what will be sent
git add -A
COUNT="$(git status --short | wc -l | tr -d ' ')"
[ "$COUNT" -gt 0 ] || die "Nothing new to push. The repo already has these files."
echo
echo "Files to be added ($COUNT):"
git status --short | head -60
[ "$COUNT" -gt 60 ] && echo "... and $((COUNT - 60)) more"
echo
if [ "$DRY" -eq 1 ]; then
  echo "Dry run: nothing was committed or pushed."
  exit 0
fi
ask "Commit and push these to a new branch called $BRANCH?" || die "Cancelled. Nothing was pushed."

# 7. commit and push
git commit -q -m "Add verification tool (claim scoring, source checks, tiers, Bluesky input)" || die "Commit failed."
echo "Pushing..."
PUSH_OUT="$(git push -u origin "$BRANCH" 2>&1)"; PUSH_RC=$?
echo "$PUSH_OUT" | tail -5
if [ "$PUSH_RC" -ne 0 ]; then
  die "The push failed. Common fixes: sign in to GitHub (run 'gh auth login' if you have the GitHub CLI), use a personal access token as the password, or ask a teammate for write access."
fi

WEB="${REPO%.git}"
case "$WEB" in git@github.com:*) WEB="https://github.com/${WEB#git@github.com:}";; esac
echo
echo "Done. Your code is on the branch $BRANCH (it did not touch $BASE)."
case "$WEB" in
  https://github.com/*) echo "Open a pull request here: $WEB/compare/$BRANCH?expand=1";;
  *) echo "Open a pull request for $BRANCH on your Git host.";;
esac
