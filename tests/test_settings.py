"""api/settings.py: defaults, deep merge and validation of stats_tracking."""
import json
import re
from pathlib import Path

import pytest

from api.settings import DEFAULTS, load_settings


def test_defaults_with_no_keys():
    for cfg in (None, {}, {"db_path": "/x.db", "excluded_servers": ["mirror"],
                           "servers": {"smp": {"label": "SMP", "group": "smp"},
                                       "cmp": {"label": "CMP", "group": "cmp"}}}):
        s = load_settings(cfg)
        assert s.scope_groups == {"cmps": "cmp"}
        assert s.activity_stat == ("minecraft:custom", "minecraft:play_time")
        assert s.rivalries.min_value == 1000 and s.rivalries.max_gap_ratio == 0.02
        assert s.rivalries.active_days == 7
        assert (s.rivalries.per_player, s.rivalries.per_key) == (5, 3)
        assert s.spotlight.outlier.ratio == 10 and s.spotlight.outlier.min_holders == 10
        assert s.spotlight.rare.max_holders == 3
        assert (s.spotlight.recent_milestone_days, s.spotlight.recent_first_days) == (7, 30)
        m = s.spotlight.mover
        assert (m.top, m.min_growth, m.min_delta, m.window_days) == (20, 0.5, 1000, 7)
        assert (s.mojang.max_calls, s.mojang.timeout_s, s.mojang.max_seconds,
                s.mojang.retry_after_days) == (60, 3, 60, 7)
        assert s.badges.firsts == (1, 10, 50) and s.badges.top1 == (1, 10, 50)
        assert s.badges.big_numbers == (1000000, 10000000)
        assert s.badges.category_tiers == (100000, 1000000, 10000000)
        assert s.badges.veteran_hours == 1000
        assert (s.badges.regular.days, s.badges.regular.window_days) == (20, 30)
        n = s.badges.night_owl
        assert (n.min_buckets, n.window_days, n.share, n.start_hour, n.end_hour) == (
            40, 30, 0.5, 0, 6)
        a = s.api
        assert dict(a.range_presets) == {"1d": 86400, "1w": 604800, "1m": 2592000,
                                         "6m": 15724800, "1y": 31536000}
        assert (a.activity_bucket_s, a.series_max_points, a.leaderboard_max_limit,
                a.feed_max_limit, a.search_max_limit) == (900, 500, 200, 200, 50)
        assert (a.compare_min_players, a.compare_max_players, a.compare_max_keys,
                a.series_max_players) == (2, 6, 20, 6)
        assert (a.cache_max_bytes, a.cache_max_body_bytes, a.cache_max_entries) == (
            134217728, 8388608, 2048)
    assert DEFAULTS == load_settings(None)


def test_default_milestone_rules_match_previous_hardcoded_list():
    rules = DEFAULTS.milestones.rules()
    cats = ("mined", "used", "crafted", "broken", "picked_up", "dropped", "killed", "killed_by")
    expected = [(f"cat:minecraft:{c}", f"minecraft:{c}", None,
                 [1000, 10000, 100000, 1000000, 10000000]) for c in cats]
    expected += [
        ("key:minecraft:custom/minecraft:play_time", "minecraft:custom", "minecraft:play_time",
         [h * 72000 for h in (100, 500, 1000, 5000, 10000)]),
        ("key:minecraft:custom/minecraft:walk_one_cm", "minecraft:custom",
         "minecraft:walk_one_cm", [k * 100000 for k in (100, 1000, 10000)]),
        ("key:minecraft:custom/minecraft:deaths", "minecraft:custom", "minecraft:deaths",
         [100, 1000, 10000]),
    ]
    assert rules == expected


def test_default_servers_filters_bots_only_on_smp():
    s = load_settings({"servers": {"smp": {"label": "SMP", "group": "smp"},
                                   "cmp": {"label": "CMP", "group": "cmp"}}})
    assert s.servers["smp"].filters_bots is True
    assert s.servers["cmp"].filters_bots is False
    assert s.servers["cmp"].track_stats and s.servers["cmp"].track_scoreboards


def test_deep_merge_partial_override():
    s = load_settings({
        "rivalries": {"max_gap_ratio": 0.05},
        "spotlight": {"mover": {"top": 5}},
        "badges": {"night_owl": {"share": 0.75}},
        "api": {"leaderboard_max_limit": 100},
    })
    assert s.rivalries.max_gap_ratio == 0.05
    assert s.rivalries.min_value == 1000 and s.rivalries.per_key == 3
    assert s.spotlight.mover.top == 5 and s.spotlight.mover.min_delta == 1000
    assert s.spotlight.outlier.ratio == 10
    assert s.badges.night_owl.share == 0.75 and s.badges.night_owl.min_buckets == 40
    assert s.badges.regular.days == 20
    assert s.api.leaderboard_max_limit == 100 and s.api.feed_max_limit == 200
    assert dict(s.api.range_presets)["1y"] == 31536000


