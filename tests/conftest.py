"""Test harness: sys.path setup + fake world fixtures (real on-disk layout)."""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (str(REPO_ROOT), str(REPO_ROOT / "bot")):
    if p not in sys.path:
        sys.path.insert(0, p)

import nbt.nbt as nbt  # noqa: E402

SERVERS = {"smp": "smp", "cmp": "cmp", "cmp2": "cmp"}


class FakeWorld:
    def __init__(self, root: Path):
        self.root = root
        self.db_path = str(root / "stats.db")
        server = {"minecraft_version": "test"}
        servers = {}
        for key, group in SERVERS.items():
            server[f"{key}_path"] = str(root / key / "server")
            server[f"{key}_world_name"] = f"world-{key}0"
            servers[key] = {"label": key.upper(), "group": group,
                            "track_stats": True, "track_scoreboards": True}
        self.config = {
            "server": server,
            "stats_tracking": {"db_path": self.db_path, "excluded_servers": [],
                               "servers": servers},
        }
        for key in SERVERS:
            self.world_dir(key).mkdir(parents=True, exist_ok=True)

    def server_root(self, key):
        return Path(self.config["server"][f"{key}_path"])

    def world_dir(self, key):
        return self.server_root(key) / self.config["server"][f"{key}_world_name"]

    def add_stats(self, server_key, uuid, stats):
        d = self.world_dir(server_key) / "players" / "stats"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{uuid}.json").write_text(
            json.dumps({"stats": stats, "DataVersion": 4000}), encoding="utf-8")

    def set_usercache(self, server_key, mapping):
        entries = [{"name": n, "uuid": u, "expiresOn": "2099-01-01 00:00:00 +0000"}
                   for u, n in mapping.items()]
        (self.server_root(server_key) / "usercache.json").write_text(
            json.dumps(entries), encoding="utf-8")

    def set_scoreboard(self, server_key, objectives, scores, teams):
        d = self.world_dir(server_key) / "data" / "minecraft"
        write_scoreboard(d / "scoreboard.dat", objectives, scores, teams)


def write_scoreboard(path, objectives, scores, teams=None):
    """scoreboard.dat as Minecraft writes it: data.{Objectives,PlayerScores,Teams}."""
    teams = teams or {}
    root = nbt.NBTFile()
    root.name = ""
    data = nbt.TAG_Compound(name="data")
    objs = nbt.TAG_List(name="Objectives", type=nbt.TAG_Compound)
    for o in objectives:
        c = nbt.TAG_Compound()
        c.tags.append(nbt.TAG_String(name="Name", value=o))
        objs.tags.append(c)
    sc = nbt.TAG_List(name="PlayerScores", type=nbt.TAG_Compound)
    for name, obj, val in scores:
        c = nbt.TAG_Compound()
        c.tags.append(nbt.TAG_String(name="Name", value=name))
        c.tags.append(nbt.TAG_String(name="Objective", value=obj))
        c.tags.append(nbt.TAG_Int(name="Score", value=val))
        sc.tags.append(c)
    tl = nbt.TAG_List(name="Teams", type=nbt.TAG_Compound)
    for tname, players in teams.items():
        c = nbt.TAG_Compound()
        c.tags.append(nbt.TAG_String(name="Name", value=tname))
        pl = nbt.TAG_List(name="Players", type=nbt.TAG_String)
        for p in players:
            pl.tags.append(nbt.TAG_String(value=p))
        c.tags.append(pl)
        tl.tags.append(c)
    data.tags.extend([objs, sc, tl])
    root.tags.append(data)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    root.write_file(str(path))


