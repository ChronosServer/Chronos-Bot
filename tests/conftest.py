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
        d = self.world_dir(server_key) / "data" / "minecraft"
        d.mkdir(parents=True, exist_ok=True)
        root.write_file(str(d / "scoreboard.dat"))


@pytest.fixture
def world(tmp_path):
    return FakeWorld(tmp_path)


@pytest.fixture
def run_snapshot(world):
    import snapshot

    def _run(now, dictionary=None, **kw):
        # Never touches the network: dictionary is always injected.
        snapshot.run_once(config=world.config, now_fn=lambda: now,
                          dictionary={} if dictionary is None else dictionary, **kw)
    return _run