def test_name_keyed_maps_replace_whole():
    s = load_settings({"scope_groups": {"creative": "cmp"},
                       "api": {"range_presets": {"2w": 1209600}}})
    assert s.scope_groups == {"creative": "cmp"}
    assert dict(s.api.range_presets) == {"2w": 1209600}


def test_settings_frozen():
    with pytest.raises(Exception):
        DEFAULTS.rivalries.min_value = 5
    with pytest.raises(TypeError):
        DEFAULTS.api.range_presets["x"] = 1


@pytest.mark.parametrize("cfg, msg", [
    ({"rivalries": {"max_gap_ratio": 0}},
     "stats_tracking.rivalries.max_gap_ratio must be a number in (0, 1]"),
    ({"rivalries": {"max_gap_ratio": 1.5}},
     "stats_tracking.rivalries.max_gap_ratio must be a number in (0, 1]"),
    ({"rivalries": {"max_gap_ratio": "0.02"}},
     "stats_tracking.rivalries.max_gap_ratio must be a number in (0, 1]"),
    ({"rivalries": {"per_key": 2.5}}, "stats_tracking.rivalries.per_key must be an integer"),
    ({"rivalries": {"per_key": True}}, "stats_tracking.rivalries.per_key must be an integer"),
    ({"rivalries": {"gap": 1}}, "stats_tracking.rivalries.gap is not a known setting"),
    ({"rivalries": 5}, "stats_tracking.rivalries must be an object"),
    ({"api": {"leaderboard_max_limit": 0}},
     "stats_tracking.api.leaderboard_max_limit must be an integer >= 1"),
    ({"api": {"range_presets": {"all": 5}}}, "stats_tracking.api.range_presets.all"),
    ({"api": {"range_presets": {"1d": -1}}}, "stats_tracking.api.range_presets.1d"),
    ({"api": {"compare_min_players": 5, "compare_max_players": 4}},
     "stats_tracking.api.compare_max_players"),
    ({"milestones": {"per_key": [{"category": "minecraft:custom", "key": "k",
                                  "thresholds": [1], "scale": 0}]}},
     "stats_tracking.milestones.per_key[0].scale must be an integer >= 1"),
    ({"milestones": {"count_thresholds": [10, 5]}},
     "stats_tracking.milestones.count_thresholds"),
    ({"badges": {"night_owl": {"start_hour": 25}}},
     "stats_tracking.badges.night_owl.start_hour"),
    ({"badges": {"regular": {"days": 40}}}, "stats_tracking.badges.regular.days"),
    ({"servers": {"smp": {"label": "SMP", "group": "smp", "filters_bots": "yes"}}},
     "stats_tracking.servers.smp.filters_bots must be true or false"),
    ({"servers": {"smp": {"group": "smp"}}}, "stats_tracking.servers.smp.label"),
    ({"scope_groups": {"all": "cmp"}}, "stats_tracking.scope_groups.all"),
    ({"servers": {"cmp": {"label": "CMP", "group": "cmp"}}, "scope_groups": {"cmp": "cmp"}},
     "stats_tracking.scope_groups.cmp"),
    ({"activity_stat": ["minecraft:custom"]}, "stats_tracking.activity_stat"),
    ({"mojang": {"timeout_s": 0}}, "stats_tracking.mojang.timeout_s must be a number > 0"),
    ({"spotlight": {"mover": {"min_growth": -1}}},
     "stats_tracking.spotlight.mover.min_growth must be a number >= 0"),
])
def test_validation_errors_name_the_key_path(cfg, msg):
    with pytest.raises(ValueError, match=re.escape(msg)):
        load_settings(cfg)


def test_comment_keys_ignored():
    s = load_settings({"_comment": "hi", "rivalries": {"_note": "x", "per_key": 4}})
    assert s.rivalries.per_key == 4


def test_snapshot_fails_loudly_on_bad_config(world, run_snapshot):
    world.config["stats_tracking"]["rivalries"] = {"max_gap_ratio": 2}
    with pytest.raises(ValueError, match=re.escape("stats_tracking.rivalries.max_gap_ratio")):
        run_snapshot(1000)
    assert not Path(world.db_path).exists()  # failed before touching the database


def test_api_fails_loudly_on_bad_config(api_config):
    from api.main import create_app
    api_config({"db_path": "/nowhere.db", "api": {"series_max_points": 1}})
    with pytest.raises(ValueError, match=re.escape("stats_tracking.api.series_max_points")):
        create_app()


def test_api_settings_read_from_config_json_once(api_config, tmp_path):
    from api import db
    from api.settings import api_settings
    api_config({"db_path": "/nowhere.db", "rivalries": {"per_key": 9}})
    assert api_settings().rivalries.per_key == 9
    # cached: editing the file needs a restart (cache reset) to take effect
    db.CONFIG_PATH.write_text(json.dumps({"stats_tracking": {"rivalries": {"per_key": 1}}}),
                              encoding="utf-8")
    assert api_settings().rivalries.per_key == 9


def test_api_settings_default_without_config_file():
    from api.settings import api_settings
    assert api_settings() == DEFAULTS
