"""Render every item's inventory icon into <website_path>/cache/items/<id>.png.

    python3 tools/render_items.py                 # server.minecraft_version from config.json
    python3 tools/render_items.py --version 26.2 --dry-run

Downloads the client jar for the version from Mojang's version manifest
(sha1-checked, kept as <jar dir>/client-<version>.jar, default next to the stats
DB; never committed), renders every id in assets/minecraft/items/ with
tools/item_icons.py (64x64 PNG, pixel art) and overwrites the old files. Prints
a report: ids not rendered properly (fallback icon) and why, Storage page
names (data/ms_storage.json, dig_storage.json) whose lower().replace(' ', '_')
is not an item id, and files in the output folder that are no item id (left
alone). Re-run after every Minecraft version bump. --dry-run renders and
reports without writing icons (the jar is still downloaded).
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import item_icons  # noqa: E402

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "chronos-stats"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_jar(version, jar_dir, fetch=http_get):
    """Path of the verified client jar for version, downloading it if the
    file is missing or its sha1 differs. Raises on an unknown version or a
    bad download."""
    manifest = json.loads(fetch(MANIFEST_URL))
    entry = next((v for v in manifest.get("versions", []) if v.get("id") == version), None)
    if entry is None:
        raise SystemExit(f"version {version!r} is not in Mojang's version manifest")
    client = json.loads(fetch(entry["url"]))["downloads"]["client"]
    os.makedirs(jar_dir, exist_ok=True)
    dest = os.path.join(jar_dir, f"client-{version}.jar")
    if os.path.exists(dest) and _sha1(dest) == client["sha1"]:
        return dest
    data = fetch(client["url"])
    if hashlib.sha1(data).hexdigest() != client["sha1"]:
        raise SystemExit(f"client jar for {version}: sha1 mismatch, not saved")
    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)
    return dest


def storage_names(data_dir):
    names = set()
    for fname in ("ms_storage.json", "dig_storage.json"):
        try:
            with open(os.path.join(data_dir, fname), encoding="utf-8") as f:
                names.update(json.load(f).keys())
        except (OSError, ValueError, AttributeError):
            continue
    return names


def render_all(jar_path, out_dir, data_dir=None, dry_run=False, log=print):
    """Render every item id; returns the report as a dict."""
    with zipfile.ZipFile(jar_path) as zf:
        assets = item_icons.Assets(zf)
        ids = assets.item_ids()
        flagged = {}
        if not dry_run:
            os.makedirs(out_dir, exist_ok=True)
        for item_id in ids:
            res = item_icons.render_item(assets, item_id)
            if res.fallback or res.problems:
                flagged[item_id] = ("fallback: " if res.fallback else "") + "; ".join(
                    sorted(set(res.problems)))
            if not dry_run:
                dest = os.path.join(out_dir, f"{item_id}.png")
                tmp = dest + ".tmp"
                res.image.save(tmp, "PNG")
                os.replace(tmp, dest)
    known = set(ids)
    bad_names = sorted(n for n in (storage_names(data_dir) if data_dir else set())
                       if n.lower().replace(" ", "_") not in known)
    extra = sorted(p[:-4] for p in (os.listdir(out_dir) if os.path.isdir(out_dir) else [])
                   if p.endswith(".png") and p[:-4] not in known)
    report = {"rendered": len(ids), "flagged": flagged, "storage_names": bad_names,
              "extra_files": extra}
    log(f"{'would render' if dry_run else 'rendered'} {len(ids)} item icons into {out_dir}")
    log(f"\nnot rendered properly ({len(flagged)}):")
    for item_id, why in sorted(flagged.items()):
        log(f"  {item_id}: {why}")
    log(f"\nStorage names that are no item id ({len(bad_names)}; the site needs overrides):")
    for n in bad_names:
        log(f"  {n} -> {n.lower().replace(' ', '_')}")
    log(f"\nfiles in {out_dir} that are no item id ({len(extra)}; left alone):")
    for n in extra:
        log(f"  {n}.png")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=str(REPO_ROOT / "config.json"))
    ap.add_argument("--version", help="default: server.minecraft_version")
    ap.add_argument("--out", help="default: <server.website_path>/cache/items")
    ap.add_argument("--jar-dir", help="default: the stats DB's folder")
    ap.add_argument("--jar", help="use this client jar instead of downloading")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    server = cfg.get("server", {})
    website = server.get("website_path")
    version = args.version or server.get("minecraft_version")
    out = args.out or (os.path.join(website, "cache", "items") if website else None)
    if not version or not out:
        raise SystemExit("need --version and --out (or server.minecraft_version / website_path)")
    jar = args.jar
    if not jar:
        db = cfg.get("stats_tracking", {}).get("db_path")
        jar_dir = args.jar_dir or (os.path.dirname(db) if db else ".")
        jar = download_jar(version, jar_dir)
    render_all(jar, out, os.path.join(website, "data") if website else None, args.dry_run)


if __name__ == "__main__":
    main()
