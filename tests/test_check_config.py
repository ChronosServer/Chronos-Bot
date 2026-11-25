"""deploy/check_config.py: compares config.json files, never writes."""
import copy
import json

from deploy import check_config as cc

REPO_CFG = {
    "bot": {"token": "x", "guild_id": 1, "archive_channel_id": 2},
    "server": {"smp_path": "/a", "smp_world_name": "w", "colors": {"red": 1}},
    "stats_tracking": {
        "db_path": "/db", "excluded_servers": [],
        "servers": {"smp": {"label": "SMP", "group": "smp"}},
        "api": {"activity_bucket_s": 900},
    },
}


def run(tmp_path, server_cfg, capsys):
    s, r = tmp_path / "server.json", tmp_path / "repo.json"
    s.write_text(json.dumps(server_cfg), encoding="utf-8")
    r.write_text(json.dumps(REPO_CFG), encoding="utf-8")
    before = s.read_bytes()
    rc = cc.main([str(s), str(r)])
    assert s.read_bytes() == before  # never written
    return rc, capsys.readouterr().out


def test_identical_ok(tmp_path, capsys):
    rc, out = run(tmp_path, REPO_CFG, capsys)
    assert rc == 0 and "config check: ok" in out


def test_missing_bot_key_warns_extra_is_info(tmp_path, capsys):
    cfg = copy.deepcopy(REPO_CFG)
    del cfg["bot"]["archive_channel_id"]
    cfg["bot"]["legacy"] = True
    cfg["server"]["colors"] = {"blue": 2}  # name map: own entries, not MISSING
    rc, out = run(tmp_path, cfg, capsys)
    assert rc == 1
    assert "MISSING bot.archive_channel_id" in out
    assert "extra   bot.legacy" in out
    assert "MISSING server.colors" not in out


def test_stats_key_left_out_uses_default(tmp_path, capsys):
    cfg = copy.deepcopy(REPO_CFG)
    del cfg["stats_tracking"]["api"]
    rc, out = run(tmp_path, cfg, capsys)
    assert rc == 0 and "default stats_tracking.api" in out


def test_invalid_stats_tracking_is_error(tmp_path, capsys):
    cfg = copy.deepcopy(REPO_CFG)
    cfg["stats_tracking"]["nonsense"] = 1
    rc, out = run(tmp_path, cfg, capsys)
    assert rc == 2 and "ERROR" in out and "nonsense" in out


def test_tracked_server_without_paths(tmp_path, capsys):
    cfg = copy.deepcopy(REPO_CFG)
    cfg["stats_tracking"]["servers"]["cmp"] = {"label": "CMP", "group": "cmp"}
    rc, out = run(tmp_path, cfg, capsys)
    assert rc == 1 and "MISSING server.cmp_path" in out


def test_type_change(tmp_path, capsys):
    cfg = copy.deepcopy(REPO_CFG)
    cfg["bot"] = "oops"
    rc, out = run(tmp_path, cfg, capsys)
    assert rc == 1 and "TYPE    bot" in out
