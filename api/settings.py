"""Stats tuning: defaults and loader for config.json's `stats_tracking` block.

The single place every stats tuning value lives. Every key is optional: a
missing key falls back to DEFAULT_CONFIG below, so a config.json without any
tuning keys behaves exactly like the old hardcoded values. Objects merge deep
(overriding one rivalry value keeps the other defaults); lists and scalars
replace. Maps keyed by user-chosen names (servers, scope_groups,
api.range_presets) replace the default map as a whole, so a name can be removed.

load_settings() validates types and ranges and raises ValueError naming the
bad key path ("stats_tracking.rivalries.max_gap_ratio must be a number in
(0, 1]"). Snapshot and API fail loudly on a bad config; there is no silent
fallback. Keys starting with "_" are ignored (comments).

Who reads it:
  * bot/snapshot.py builds Settings from the config it loads, every run, and
    passes it down to derive (changes take effect on the next snapshot run);
  * the API builds it once, from the config.json api/db.py reads db_path
    from (api_settings(), cached; changes need an API restart). With no
    config file it uses DEFAULTS.

Pure stdlib on purpose: the snapshot imports it too.
"""
import copy
import hashlib
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

ROOT = "stats_tracking"
DAY = 86400

DEFAULT_CONFIG = {
    "db_path": None,
    "excluded_servers": [],
    "servers": {},
    # extra scope name -> servers.<key>.group
    "scope_groups": {"cmps": "cmp"},
    # the play-time stat (ticks): a change marks a player active, and
    # badges.veteran_hours is measured on it
    "activity_stat": ["minecraft:custom", "minecraft:play_time"],
    "milestones": {
        "count_categories": ["minecraft:mined", "minecraft:used", "minecraft:crafted",
                             "minecraft:broken", "minecraft:picked_up", "minecraft:dropped",
                             "minecraft:killed", "minecraft:killed_by"],
        "count_thresholds": [1000, 10000, 100000, 1000000, 10000000],
        # stored threshold = threshold * scale (72000 ticks per hour, 100000 cm per km)
        "per_key": [
            {"category": "minecraft:custom", "key": "minecraft:play_time",
             "thresholds": [100, 500, 1000, 5000, 10000], "scale": 72000},
            {"category": "minecraft:custom", "key": "minecraft:walk_one_cm",
             "thresholds": [100, 1000, 10000], "scale": 100000},
            {"category": "minecraft:custom", "key": "minecraft:deaths",
             "thresholds": [100, 1000, 10000], "scale": 1},
        ],
    },
    "badges": {
        "firsts": [1, 10, 50],
        "top1": [1, 10, 50],
        "big_numbers": [1000000, 10000000],
        "category_tiers": [100000, 1000000, 10000000],
        "veteran_hours": 1000,
        "regular": {"days": 20, "window_days": 30},
        "night_owl": {"min_buckets": 40, "window_days": 30, "share": 0.5,
                      "start_hour": 0, "end_hour": 6},
    },
    "rivalries": {"min_value": 1000, "max_gap_ratio": 0.02, "active_days": 7,
                  "per_player": 5, "per_key": 3},
    "spotlight": {
        "outlier": {"ratio": 10, "min_holders": 10},
        "rare": {"max_holders": 3},
        "recent_milestone_days": 7,
        "recent_first_days": 30,
        "mover": {"top": 20, "min_growth": 0.5, "min_delta": 1000, "window_days": 7},
    },
    "mojang": {"max_calls": 60, "timeout_s": 3, "max_seconds": 60, "retry_after_days": 7},
    # mirror_prefix: objective names matching it are every-scoreboard copies of
    # vanilla stats even when no bundled dictionary lists them ("" = off)
    "snapshot": {"dictionary_cache_hours": 24, "dictionary_timeout_s": 15,
                 "mirror_prefix": "^(m|u|c|b|p|d|k|kb|z)-",
                 # stat keys that aren't real items/blocks (in every category):
                 # never stored; already stored rows are deleted once
                 "ignored_stat_keys": ["minecraft:air", "minecraft:cave_air",
                                       "minecraft:void_air"]},
    "api": {
        "range_presets": {"1d": 86400, "1w": 604800, "1m": 2592000, "6m": 15724800,
                          "1y": 31536000},
        "activity_bucket_s": 900,
        "series_max_points": 500,
        "leaderboard_max_limit": 200,
        "feed_max_limit": 200,
        "search_max_limit": 50,
        "compare_min_players": 2,
        "compare_max_players": 6,
        "compare_max_keys": 20,
        "series_max_players": 6,
        "player_milestones_limit": 50,
        "cache_max_bytes": 134217728,
        "cache_max_body_bytes": 8388608,
        "cache_max_entries": 2048,
        "cache_max_age_s": 60,
        "cache_stale_while_revalidate_s": 900,
        "gzip_min_bytes": 1000,
        "sqlite_mmap_bytes": 268435456,
        "sqlite_cache_kib": 65536,
    },
}

