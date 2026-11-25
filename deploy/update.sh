#!/usr/bin/env bash
# Update the live Chronos-Bot checkout from GitHub (origin/master).
#
#   bash deploy/update.sh            # update (asks before pip / bot restart)
#   bash deploy/update.sh --check    # show what would change + config check; change nothing
#   bash deploy/update.sh --yes      # answer yes to every question
#   bash deploy/update.sh --discard-local
#                                    # server files that differ from git are backed up,
#                                    # then replaced by the git version (never config.json)
#
# What it does, in order (stops at the first problem, before changing anything
# where it can):
#   1. git fetch; list incoming commits and changed files; refuse if the server
#      has its own commits or modified tracked files (other than config.json).
#   2. Check config.json against the incoming repo config.json, validating
#      stats_tracking with the incoming code (deploy/check_config.py). config.json
#      is NEVER written: it is backed up, set aside for the pull, and put back
#      byte for byte. Missing keys are reported for you to add by hand.
#   3. Stop the stats snapshot timer (if stats code changed), fast-forward,
#      apply the sparse checkout (tests/, docs/, ... are not kept on the server).
#   4. requirements.txt changed: pip --dry-run, then install if you say yes.
#   5. Stats code changed: one snapshot run by hand (migrations), restart the API
#      if api/ or sql/ changed, check it answers, start the timer again.
#   6. Bot code changed: offer to restart the Discord bot (start.sh restarts it).
#   7. systemd/nginx files changed: print what to copy (never installed by itself).
# Everything is logged to /opt/chronos/update-logs/ (download it with WinSCP).

set -uo pipefail

PY=${CHRONOS_PY:-/opt/python/bin/python3}
BRANCH=${CHRONOS_BRANCH:-master}
REMOTE=origin
LOG_DIR=${CHRONOS_LOG_DIR:-/opt/chronos/update-logs}
BACKUP_DIR=${CHRONOS_BACKUP_DIR:-/opt/chronos/config-backups}
LOCK=${CHRONOS_LOCK:-/run/chronos-update.lock}
TIMER=chronos-stats-snapshot.timer
SNAP_SERVICE=chronos-stats-snapshot.service
API_SERVICE=chronos-stats-api.service
API_URL=http://127.0.0.1:8765/api/stats/meta
# Never checked out on the server (git sparse-checkout, non-cone patterns).
SPARSE_PATTERNS=(
  '/*'
  '!/tests/'
  '!/docs/'
  '!/examples/'
  '!/pytest.ini'
  '!/requirements-dev.txt'
  '!/start.bat'
)

