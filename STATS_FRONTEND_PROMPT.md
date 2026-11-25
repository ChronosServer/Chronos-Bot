# Prompt for Claude Code in the website repo

Before starting, copy `STATS_PLAN.md` and `STATS_API.md` from the Chronos-Bot repo into
the website repo root. Then paste everything below the line into Claude Code, opened in
the website repo.

---

We're building the **Stats** section of the Chronos website. The backend is done and
deployed: a read-only JSON API at `/api/stats/*` on the same origin. Your job is the
frontend only.

**Read these first, fully, before proposing anything:**
1. `STATS_PLAN.md`: the agreed scope, feature list and hard rules. Its decisions are
   final; don't relitigate them.
2. `STATS_API.md`: the exact API contract. It covers every endpoint, response shape,
   units, scopes/bots, time ranges, the `earliest_known` / `approximate` labelling, `/meta.settings`, badge names
   and spotlight sentence templates. Build against it exactly. Don't invent endpoints or
   fields; if something you need is missing, tell me instead of working around it.
3. This repo's `CLAUDE.md` and the existing pages/CSS/JS: the established Chronos
   design system (dark panels, teal accent, Syncopate / IBM Plex, scroll-reveal) and
   how the site is built and deployed (`deploy.sh`, `/var/www/html`). The Stats pages
   must look and feel like the rest of the Chronos site, not like the WaveStats
   reference that inspired the feature list.

**What to build (v1):**
- **Navigation:** one new top-level **"Stats ▾"** dropdown with Overview / Explorer /
  Scoreboards / Player / Compare / Milestones. Not six top-level items. Bolt map and
  World map stay as their own separate links.
- **Overview:**
  - server totals (All vs CMPs vs SMP) from `/totals`;
  - the activity heatmap and the 24-hour **server pulse clock** from `/activity`, binned
    in the viewer's local time;
  - the random **stat spotlight** from `/spotlight`;
  - the latest milestones and firsts.
- **Explorer:** search or browse any vanilla stat, with autocomplete from `/search`
  (debounced, keyboard-navigable). Show a sortable leaderboard with player heads, and a
  graph with a timeframe picker (the presets from `/meta.settings.range_presets`, plus
  `all` and custom).
- **Scoreboards:** the same, for custom objectives (`kind=scoreboard`). Mark removed
  objectives as removed.
- **Player (dossier):**
  - stats with rank of N, plus "best ranks";
  - per-stat graphs;
  - badges, using the display names from STATS_API.md;
  - milestones, firsts, and rivals with "catching up in N days".
- **Compare:** 2–6 players side by side on chosen stats (up to 20), with overlaid graphs.
- **Milestones:** the cursor-paginated feed, firsts, and rivalries.
- **Everywhere:**
  - an **All / CMPs / SMP** scope switch plus a single-server picker (from `/meta`);
  - an **include bots** toggle wherever SMP data is in view;
  - an "updated N min ago" label from `/meta.last_run_at`.

**Rules that must hold:**
- **Nothing hardcoded:** stat lists, objectives, servers and scopes all come from the
  API. New stats must appear automatically. So do the date presets, the limits
  (leaderboard/feed/search page sizes, compare player and key counts, series caps) and
  the badge thresholds: read them from `/meta.settings`, never hardcode them.
- **Pre-tracking data:** honour `effective_from`. Show "since tracking began
  (<date>)" / "since <date>", formatted from `effective_from`, when a range is clamped
  or snapped forward to the next data point (a window starting before live tracking is
  measured from the next backup or the first live snapshot). History may
  reach back to **2021**, because old world backups get imported on the server, so
  never hardcode 24 Nov 2026, and make the range picker and graphs cope with years of
  coarse history (few points between backups).
- **`earliest_known: 1` items** read as "earliest known holder" or "already reached
  before tracking", never as a dated first.
- **`approximate: 1` items** (firsts and milestones) read as "by <date>", not as an
  exact date.
- **Graphs:** step lines (values are cumulative and only change at snapshots). Smooth
  and fast, with a sensible empty state while history is short. A series can be a
  single point (a custom window entirely between two imported backups returns just the
  value known at `to`, see STATS_API.md): render it as a value, not a broken chart.
- **Speed:** a page should need one or two requests. Let the browser's HTTP cache work
  (the API sends ETags). No cache-busting params, and no polling faster than every 5
  minutes.
- **Players with no name:** show the first 8 characters of the uuid plus their Crafatar
  head.
- **Accessibility:** keyboard-accessible controls and readable contrast in the existing
  dark theme.

**How I want you to work:**
- Use the brainstorming skill first. Look at the existing site, then show me layout
  options for the Overview and Player pages as mockups before building.
- Then write a plan, then build page by page, starting with the shared pieces: API
  client, scope switch, range picker, chart component, player head/name component.
- Use the design, frontend-design and animation skills installed here for polish, but
  match the existing site's style rather than introducing a new one.
- Verify each page in a real browser against the live API before calling it done.
- If any live response doesn't match STATS_API.md, stop and tell me (the backend can be
  fixed).