# Per-server entry defaults (filters_bots: True for key "smp" only).
SERVER_DEFAULTS = {"track_stats": True, "track_scoreboards": True}
SERVER_REQUIRED = ("label", "group")

# Name-keyed maps: an override replaces the default map whole.
_REPLACE_WHOLE = {f"{ROOT}.servers", f"{ROOT}.scope_groups", f"{ROOT}.api.range_presets"}


# ------------------------------------------------------------------ dataclasses

@dataclass(frozen=True)
class ServerSettings:
    label: str
    group: str
    track_stats: bool
    track_scoreboards: bool
    filters_bots: bool


@dataclass(frozen=True)
class KeyMilestone:
    category: str
    key: str
    thresholds: Tuple[int, ...]
    scale: int

    @property
    def rule_key(self) -> str:
        return f"key:{self.category}/{self.key}"

    @property
    def stored_thresholds(self) -> list:
        return [t * self.scale for t in self.thresholds]


@dataclass(frozen=True)
class Milestones:
    count_categories: Tuple[str, ...]
    count_thresholds: Tuple[int, ...]
    per_key: Tuple[KeyMilestone, ...]

    def rules(self) -> list:
        """milestone_rules rows: (rule_key, category, stat_key|None, thresholds)."""
        out = [(f"cat:{c}", c, None, list(self.count_thresholds))
               for c in self.count_categories]
        out += [(r.rule_key, r.category, r.key, r.stored_thresholds) for r in self.per_key]
        return out


@dataclass(frozen=True)
class Regular:
    days: int
    window_days: float


@dataclass(frozen=True)
class NightOwl:
    min_buckets: int
    window_days: float
    share: float
    start_hour: int
    end_hour: int


@dataclass(frozen=True)
class Badges:
    firsts: Tuple[int, ...]
    top1: Tuple[int, ...]
    big_numbers: Tuple[int, ...]
    category_tiers: Tuple[int, ...]
    veteran_hours: float
    regular: Regular
    night_owl: NightOwl


@dataclass(frozen=True)
class Rivalries:
    min_value: int
    max_gap_ratio: float
    active_days: float
    per_player: int
    per_key: int


@dataclass(frozen=True)
class Outlier:
    ratio: float
    min_holders: int


@dataclass(frozen=True)
class Rare:
    max_holders: int


@dataclass(frozen=True)
class Mover:
    top: int
    min_growth: float
    min_delta: int
    window_days: float


@dataclass(frozen=True)
class Spotlight:
    outlier: Outlier
    rare: Rare
    recent_milestone_days: float
    recent_first_days: float
    mover: Mover


@dataclass(frozen=True)
class Mojang:
    max_calls: int
    timeout_s: float
    max_seconds: float
    retry_after_days: float


@dataclass(frozen=True)
class Snapshot:
    dictionary_cache_hours: float
    dictionary_timeout_s: float
    mirror_prefix: str
    ignored_stat_keys: Tuple[str, ...]


@dataclass(frozen=True)
class Api:
    range_presets: Mapping[str, int]
    activity_bucket_s: int
    series_max_points: int
    leaderboard_max_limit: int
    feed_max_limit: int
    search_max_limit: int
    compare_min_players: int
    compare_max_players: int
    compare_max_keys: int
    series_max_players: int
    player_milestones_limit: int
    cache_max_bytes: int
    cache_max_body_bytes: int
    cache_max_entries: int
    cache_max_age_s: int
    cache_stale_while_revalidate_s: int
    gzip_min_bytes: int
    sqlite_mmap_bytes: int
    sqlite_cache_kib: int


