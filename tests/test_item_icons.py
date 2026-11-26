"""Item icons from a (synthetic) client jar: tools/item_icons.py + tools/render_items.py."""
import hashlib
import io
import json
import zipfile

import pytest
from PIL import Image

from tools import item_icons as ii
from tools import render_items

RED, GREEN, BLUE, GRAY = (200, 0, 0, 255), (0, 200, 0, 255), (0, 0, 200, 255), (100, 100, 100, 255)
BLOCK_GUI = {"gui": {"rotation": [30, 225, 0], "translation": [0, 0, 0],
                     "scale": [0.625, 0.625, 0.625]}}


def png(color, size=(16, 16)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def strip(colors):
    """A 16x(16*n) animation strip, one solid frame per color."""
    img = Image.new("RGBA", (16, 16 * len(colors)))
    for i, c in enumerate(colors):
        img.paste(Image.new("RGBA", (16, 16), c), (0, 16 * i))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_jar(path, extra=None):
    files = {
        "models/block/block.json": {"gui_light": "side", "display": BLOCK_GUI},
        "models/block/colored.json": {
            "parent": "block/block",
            "textures": {"u": "block/red", "n": "block/green", "e": "block/blue",
                         "o": {"sprite": "minecraft:block/gray"}},
            "elements": [{"from": [0, 0, 0], "to": [16, 16, 16], "faces": {
                "up": {"texture": "#u"}, "north": {"texture": "#n"}, "east": {"texture": "#e"},
                "south": {"texture": "o"}, "west": {"texture": "#o"}, "down": {"texture": "#o"}}}]},
        "models/block/rotated.json": {
            "parent": "block/block", "textures": {"h": "block/half"},
            "elements": [{"from": [0, 0, 0], "to": [16, 16, 16], "faces": {
                "up": {"texture": "#h", "rotation": 90}}}]},
        "items/rotated.json": {"model": {"type": "minecraft:model", "model": "block/rotated"}},
        "models/item/generated.json": {"parent": "builtin/generated", "gui_light": "front"},
        "models/item/flat.json": {"parent": "item/generated",
                                  "textures": {"layer0": "item/white", "layer1": "item/dot"}},
        "models/item/anim.json": {"parent": "item/generated", "textures": {"layer0": "item/anim"}},
        "models/item/gui_one.json": {"parent": "item/generated", "textures": {"layer0": "block/green"}},
        "models/item/hand_one.json": {"parent": "item/generated", "textures": {"layer0": "block/red"}},
        "models/item/template_skull": None,
        "models/item/statue.json": {"textures": {"particle": "block/blue"}},
        "items/colored.json": {"model": {"type": "minecraft:model", "model": "minecraft:block/colored"}},
        "items/flat.json": {"model": {"type": "minecraft:model", "model": "item/flat",
                                      "tints": [{"type": "minecraft:dye", "default": -16744448}]}},
        "items/anim.json": {"model": {"type": "minecraft:model", "model": "item/anim"}},
        "items/spear.json": {"model": {
            "type": "minecraft:select", "property": "minecraft:display_context",
            "cases": [{"when": ["gui", "ground"],
                       "model": {"type": "minecraft:model", "model": "item/gui_one"}}],
            "fallback": {"type": "minecraft:model", "model": "item/hand_one"}}},
        "items/toggle.json": {"model": {
            "type": "minecraft:condition", "property": "minecraft:using_item",
            "on_true": {"type": "minecraft:model", "model": "item/hand_one"},
            "on_false": {"type": "minecraft:model", "model": "item/gui_one"}}},
        "items/statue.json": {"model": {"type": "minecraft:special", "base": "item/statue",
                                        "model": {"type": "minecraft:copper_golem_statue"}}},
        "items/broken.json": {"model": {"type": "minecraft:model", "model": "item/nope"}},
        "items/skull.json": {"model": {
            "type": "minecraft:special", "base": "minecraft:item/skull_base",
            "model": {"type": "minecraft:head", "kind": "skeleton"},
            "transformation": {"left_rotation": [1, 0, 0, 0], "right_rotation": [0, 0, 0, 1],
                               "scale": [1, 1, 1], "translation": [0.5, 0, 0.5]}}},
        "models/item/skull_base.json": {"display": {"gui": {
            "rotation": [30, 45, 0], "translation": [0, 3, 0], "scale": [1, 1, 1]}}},
    }
    files.update(extra or {})
    skull = Image.new("RGBA", (64, 32), GRAY)
    skull.paste(Image.new("RGBA", (8, 8), RED), (8, 8))  # front of the head box
    buf = io.BytesIO()
    skull.save(buf, "PNG")
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            if data is not None:
                z.writestr(f"assets/minecraft/{name}", json.dumps(data))
        for name, color in (("block/red", RED), ("block/green", GREEN), ("block/blue", BLUE),
                            ("block/gray", GRAY), ("item/white", (255, 255, 255, 255))):
            z.writestr(f"assets/minecraft/textures/{name}.png", png(color))
        half = Image.new("RGBA", (16, 16), BLUE)
        half.paste(Image.new("RGBA", (8, 16), RED), (0, 0))  # left half red
        b3 = io.BytesIO()
        half.save(b3, "PNG")
        z.writestr("assets/minecraft/textures/block/half.png", b3.getvalue())
        dot = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        dot.putpixel((15, 15), BLUE)
        b2 = io.BytesIO()
        dot.save(b2, "PNG")
        z.writestr("assets/minecraft/textures/item/dot.png", b2.getvalue())
        z.writestr("assets/minecraft/textures/item/anim.png", strip([RED, GREEN, BLUE]))
        z.writestr("assets/minecraft/textures/item/anim.png.mcmeta",
                   json.dumps({"animation": {"frames": [2, 0, 1]}}))
        z.writestr("assets/minecraft/textures/entity/skeleton/skeleton.png", buf.getvalue())
    return path


@pytest.fixture
def assets(tmp_path):
    zf = zipfile.ZipFile(make_jar(tmp_path / "client.jar"))
    yield ii.Assets(zf)
    zf.close()


def shaded(c, f):
    return (int(c[0] * f), int(c[1] * f), int(c[2] * f), 255)


def test_block_iso_faces_and_shading(assets):
    r = ii.render_item(assets, "colored")
    assert r.problems == [] and not r.fallback
    img = r.image
    assert img.size == (64, 64)
    assert img.getpixel((32, 14)) == RED                       # up, full light
    assert img.getpixel((17, 39)) == shaded(BLUE, 0.8)         # east = left side
    assert img.getpixel((46, 39)) == shaded(GREEN, 0.6)        # north = right side
    assert img.getpixel((1, 1))[3] == 0                        # corner stays empty


def test_face_rotation_is_clockwise(assets):
    # up face: texture top = north. Rotated 90 clockwise, the red left half
    # becomes the top = the north half (screen right-down of the face centre)
    img = ii.render_item(assets, "rotated").image
    assert img.getpixel((39, 18)) == RED and img.getpixel((25, 11)) == BLUE


def test_flat_item_layers_and_dye_tint(assets):
    img = ii.render_item(assets, "flat").image
    assert img.getpixel((0, 0)) == (0, 128, 0, 255)            # -16744448 = 0xFF008000
    assert img.getpixel((63, 63)) == BLUE                       # layer1 on top, untinted
    assert img.getpixel((60, 60)) == BLUE                       # one texel = 4x4 pixels


def test_animated_texture_uses_first_listed_frame(assets):
    assert ii.render_item(assets, "anim").image.getpixel((8, 8)) == BLUE


def test_select_display_context_takes_gui_case(assets):
    assert ii.render_item(assets, "spear").image.getpixel((8, 8)) == GREEN


def test_condition_takes_on_false(assets):
    assert ii.render_item(assets, "toggle").image.getpixel((8, 8)) == GREEN


def test_unsupported_special_falls_back_to_particle_and_is_reported(assets):
    r = ii.render_item(assets, "statue")
    assert r.fallback and "special copper_golem_statue" in r.problems
    assert r.image.getpixel((32, 32)) == BLUE


def test_missing_model_reported(assets):
    r = ii.render_item(assets, "broken")
    assert r.fallback and any("item/nope" in p for p in r.problems)


def test_skull_face_on_right_side(assets):
    r = ii.render_item(assets, "skull")
    assert r.problems == []
    assert r.image.getpixel((43, 39)) == shaded(RED, 0.6)


def test_item_ids(assets):
    assert "colored" in assets.item_ids() and "skull" in assets.item_ids()


# ---------------------------------------------------------------- CLI

def test_render_all_writes_every_id_and_reports(tmp_path, capsys):
    jar = make_jar(tmp_path / "c.jar")
    out = tmp_path / "www" / "cache" / "items"
    out.mkdir(parents=True)
    (out / "dropper_front.png").write_bytes(png(RED))          # old scraped file
    data = tmp_path / "www" / "data"
    data.mkdir()
    (data / "ms_storage.json").write_text(json.dumps({"Colored": 5, "Grass": 2}))
    rep = render_items.render_all(str(jar), str(out), str(data))
    written = {p.stem for p in out.glob("*.png")}
    assert {"colored", "flat", "skull", "statue", "broken"} <= written
    assert set(rep["flagged"]) == {"statue", "broken"}
    assert rep["storage_names"] == ["Grass"]
    assert rep["extra_files"] == ["dropper_front"]
    assert "Grass -> grass" in capsys.readouterr().out
    assert Image.open(out / "colored.png").size == (64, 64)
    assert not list(out.glob("*.tmp"))


def test_render_all_dry_run_writes_nothing(tmp_path):
    jar = make_jar(tmp_path / "c.jar")
    out = tmp_path / "items"
    rep = render_items.render_all(str(jar), str(out), None, dry_run=True)
    assert rep["rendered"] >= 5 and not out.exists()


def _fake_mojang(jar_bytes, sha=None):
    sha = sha or hashlib.sha1(jar_bytes).hexdigest()
    calls = []

    def fetch(url):
        calls.append(url)
        if url == render_items.MANIFEST_URL:
            return json.dumps({"versions": [{"id": "26.2", "url": "https://v/26.2.json"}]}).encode()
        if url == "https://v/26.2.json":
            return json.dumps({"downloads": {"client": {"url": "https://j/c.jar",
                                                        "sha1": sha}}}).encode()
        if url == "https://j/c.jar":
            return jar_bytes
        raise AssertionError(url)
    return fetch, calls


def test_download_jar_verifies_and_reuses(tmp_path):
    data = b"jar bytes"
    fetch, calls = _fake_mojang(data)
    p = render_items.download_jar("26.2", str(tmp_path), fetch)
    assert open(p, "rb").read() == data and p.endswith("client-26.2.jar")
    fetch2, calls2 = _fake_mojang(data)
    render_items.download_jar("26.2", str(tmp_path), fetch2)
    assert "https://j/c.jar" not in calls2


def test_download_jar_rejects_bad_sha1(tmp_path):
    fetch, _ = _fake_mojang(b"jar bytes", sha="0" * 40)
    with pytest.raises(SystemExit, match="sha1"):
        render_items.download_jar("26.2", str(tmp_path), fetch)
    assert not list(tmp_path.iterdir())


def test_download_jar_unknown_version(tmp_path):
    fetch, _ = _fake_mojang(b"x")
    with pytest.raises(SystemExit, match="not in Mojang"):
        render_items.download_jar("1.0", str(tmp_path), fetch)


def test_oversized_model_is_centred_and_fitted():
    # a quad hanging low out of the slot (like a banner): y -1.3 .. 0.1
    q = ii.Quad([(-0.2, 0.1, 0), (-0.2, -1.3, 0), (0.2, -1.3, 0), (0.2, 0.1, 0)],
                [(0, 0), (0, 1), (1, 1), (1, 0)], Image.new("RGBA", (4, 4), RED))
    box = ii.rasterize([q]).getbbox()
    assert box[1] <= 1 and box[3] >= 63          # fills the height
    assert abs((box[0] + box[2]) / 2 - 32) <= 1  # stays horizontally centred