def make_backup(tmp, *, layout="old", last_played_ms, stats, scoreboard=None,
                usercache=None, name="backup", level_name="world",
                version_name="1.16.5", data_version=2586):
    """A backup world folder `<tmp>/<name>/world` (usercache.json next to it).

    layout "old" (<=1.21): stats/ + data/scoreboard.dat; "new" (26.x):
    players/stats/ + data/minecraft/scoreboard.dat. last_played_ms=None
    writes a level.dat without LastPlayed. scoreboard = (objectives,
    [(name, objective, score), ...])."""
    world = Path(tmp) / name / "world"
    world.mkdir(parents=True, exist_ok=True)
    root = nbt.NBTFile()
    root.name = ""
    data = nbt.TAG_Compound(name="Data")
    if last_played_ms is not None:
        data.tags.append(nbt.TAG_Long(name="LastPlayed", value=last_played_ms))
    data.tags.append(nbt.TAG_String(name="LevelName", value=level_name))
    data.tags.append(nbt.TAG_Int(name="DataVersion", value=data_version))
    ver = nbt.TAG_Compound(name="Version")
    ver.tags.append(nbt.TAG_String(name="Name", value=version_name))
    ver.tags.append(nbt.TAG_Int(name="Id", value=data_version))
    data.tags.append(ver)
    root.tags.append(data)
    root.write_file(str(world / "level.dat"))

    sdir = world / ("stats" if layout == "old" else "players/stats")
    sdir.mkdir(parents=True, exist_ok=True)
    for u, st in stats.items():
        (sdir / f"{u}.json").write_text(
            json.dumps({"stats": st, "DataVersion": data_version}), encoding="utf-8")
    if scoreboard is not None:
        objectives, scores = scoreboard
        sb = world / ("data/scoreboard.dat" if layout == "old"
                      else "data/minecraft/scoreboard.dat")
        write_scoreboard(sb, objectives, scores)
    if usercache is not None:
        entries = [{"name": n, "uuid": u, "expiresOn": "2099-01-01 00:00:00 +0000"}
                   for u, n in usercache.items()]
        (world.parent / "usercache.json").write_text(json.dumps(entries), encoding="utf-8")
    return world


@pytest.fixture
def world(tmp_path):
    return FakeWorld(tmp_path)


@pytest.fixture
def run_snapshot(world):
    import snapshot

    def _run(now, dictionary=None, **kw):
        # Never touches the network: dictionary is always injected.
        kw.setdefault("mojang_fetch", lambda method, url, body: (0, b""))
        snapshot.run_once(config=world.config, now_fn=lambda: now,
                          dictionary={} if dictionary is None else dictionary, **kw)
    return _run


@pytest.fixture(autouse=True)
def _isolated_api_settings(tmp_path, monkeypatch):
    """The API reads its settings from config.json once (cached). Tests never
    see the repo's real config.json: by default there is none (= defaults);
    `api_config` writes one."""
    from api import db, settings
    monkeypatch.setattr(db, "CONFIG_PATH", tmp_path / "no-config.json")
    settings.reset_api_settings()
    yield
    settings.reset_api_settings()


@pytest.fixture(autouse=True)
def _no_repo_merges(tmp_path, monkeypatch):
    """Tests never read the repo's player_merges.json (real accounts)."""
    from api import merges
    monkeypatch.setattr(merges, "MERGES_PATH", tmp_path / "no-merges.json")
    merges.reset_api_merges()
    yield
    merges.reset_api_merges()


@pytest.fixture(autouse=True)
def _no_repo_name_lists(tmp_path, monkeypatch):
    """Tests never read the repo's name_blocklist.txt / name_allowlist.txt;
    a test writes tmp_path/name_blocklist.txt (or the allowlist) itself."""
    from api import names
    monkeypatch.setattr(names, "BLOCKLIST_PATH", tmp_path / "name_blocklist.txt")
    monkeypatch.setattr(names, "ALLOWLIST_PATH", tmp_path / "name_allowlist.txt")


@pytest.fixture(autouse=True)
def _fresh_search_index(monkeypatch):
    """/search's in-memory index is keyed by data_version only; two test DBs
    with the same run count would otherwise share one."""
    from api.routes import search
    monkeypatch.setattr(search, "_index", (None, None))


@pytest.fixture
def api_config(tmp_path, monkeypatch):
    """api_config(stats_tracking_dict): the API's config.json for this test."""
    from api import db, settings

    def _set(tracking):
        p = tmp_path / "api-config.json"
        p.write_text(json.dumps({"stats_tracking": tracking}), encoding="utf-8")
        monkeypatch.setattr(db, "CONFIG_PATH", p)
        settings.reset_api_settings()
    return _set