@dataclass(frozen=True)
class Settings:
    db_path: Optional[str]
    excluded_servers: Tuple[str, ...]
    servers: Mapping[str, ServerSettings]
    scope_groups: Mapping[str, str]
    activity_stat: Tuple[str, str]
    milestones: Milestones
    badges: Badges
    rivalries: Rivalries
    spotlight: Spotlight
    mojang: Mojang
    snapshot: Snapshot
    api: Api

    def public(self) -> dict:
        """What /meta exposes as "settings" (JSON-able)."""
        a, b = self.api, self.badges
        return {
            "range_presets": dict(a.range_presets),
            "activity_bucket_s": a.activity_bucket_s,
            "limits": {"leaderboard": a.leaderboard_max_limit, "feed": a.feed_max_limit,
                       "search": a.search_max_limit, "series_players": a.series_max_players,
                       "series_points": a.series_max_points,
                       "player_milestones": a.player_milestones_limit},
            "compare": {"min_players": a.compare_min_players,
                        "max_players": a.compare_max_players,
                        "max_keys": a.compare_max_keys},
            "badges": {"firsts": list(b.firsts), "top1": list(b.top1),
                       "big_numbers": list(b.big_numbers),
                       "category_tiers": list(b.category_tiers),
                       "categories": list(self.milestones.count_categories),
                       "veteran_hours": b.veteran_hours,
                       "regular": {"days": b.regular.days,
                                   "window_days": b.regular.window_days},
                       "night_owl": {"min_buckets": b.night_owl.min_buckets,
                                     "window_days": b.night_owl.window_days,
                                     "share": b.night_owl.share,
                                     "start_hour": b.night_owl.start_hour,
                                     "end_hour": b.night_owl.end_hour}},
        }


def days_s(days) -> int:
    """A days setting in whole seconds."""
    return int(round(days * DAY))


# ------------------------------------------------------------------ merge

def _merge(default, override, path):
    if override is None:
        return copy.deepcopy(default)
    if not isinstance(override, dict):
        raise ValueError(f"{path} must be an object")
    out = copy.deepcopy(default)
    for k, v in override.items():
        if isinstance(k, str) and k.startswith("_"):
            continue
        if k not in default:
            raise ValueError(f"{path}.{k} is not a known setting")
        sub = f"{path}.{k}"
        if isinstance(default[k], dict) and sub not in _REPLACE_WHOLE:
            out[k] = _merge(default[k], v, sub)
        else:
            out[k] = v
    return out


# ------------------------------------------------------------------ validators

def _range_text(gt, ge, lt, le):
    lo = (f"({gt}" if gt is not None else f"[{ge}") if (gt is not None or ge is not None) else None
    hi = (f"{lt})" if lt is not None else f"{le}]") if (lt is not None or le is not None) else None
    if lo and hi:
        return f" in {lo}, {hi}"
    if gt is not None:
        return f" > {gt}"
    if ge is not None:
        return f" >= {ge}"
    if lt is not None:
        return f" < {lt}"
    if le is not None:
        return f" <= {le}"
    return ""


def _num(v, path, integer=False, gt=None, ge=None, lt=None, le=None):
    if integer:
        ok = isinstance(v, int) and not isinstance(v, bool)
    else:
        ok = (isinstance(v, (int, float)) and not isinstance(v, bool)
              and math.isfinite(v))
    if ok:
        ok = ((gt is None or v > gt) and (ge is None or v >= ge)
              and (lt is None or v < lt) and (le is None or v <= le))
    if not ok:
        kind = "an integer" if integer else "a number"
        raise ValueError(f"{path} must be {kind}{_range_text(gt, ge, lt, le)}")
    return v


def _int(v, path, **kw):
    return _num(v, path, integer=True, **kw)


def _bool(v, path):
    if not isinstance(v, bool):
        raise ValueError(f"{path} must be true or false")
    return v