# The whole script is one function, read completely before it runs: the pull
# may replace this very file.
main() {
  local CHECK=0 YES=0 DISCARD=0
  for a in "$@"; do
    case "$a" in
      --check) CHECK=1 ;;
      --yes|-y) YES=1 ;;
      --discard-local) DISCARD=1 ;;
      -h|--help) sed -n '2,30p' "$0"; return 0 ;;
      *) echo "unknown option: $a (see --help)"; return 2 ;;
    esac
  done

  local REPO STAMP LOG
  REPO=$(cd "$(dirname "$0")/.." && pwd)
  STAMP=$(date -u +%Y%m%d-%H%M%S)
  mkdir -p "$LOG_DIR" "$BACKUP_DIR"
  LOG="$LOG_DIR/update-$STAMP.log"
  exec > >(tee -a "$LOG") 2>&1
  echo "== Chronos-Bot update $STAMP (UTC), repo $REPO$([ $CHECK = 1 ] && echo ', CHECK ONLY')"
  echo "   log: $LOG"

  cd "$REPO" || return 1
  [ "$(id -u)" = 0 ] || { echo "run as root (systemctl, /run)"; return 1; }
  exec 9>"$LOCK"
  if ! flock -n 9; then echo "another update is running"; return 1; fi
  [ -d .git ] || { echo "$REPO is not a git checkout; see TEMP_INFO.md (one-time setup)"; return 1; }
  local cur
  cur=$(git symbolic-ref --short HEAD 2>/dev/null)
  [ "$cur" = "$BRANCH" ] || { echo "on branch '$cur', expected $BRANCH"; return 1; }

  # ---------------------------------------------------------------- 1. fetch
  echo; echo "== 1. fetch"
  git fetch --quiet "$REMOTE" "$BRANCH" || { echo "git fetch failed"; return 1; }
  local OLD NEW
  OLD=$(git rev-parse HEAD)
  NEW=$(git rev-parse "$REMOTE/$BRANCH")
  if ! git merge-base --is-ancestor HEAD "$NEW"; then
    echo "the server has commits that $REMOTE/$BRANCH doesn't (git log $NEW..HEAD):"
    git log --oneline "$NEW..HEAD"
    echo "refusing: sort that out by hand"; return 1
  fi
  local CHANGED=""
  if [ "$OLD" = "$NEW" ]; then
    echo "already up to date at $(git log -1 --format='%h %s')"
  else
    echo "incoming commits:"; git log --oneline "$OLD..$NEW" | sed 's/^/  /'
    CHANGED=$(git diff --name-only "$OLD" "$NEW")
    echo "changed files:"; echo "$CHANGED" | sed 's/^/  /'
  fi

  # Tracked files that differ on the server (config.json is expected to).
  # Harmless, so git's version is used without asking (nothing is lost):
  # a file already identical to the incoming version (copied with WinSCP),
  # one that differs from git only in line endings (Windows CRLF from a
  # WinSCP copy) from the current or the incoming version, or a deleted one.
  local DIRTY="" SAME_TRACKED="" f n_same=0
  while IFS= read -r f; do
    [ -z "$f" ] || [ "$f" = config.json ] && continue
    if [ ! -e "$f" ] \
        || [ "$(git hash-object "$f")" = "$(git rev-parse "$NEW:$f" 2>/dev/null)" ] \
        || git diff --quiet --ignore-cr-at-eol HEAD -- "$f" 2>/dev/null \
        || git diff --quiet --ignore-cr-at-eol "$NEW" -- "$f" 2>/dev/null; then
      SAME_TRACKED+="$f"$'\n'; n_same=$((n_same + 1))
    else
      DIRTY+="$f"$'\n'
    fi
  done < <(git status --porcelain --untracked-files=no | awk '{print $NF}')
  [ $n_same -gt 0 ] && echo "$n_same file(s) differ from git only in line endings, are missing, or already match the new version: git's version is used"
  # Untracked files where an incoming commit adds a file: the pull would refuse.
  # One already identical to the incoming file (e.g. copied by hand) is just removed.
  local BLOCKING="" SAME=""
  if [ "$OLD" != "$NEW" ]; then
    while IFS= read -r f; do
      [ -n "$f" ] && [ -e "$f" ] && ! git ls-files --error-unmatch "$f" >/dev/null 2>&1 || continue
      if [ "$(git hash-object "$f")" = "$(git rev-parse "$NEW:$f")" ]; then
        SAME+="$f"$'\n'
      else
        BLOCKING+="$f"$'\n'
      fi
    done < <(git diff --name-only --diff-filter=A "$OLD" "$NEW")
  fi
  local REFUSE=0
  if [ -n "$DIRTY$BLOCKING" ]; then
    echo; echo "server files that differ from git (not config.json):"
    printf '%s\n%s' "$DIRTY" "$BLOCKING" | sed '/^$/d; s/^/  /'
    if [ $DISCARD = 0 ]; then
      echo "refusing to overwrite them. Look at them (git diff <file>); if the git"
      echo "version is the right one, rerun with --discard-local (they are backed up first)."
      REFUSE=1
      [ $CHECK = 1 ] || return 1
    fi
  fi

  # ---------------------------------------------------------------- 2. config
  echo; echo "== 2. config.json check (against the incoming code and config)"
  local TMP
  TMP=$(mktemp -d)
  git archive "$NEW" api/__init__.py api/settings.py deploy/check_config.py config.json \
    | tar -x -C "$TMP" || { echo "can't extract the incoming checker"; rm -rf "$TMP"; return 1; }
  "$PY" "$TMP/deploy/check_config.py" "$REPO/config.json" "$TMP/config.json"
  local cfg_rc=$?
  rm -rf "$TMP"
  if [ $cfg_rc = 2 ]; then echo "stopping: config.json must be fixed first (nothing changed)"; return 1; fi

  if [ $CHECK = 1 ]; then
    echo; echo "== check only: nothing changed$([ $REFUSE = 1 ] && echo ' (an update would refuse: see above)')"
    return $(( REFUSE || cfg_rc == 2 ))
  fi
  if [ "$OLD" = "$NEW" ]; then apply_sparse; echo; echo "== done (nothing to update)"; return 0; fi

  has() { echo "$CHANGED" | grep -Eq "$1"; }
  local STATS=0 API=0 SQL=0 BOT=0 REQ=0
  has '^(bot|sql|api|dictionaries)/|^player_merges\.json$' && STATS=1
  has '^(api|sql)/|^player_merges\.json$' && API=1
  has '^sql/' && SQL=1
  has '^(main\.py|commands/|Chronos-Library/)' && BOT=1
  has '^requirements\.txt$' && REQ=1

  # ---------------------------------------------------------------- 3. pull
  echo; echo "== 3. update the code"
  local TIMER_WAS=inactive
  if [ $STATS = 1 ]; then
    TIMER_WAS=$(systemctl is-active "$TIMER")
    systemctl stop "$TIMER"
    echo "stopped $TIMER (was $TIMER_WAS); waiting for a running snapshot to finish..."
    local i=0
    while systemctl is-active --quiet "$SNAP_SERVICE"; do
      i=$((i + 1)); [ $i -gt 120 ] && { echo "snapshot still running after 4 min; stopping"; return 1; }
      sleep 2
    done
  fi

  local CFG_BAK="$BACKUP_DIR/config.json.$STAMP"
  cp -p config.json "$CFG_BAK" || { echo "can't back up config.json"; return 1; }
  echo "config.json backed up: $CFG_BAK"
  if [ -n "$DIRTY$BLOCKING" ]; then
    local LB="$BACKUP_DIR/local-files.$STAMP"
    mkdir -p "$LB"
    printf '%s\n%s' "$DIRTY" "$BLOCKING" | sed '/^$/d' | while IFS= read -r f; do
      [ -e "$f" ] && mkdir -p "$LB/$(dirname "$f")" && cp -p "$f" "$LB/$f"
    done
    echo "differing server files backed up to $LB"
    echo "$DIRTY" | sed '/^$/d' | xargs -r -d '\n' git checkout --
    echo "$BLOCKING" | sed '/^$/d' | xargs -r -d '\n' rm -f --
  fi
  # identical to the incoming files: nothing is lost
  echo "$SAME" | sed '/^$/d' | xargs -r -d '\n' rm -f --
  echo "$SAME_TRACKED" | sed '/^$/d' | xargs -r -d '\n' git checkout --
  git checkout -- config.json 2>/dev/null   # set aside; restored from the backup below
  if ! git merge --ff-only --quiet "$NEW"; then
    cp -p "$CFG_BAK" config.json
    echo "git merge failed; config.json restored; nothing else changed"
    [ $STATS = 1 ] && [ "$TIMER_WAS" = active ] && systemctl start "$TIMER"
    return 1
  fi
  cp -p "$CFG_BAK" config.json
  cmp -s "$CFG_BAK" config.json && echo "config.json restored (unchanged)" \
    || { echo "!! config.json differs from its backup: restore $CFG_BAK by hand"; return 1; }
  echo "now at $(git log -1 --format='%h %s')"
  apply_sparse

  # ---------------------------------------------------------------- 4. pip
  if [ $REQ = 1 ]; then
    echo; echo "== 4. requirements.txt changed"
    "$PY" -m pip install --dry-run -r requirements.txt | grep -E '^Would install' || echo "  (nothing to install)"
    if ask "install these packages now?" $YES; then
      "$PY" -m pip install -r requirements.txt || echo "!! pip install failed; see above"
    else
      echo "skipped: run '$PY -m pip install -r requirements.txt' yourself"
    fi
  fi

  # ---------------------------------------------------------------- 5. stats
  if [ $STATS = 1 ]; then
    echo; echo "== 5. stats: one snapshot run with the new code$([ $SQL = 1 ] && echo ' (applies migrations)')"
    if ! "$PY" bot/snapshot.py; then
      echo "!! the snapshot failed with the new code. The timer stays STOPPED."
      echo "   Send this log. To go back to the old code (config.json kept):"
      echo "     cd $REPO && git reset --hard $OLD && cp -p $CFG_BAK config.json"
      echo "     $PY bot/snapshot.py && systemctl restart $API_SERVICE && systemctl start $TIMER"
      return 1
    fi
    if [ $API = 1 ]; then
      systemctl restart "$API_SERVICE"
      echo "restarted $API_SERVICE; waiting for it..."
      local ok=0
      for i in $(seq 1 30); do
        if out=$(curl -fsS "$API_URL" 2>/dev/null); then ok=1; break; fi
        sleep 1
      done
      if [ $ok = 1 ]; then
        echo "API ok: $("$PY" -c 'import json,sys; j=json.load(sys.stdin); print("data_version", j["data_version"], "servers", [s["key"] for s in j["servers"]])' <<<"$out")"
      else
        echo "!! API not answering at $API_URL; see: journalctl -u $API_SERVICE -n 50"
      fi
    fi
    if [ "$TIMER_WAS" = active ]; then
      systemctl start "$TIMER" && echo "started $TIMER again"
    else
      echo "$TIMER was not running before; left stopped"
    fi
  fi

  # ---------------------------------------------------------------- 6. bot
  if [ $BOT = 1 ]; then
    echo; echo "== 6. Discord bot code changed"
    restart_bot "$REPO" $YES
  fi

  # ---------------------------------------------------------------- 7. units
  local f
  for f in $(echo "$CHANGED" | grep -E '^deploy/(systemd|nginx)/' || true); do
    case "$f" in
      deploy/systemd/*)
        echo; echo "!! $f changed: compare with the installed one and install by hand:"
        echo "   diff /etc/systemd/system/$(basename "$f") $REPO/$f"
        echo "   cp $REPO/$f /etc/systemd/system/ && systemctl daemon-reload" ;;
      deploy/nginx/*)
        echo; echo "!! $f changed: merge it into /etc/nginx/sites-enabled/default by hand, then nginx -t && systemctl reload nginx" ;;
    esac
  done

  echo; echo "== done: $(git log -1 --format='%h %s')"
  [ $cfg_rc = 1 ] && echo "   (config.json has MISSING keys: see step 2)"
  return 0
}

apply_sparse() {
  # The classic way (any git version; `sparse-checkout set --no-cone` differs
  # between git releases, and Debian bullseye has 2.30).
  git config core.sparseCheckout true
  git config core.sparseCheckoutCone false
  printf '%s\n' "${SPARSE_PATTERNS[@]}" > "$(git rev-parse --git-path info/sparse-checkout)"
  if git read-tree -mu HEAD; then
    echo "sparse checkout: tests/, docs/ and other dev-only files are not kept here"
  else
    echo "!! sparse checkout not applied (git read-tree failed; harmless, files stay)"
  fi
}

ask() {  # ask "question" <yes-flag>
  [ "$2" = 1 ] && { echo "$1 yes (--yes)"; return 0; }
  local r
  read -r -p "$1 [y/N] " r </dev/tty || return 1
  [[ "$r" =~ ^[Yy] ]]
}

restart_bot() {  # the bot runs `python3 main.py` inside start.sh's restart loop
  local repo=$1 yes=$2 pid ppid pcmd found=""
  for pid in $(pgrep -f 'python3? main\.py' || true); do
    [ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" = "$repo" ] && found=$pid
  done
  if [ -z "$found" ]; then
    echo "no running 'python3 main.py' from $repo found: start the bot yourself (sh start.sh in screen/tmux)"
    return
  fi
  ppid=$(ps -o ppid= -p "$found" | tr -d ' ')
  pcmd=$(ps -o args= -p "$ppid" 2>/dev/null)
  echo "bot: pid $found, parent: $pcmd"
  if [[ "$pcmd" == *start.sh* ]]; then
    if ask "restart the bot now? (start.sh starts it again 5 s later)" "$yes"; then
      kill -TERM "$found" && echo "bot stopped; start.sh restarts it in ~5 s"
    else
      echo "not restarted. Later: kill $found (start.sh restarts it), or in Discord !!reload"
      echo "(!!reload only reloads commands/*.py, not main.py or Chronos-Library/)"
    fi
  else
    echo "the bot isn't under start.sh: restart it yourself in its screen/tmux session"
  fi
}

main "$@"
exit $?