def _str(v, path):
    if not isinstance(v, str) or not v:
        raise ValueError(f"{path} must be a non-empty string")
    return v


def _regex(v, path):
    """A regular expression string ("" allowed = off)."""
    if not isinstance(v, str):
        raise ValueError(f"{path} must be a string (a regular expression, or \"\")")
    try:
        re.compile(v)
    except re.error as e:
        raise ValueError(f"{path} is not a valid regular expression: {e}")
    return v


def _obj(v, path):
    if not isinstance(v, dict):
        raise ValueError(f"{path} must be an object")
    return v


def _int_list(v, path, ge=1, increasing=True, nonempty=False):
    desc = ("a" + (" non-empty" if nonempty else "") + " list of "
            + ("strictly increasing " if increasing else "distinct ") + f"integers >= {ge}")
    ok = isinstance(v, list) and (v or not nonempty) and all(
        isinstance(x, int) and not isinstance(x, bool) and x >= ge for x in v)
    if ok:
        ok = (all(a < b for a, b in zip(v, v[1:])) if increasing
              else len(set(v)) == len(v))
    if not ok:
        raise ValueError(f"{path} must be {desc}")
    return tuple(v)


def _str_list(v, path, distinct=True):
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v) or (
            distinct and len(set(v)) != len(v)):
        raise ValueError(f"{path} must be a list of distinct non-empty strings")
    return tuple(v)


def _no_comments(d):
    """A whole-replaced map without its "_"-prefixed (comment) keys."""
    return {k: v for k, v in d.items() if not (isinstance(k, str) and k.startswith("_"))}


def _known_keys(d, allowed, path):
    for k in d:
        if isinstance(k, str) and k.startswith("_"):
            continue
        if k not in allowed:
            raise ValueError(f"{path}.{k} is not a known setting")


# ------------------------------------------------------------------ builders

def _servers(raw, path):
    _obj(raw, path)
    out = {}
    for key, entry in _no_comments(raw).items():
        p = f"{path}.{key}"
        if key == "all":
            raise ValueError(f"{p}: the server key 'all' is reserved")
        _obj(entry, p)
        _known_keys(entry, (*SERVER_REQUIRED, *SERVER_DEFAULTS, "filters_bots"), p)
        e = {**SERVER_DEFAULTS, "filters_bots": key == "smp", **entry}
        for f in SERVER_REQUIRED:
            if f not in e:
                raise ValueError(f"{p}.{f} is required")
        out[key] = ServerSettings(
            label=_str(e["label"], f"{p}.label"), group=_str(e["group"], f"{p}.group"),
            track_stats=_bool(e["track_stats"], f"{p}.track_stats"),
            track_scoreboards=_bool(e["track_scoreboards"], f"{p}.track_scoreboards"),
            filters_bots=_bool(e["filters_bots"], f"{p}.filters_bots"))
    return MappingProxyType(out)


def _scope_groups(raw, path, servers):
    _obj(raw, path)
    out = {}
    for name, group in _no_comments(raw).items():
        p = f"{path}.{name}"
        if not name or name == "all" or name in servers:
            raise ValueError(f"{p}: a scope name must not be empty, 'all' or a server key")
        out[name] = _str(group, p)
    return MappingProxyType(out)


def _milestones(raw, path):
    cats = _str_list(raw["count_categories"], f"{path}.count_categories")
    thr = _int_list(raw["count_thresholds"], f"{path}.count_thresholds")
    pk = raw["per_key"]
    if not isinstance(pk, list):
        raise ValueError(f"{path}.per_key must be a list")
    rules, seen = [], set()
    for i, r in enumerate(pk):
        p = f"{path}.per_key[{i}]"
        _obj(r, p)
        _known_keys(r, ("category", "key", "thresholds", "scale"), p)
        for f in ("category", "key", "thresholds"):
            if f not in r:
                raise ValueError(f"{p}.{f} is required")
        rule = KeyMilestone(
            category=_str(r["category"], f"{p}.category"), key=_str(r["key"], f"{p}.key"),
            thresholds=_int_list(r["thresholds"], f"{p}.thresholds", nonempty=True),
            scale=_int(r.get("scale", 1), f"{p}.scale", ge=1))
        if rule.rule_key in seen:
            raise ValueError(f"{p}: duplicate rule for {rule.category} / {rule.key}")
        seen.add(rule.rule_key)
        rules.append(rule)
    return Milestones(cats, thr, tuple(rules))


def _badges(raw, path):
    reg, owl = raw["regular"], raw["night_owl"]
    _obj(reg, f"{path}.regular")
    _obj(owl, f"{path}.night_owl")
    rp, op = f"{path}.regular", f"{path}.night_owl"
    window = _num(reg["window_days"], f"{rp}.window_days", gt=0)
    regular = Regular(days=_int(reg["days"], f"{rp}.days", ge=1), window_days=window)
    if regular.days > window:
        raise ValueError(f"{rp}.days must be <= {rp}.window_days")
    start = _int(owl["start_hour"], f"{op}.start_hour", ge=0, le=23)
    end = _int(owl["end_hour"], f"{op}.end_hour", ge=1, le=24)
    if start >= end:
        raise ValueError(f"{op}.start_hour must be < {op}.end_hour")
    night = NightOwl(min_buckets=_int(owl["min_buckets"], f"{op}.min_buckets", ge=1),
                     window_days=_num(owl["window_days"], f"{op}.window_days", gt=0),
                     share=_num(owl["share"], f"{op}.share", gt=0, lt=1),
                     start_hour=start, end_hour=end)
    return Badges(
        firsts=_int_list(raw["firsts"], f"{path}.firsts"),
        top1=_int_list(raw["top1"], f"{path}.top1"),
        big_numbers=_int_list(raw["big_numbers"], f"{path}.big_numbers"),
        category_tiers=_int_list(raw["category_tiers"], f"{path}.category_tiers"),
        veteran_hours=_num(raw["veteran_hours"], f"{path}.veteran_hours", gt=0),
        regular=regular, night_owl=night)


def _spotlight(raw, path):
    o, r, m = raw["outlier"], raw["rare"], raw["mover"]
    for name, v in (("outlier", o), ("rare", r), ("mover", m)):
        _obj(v, f"{path}.{name}")
    return Spotlight(
        outlier=Outlier(ratio=_num(o["ratio"], f"{path}.outlier.ratio", gt=0),
                        min_holders=_int(o["min_holders"], f"{path}.outlier.min_holders", ge=1)),
        rare=Rare(max_holders=_int(r["max_holders"], f"{path}.rare.max_holders", ge=1)),
        recent_milestone_days=_num(raw["recent_milestone_days"],
                                   f"{path}.recent_milestone_days", gt=0),
        recent_first_days=_num(raw["recent_first_days"], f"{path}.recent_first_days", gt=0),
        mover=Mover(top=_int(m["top"], f"{path}.mover.top", ge=1),
                    min_growth=_num(m["min_growth"], f"{path}.mover.min_growth", ge=0),
                    min_delta=_int(m["min_delta"], f"{path}.mover.min_delta", ge=0),
                    window_days=_num(m["window_days"], f"{path}.mover.window_days", gt=0)))


def _api(raw, path):
    presets = _no_comments(_obj(raw["range_presets"], f"{path}.range_presets"))
    if not presets:
        raise ValueError(f"{path}.range_presets must not be empty")
    for name, secs in presets.items():
        p = f"{path}.range_presets.{name}"
        if not name or name == "all":
            raise ValueError(f"{p}: 'all' is built in; preset names must not be empty or 'all'")
        _int(secs, p, ge=1)
    i = {}
    mins = {"series_max_points": 2, "compare_min_players": 1, "sqlite_cache_kib": 1,
            "cache_max_bytes": 0, "cache_max_body_bytes": 0, "cache_max_entries": 0,
            "cache_max_age_s": 0, "cache_stale_while_revalidate_s": 0,
            "gzip_min_bytes": 0, "sqlite_mmap_bytes": 0}
    for k, v in raw.items():
        if k != "range_presets":
            i[k] = _int(v, f"{path}.{k}", ge=mins.get(k, 1))
    if i["compare_max_players"] < i["compare_min_players"]:
        raise ValueError(f"{path}.compare_max_players must be >= {path}.compare_min_players")
    return Api(range_presets=MappingProxyType(dict(presets)), **i)


def load_settings(stats_tracking_cfg: Optional[dict]) -> Settings:
    raw = _merge(DEFAULT_CONFIG, stats_tracking_cfg, ROOT)
    p = ROOT
    db_path = raw["db_path"]
    if db_path is not None:
        _str(db_path, f"{p}.db_path")
    servers = _servers(raw["servers"], f"{p}.servers")
    act = raw["activity_stat"]
    if not (isinstance(act, list) and len(act) == 2 and all(isinstance(x, str) and x for x in act)):
        raise ValueError(f"{p}.activity_stat must be [category, stat_key]")
    rv, mj, sn = raw["rivalries"], raw["mojang"], raw["snapshot"]
    return Settings(
        db_path=db_path,
        excluded_servers=_str_list(raw["excluded_servers"], f"{p}.excluded_servers"),
        servers=servers,
        scope_groups=_scope_groups(raw["scope_groups"], f"{p}.scope_groups", servers),
        activity_stat=(act[0], act[1]),
        milestones=_milestones(raw["milestones"], f"{p}.milestones"),
        badges=_badges(raw["badges"], f"{p}.badges"),
        rivalries=Rivalries(
            min_value=_int(rv["min_value"], f"{p}.rivalries.min_value", ge=0),
            max_gap_ratio=_num(rv["max_gap_ratio"], f"{p}.rivalries.max_gap_ratio", gt=0, le=1),
            active_days=_num(rv["active_days"], f"{p}.rivalries.active_days", gt=0),
            per_player=_int(rv["per_player"], f"{p}.rivalries.per_player", ge=1),
            per_key=_int(rv["per_key"], f"{p}.rivalries.per_key", ge=1)),
        spotlight=_spotlight(raw["spotlight"], f"{p}.spotlight"),
        mojang=Mojang(
            max_calls=_int(mj["max_calls"], f"{p}.mojang.max_calls", ge=0),
            timeout_s=_num(mj["timeout_s"], f"{p}.mojang.timeout_s", gt=0),
            max_seconds=_num(mj["max_seconds"], f"{p}.mojang.max_seconds", gt=0),
            retry_after_days=_num(mj["retry_after_days"], f"{p}.mojang.retry_after_days", gt=0)),
        snapshot=Snapshot(
            dictionary_cache_hours=_num(sn["dictionary_cache_hours"],
                                        f"{p}.snapshot.dictionary_cache_hours", ge=0),
            dictionary_timeout_s=_num(sn["dictionary_timeout_s"],
                                      f"{p}.snapshot.dictionary_timeout_s", gt=0),
            mirror_prefix=_regex(sn["mirror_prefix"], f"{p}.snapshot.mirror_prefix"),
            ignored_stat_keys=tuple(_str_list(sn["ignored_stat_keys"],
                                              f"{p}.snapshot.ignored_stat_keys"))),
        api=_api(raw["api"], f"{p}.api"),
    )


DEFAULTS = load_settings(None)


# ------------------------------------------------------------------ API side

@lru_cache(maxsize=1)
def api_settings() -> Settings:
    """The API's Settings: read once from the same config.json api/db.py
    reads db_path from (DEFAULTS when there is no config file). A config
    change needs an API restart."""
    from api import db  # lazy: api.db imports this module
    return load_settings(db.config_tracking())


@lru_cache(maxsize=1)
def api_settings_tag() -> str:
    """Short fingerprint of api_settings(), part of every ETag, so a restart
    with changed settings never answers 304 with a response built under the
    old ones."""
    return hashlib.sha1(repr(api_settings()).encode()).hexdigest()[:8]


def reset_api_settings() -> None:
    """Forget the cached config (tests; a restart does the same)."""
    from api import db
    db.config_tracking.cache_clear()
    api_settings.cache_clear()
    api_settings_tag.cache_clear()


__all__ = ["DEFAULTS", "DEFAULT_CONFIG", "Settings", "api_settings", "api_settings_tag",
           "days_s", "load_settings", "reset_api_settings"]
