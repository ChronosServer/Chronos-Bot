"""Inventory icons for every item id, rendered from the client jar's assets.

Used by tools/render_items.py. Reads assets/minecraft/items/<id>.json (the item
model definitions, 1.21.4+), resolves block/item model parents and textures,
and draws the icon the way the inventory does:
- flat items (parent chain reaches builtin/generated): the layer textures,
  tinted (dye / potion / grass ... defaults), 16x16 scaled up;
- block models: every element's faces through the model's display.gui
  transform (usually rotation 30/225/0, scale 0.625), orthographic, depth
  sorted, nearest-neighbour texture sampling, back faces culled, shaded by
  the face's on-screen direction (top 1.0, left 0.8, right 0.6, bottom 0.5)
  when gui_light is "side";
- minecraft:special items: chests, shulker boxes and mob/player heads as
  entity boxes (the game's ModelPart cube UV layout); every other special
  (banner, shield, conduit, decorated pot, copper golem statue, dragon head)
  falls back to its base model's particle texture and is reported.
Pure Python + Pillow (PNG reading/writing only).
"""
import io
import json
import math
from dataclasses import dataclass, field

from PIL import Image

NS = "minecraft:"
SIZE = 64
SIDE_SHADE = {"top": 1.0, "bottom": 0.5, "left": 0.8, "right": 0.6}


def strip_ns(ref):
    return ref[len(NS):] if ref.startswith(NS) else ref


# ------------------------------------------------------------------ matrices
# 4x4 affine matrices as row-major lists of 16 floats.

def mat_identity():
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def mat_mul(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def mat_translate(x, y, z):
    m = mat_identity()
    m[3], m[7], m[11] = x, y, z
    return m


def mat_scale(x, y, z):
    m = mat_identity()
    m[0], m[5], m[10] = x, y, z
    return m


def mat_rot(axis, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    if axis == "x":
        r = [1, 0, 0, 0, c, -s, 0, s, c]
    elif axis == "y":
        r = [c, 0, s, 0, 1, 0, -s, 0, c]
    else:
        r = [c, -s, 0, s, c, 0, 0, 0, 1]
    return [r[0], r[1], r[2], 0, r[3], r[4], r[5], 0, r[6], r[7], r[8], 0, 0, 0, 0, 1.0]


def mat_quat(q):
    """Rotation from a quaternion [x, y, z, w] (JOML order)."""
    x, y, z, w = (float(v) for v in q)
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0,
            2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0,
            2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0,
            0, 0, 0, 1.0]


def mat_euler_xyz(r):
    """Minecraft's rotationXYZ(x, y, z) in degrees: Rx * Ry * Rz."""
    return mat_mul(mat_mul(mat_rot("x", r[0]), mat_rot("y", r[1])), mat_rot("z", r[2]))


def _rotation(v):
    if isinstance(v, dict) and "axis" in v:  # axis-angle form
        ax = v["axis"]
        ang = math.radians(float(v.get("angle", 0)))
        s = math.sin(ang / 2)
        return mat_quat([ax[0] * s, ax[1] * s, ax[2] * s, math.cos(ang / 2)])
    if isinstance(v, list) and len(v) == 4:
        return mat_quat(v)
    return mat_identity()


def mat_transformation(t):
    """An item model `transformation`: a 16-float matrix (column-major, as
    the game writes it) or {translation, left_rotation, scale, right_rotation}
    = T * L * S * R."""
    if not t:
        return mat_identity()
    if isinstance(t, list) and len(t) == 16:
        return [float(t[c * 4 + r]) for r in range(4) for c in range(4)]
    tr = t.get("translation", [0, 0, 0])
    sc = t.get("scale", [1, 1, 1])
    m = mat_translate(*tr)
    m = mat_mul(m, _rotation(t.get("left_rotation")))
    m = mat_mul(m, mat_scale(*sc))
    return mat_mul(m, _rotation(t.get("right_rotation")))


def mat_display(d):
    """display.gui: translate(t/16) * rotationXYZ(r) * scale(s)."""
    d = d or {}
    t = [float(v) / 16 for v in d.get("translation", [0, 0, 0])]
    m = mat_translate(*t)
    m = mat_mul(m, mat_euler_xyz([float(v) for v in d.get("rotation", [0, 0, 0])]))
    return mat_mul(m, mat_scale(*[float(v) for v in d.get("scale", [1, 1, 1])]))


def apply(m, p):
    x, y, z = p
    return (m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11])


def normal_of(m, n):
    """Transform a normal by the inverse transpose of m's 3x3 part."""
    a, b, c, d, e, f, g, h, i = m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]
    # cofactor matrix = det * inverse transpose; sign of det kept so mirrored
    # transforms flip normals like the game's winding does
    cof = [e * i - f * h, -(d * i - f * g), d * h - e * g,
           -(b * i - c * h), a * i - c * g, -(a * h - b * g),
           b * f - c * e, -(a * f - c * d), a * e - b * d]
    x, y, z = n
    out = (cof[0] * x + cof[1] * y + cof[2] * z,
           cof[3] * x + cof[4] * y + cof[5] * z,
           cof[6] * x + cof[7] * y + cof[8] * z)
    ln = math.sqrt(sum(v * v for v in out)) or 1.0
    return tuple(v / ln for v in out)


# ------------------------------------------------------------------ assets

class Assets:
    """Reads models, textures and item definitions out of a client jar
    (a zipfile.ZipFile, or anything with .read(name) and .namelist())."""

    def __init__(self, zf):
        self.zf = zf
        self.names = set(zf.namelist())
        self._json = {}
        self._tex = {}

    def read_json(self, path):
        if path not in self._json:
            self._json[path] = (json.loads(self.zf.read(path))
                                if path in self.names else None)
        return self._json[path]

    def item_ids(self):
        pre = "assets/minecraft/items/"
        return sorted(n[len(pre):-5] for n in self.names
                      if n.startswith(pre) and n.endswith(".json") and "/" not in n[len(pre):])

    def item_definition(self, item_id):
        return self.read_json(f"assets/minecraft/items/{item_id}.json")

    def texture(self, ref):
        """RGBA image for a texture ref ("minecraft:block/stone"), the first
        animation frame; None if missing."""
        ref = strip_ns(ref)
        if ref in self._tex:
            return self._tex[ref]
        path = f"assets/minecraft/textures/{ref}.png"
        img = None
        if path in self.names:
            img = Image.open(io.BytesIO(self.zf.read(path))).convert("RGBA")
            img = _first_frame(img, self.read_json(path + ".mcmeta"))
        self._tex[ref] = img
        return img


def _first_frame(img, meta):
    anim = (meta or {}).get("animation") if isinstance(meta, dict) else None
    if anim is None:
        return img
    w = int(anim.get("width", img.width))
    h = int(anim.get("height", w))
    frames = anim.get("frames")
    idx = 0
    if frames:
        f0 = frames[0]
        idx = int(f0["index"] if isinstance(f0, dict) else f0)
    per_row = max(1, img.width // w)
    x, y = (idx % per_row) * w, (idx // per_row) * h
    if y + h > img.height:
        x, y = 0, 0
    return img.crop((x, y, x + w, y + h))


@dataclass
class Model:
    textures: dict = field(default_factory=dict)
    elements: list = None
    display: dict = field(default_factory=dict)
    gui_light: str = "side"
    generated: bool = False
    missing: list = field(default_factory=list)


def resolve_model(assets, ref):
    """Merge a model with its parents (textures child-first, elements from the
    nearest model that has them, display per key)."""
    chain, cur, out, seen = [], strip_ns(ref), Model(), set()
    while cur and cur not in seen:
        seen.add(cur)
        if cur == "builtin/generated":
            out.generated = True
            break
        if cur.startswith("builtin/"):
            break
        d = assets.read_json(f"assets/minecraft/models/{cur}.json")
        if d is None:
            out.missing.append(f"model {cur}")
            break
        chain.append(d)
        cur = strip_ns(d["parent"]) if d.get("parent") else None
    for d in reversed(chain):
        out.textures.update(d.get("textures", {}))
        out.display.update(d.get("display", {}))
    for d in chain:
        if "elements" in d:
            out.elements = d["elements"]
            break
    for d in chain:
        if "gui_light" in d:
            out.gui_light = d["gui_light"]
            break
    return out


def texture_ref(model, ref):
    """Follow "#name" references; None if unresolved. A texture entry may be
    an object {"sprite": ..., "force_translucent": ...} (26.x)."""
    seen = set()
    while True:
        if isinstance(ref, dict):
            ref = ref.get("sprite")
        if not (isinstance(ref, str) and ref.startswith("#")):
            break
        key = ref[1:]
        if key in seen:
            return None
        seen.add(key)
        ref = model.textures.get(key)
    return ref if isinstance(ref, str) else None


def face_texture(model, ref):
    """A face's texture: "#name", or (26.x) a bare variable name."""
    if isinstance(ref, str) and not ref.startswith("#") and ref in model.textures:
        return "#" + ref
    return ref


# ------------------------------------------------------------------ tints

def _argb(v):
    v = int(v) & 0xFFFFFFFF
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)


def _color(v):
    if isinstance(v, (int, float)):
        return _argb(v)
    if isinstance(v, list) and len(v) >= 3:
        return tuple(int(round(float(c) * 255)) for c in v[:3])
    return None


def tint_color(assets, t):
    """Default color of one item-model tint source, or None (no tint)."""
    kind = strip_ns(t.get("type", ""))
    if kind == "constant":
        return _color(t.get("value"))
    if kind in ("grass", "foliage", "dry_foliage"):
        cmap = assets.texture(f"colormap/{kind}")
        if cmap is None:
            return None
        temp = min(max(float(t.get("temperature", 0.5)), 0.0), 1.0)
        down = min(max(float(t.get("downfall", 1.0)), 0.0), 1.0) * temp
        x = int((1 - temp) * (cmap.width - 1))
        y = int((1 - down) * (cmap.height - 1))
        return cmap.getpixel((x, y))[:3]
    if "default" in t:
        return _color(t["default"])
    return None


# ------------------------------------------------------------------ quads

@dataclass
class Quad:
    verts: list            # 4 view-space points, in texture order TL, BL, BR, TR
    uvs: list              # 4 (u, v) in 0..1 of the texture image
    image: Image.Image
    tint: tuple = None
    shade: float = 1.0


# Block model faces: corners TL, BL, BR, TR as seen from outside, in element
# coordinates chosen from (x1|x2, y1|y2, z1|z2); and the outward normal.
FACES = {
    "north": (((1, 1, 0), (1, 0, 0), (0, 0, 0), (0, 1, 0)), (0, 0, -1)),
    "south": (((0, 1, 1), (0, 0, 1), (1, 0, 1), (1, 1, 1)), (0, 0, 1)),
    "west": (((0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1)), (-1, 0, 0)),
    "east": (((1, 1, 1), (1, 0, 1), (1, 0, 0), (1, 1, 0)), (1, 0, 0)),
    "up": (((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)), (0, 1, 0)),
    "down": (((0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1)), (0, -1, 0)),
}


def default_uv(face, f, t):
    x1, y1, z1 = f
    x2, y2, z2 = t
    return {"down": [x1, 16 - z2, x2, 16 - z1], "up": [x1, z1, x2, z2],
            "north": [16 - x2, 16 - y2, 16 - x1, 16 - y1],
            "south": [x1, 16 - y2, x2, 16 - y1],
            "west": [z1, 16 - y2, z2, 16 - y1],
            "east": [16 - z2, 16 - y2, 16 - z1, 16 - y1]}[face]


def element_matrix(rot):
    """An element's own rotation (pixels, around origin), with rescale."""
    if not rot:
        return mat_identity()
    o = [float(v) for v in rot.get("origin", [8, 8, 8])]
    if "axis" in rot:
        axis, ang = rot["axis"], float(rot.get("angle", 0))
        r = mat_rot(axis, ang)
        if rot.get("rescale") and ang % 90:
            s = 1 / math.cos(math.radians(abs(ang)))
            sc = [1.0 if a == axis else s for a in "xyz"]
            r = mat_mul(r, mat_scale(*sc))
    else:
        r = mat_euler_xyz([float(rot.get(a, 0)) for a in "xyz"])
    return mat_mul(mat_mul(mat_translate(*o), r), mat_translate(*[-v for v in o]))


def _shade_for(n):
    nx, ny, _ = n
    if ny > 0.5:
        return SIDE_SHADE["top"]
    if ny < -0.5:
        return SIDE_SHADE["bottom"]
    return SIDE_SHADE["left"] if nx < 0 else SIDE_SHADE["right"]


def _emit(out, corners_px, uv_corners, normal_px, img, tint, shade_on, to_view, elem_m):
    """corners_px: 4 points in pixel model space (TL, BL, BR, TR)."""
    m = mat_mul(to_view, elem_m)
    n = normal_of(m, normal_px)
    if n[2] <= 1e-6:  # facing away from the viewer
        return
    verts = [apply(m, p) for p in corners_px]
    out.append(Quad(verts, uv_corners, img, tint, _shade_for(n) if shade_on else 1.0))


def block_quads(assets, model, tints, to_view, problems):
    """Quads of a block model's elements. to_view maps PIXEL model space
    (0..16) to view space."""
    out = []
    for el in model.elements or []:
        f = [float(v) for v in el["from"]]
        t = [float(v) for v in el["to"]]
        em = element_matrix(el.get("rotation"))
        shade_on = model.gui_light == "side" and el.get("shade", True)
        for face, spec in (el.get("faces") or {}).items():
            if face not in FACES:
                continue
            ref = texture_ref(model, face_texture(model, spec.get("texture", "")))
            img = assets.texture(ref) if ref else None
            if img is None:
                problems.append(f"texture {ref or spec.get('texture')}")
                continue
            sel, normal = FACES[face]
            corners = [tuple((t if s else f)[i] for i, s in enumerate(c)) for c in sel]
            uv = spec.get("uv") or default_uv(face, f, t)
            u1, v1, u2, v2 = [float(v) / 16 for v in uv]
            uvc = [(u1, v1), (u1, v2), (u2, v2), (u2, v1)]  # TL, BL, BR, TR
            r = (int(spec.get("rotation", 0)) // 90) % 4
            uvc = [uvc[(i + r) % 4] for i in range(4)]
            ti = spec.get("tintindex", -1)
            tint = tints[ti] if isinstance(ti, int) and 0 <= ti < len(tints) else None
            _emit(out, corners, uvc, normal, img, tint, shade_on, to_view, em)
    return out


def flat_quads(assets, model, tints, to_view, problems):
    """A generated item: layer0, layer1 ... as stacked squares at z = 8 px."""
    out = []
    i = 0
    while f"layer{i}" in model.textures:
        ref = texture_ref(model, model.textures[f"layer{i}"])
        img = assets.texture(ref) if ref else None
        if img is None:
            problems.append(f"texture {ref}")
        else:
            z = 8 + i * 0.01
            corners = [(0, 16, z), (0, 0, z), (16, 0, z), (16, 16, z)]
            uvc = [(0, 0), (0, 1), (1, 1), (1, 0)]
            tint = tints[i] if i < len(tints) else None
            _emit(out, corners, uvc, (0, 0, 1), img, tint, False, to_view, mat_identity())
        i += 1
    if i == 0:
        problems.append("no layer0")
    return out


# ------------------------------------------------------------------ entity boxes

def entity_box(origin, size, tex_off, tex_size, inflate=0.0, offset=(0, 0, 0)):
    """The game's ModelPart cube: 6 faces (pixel entity space) with the
    standard box UV layout at tex_off. Returns [(corners TL,BL,BR,TR, uvs, normal)]."""
    w, h, d = size
    x0, y0, z0 = (origin[i] - inflate + offset[i] for i in range(3))
    x1, y1, z1 = (origin[i] + size[i] + inflate + offset[i] for i in range(3))
    tw, th = tex_size
    u, v = tex_off
    U = [u, u + d, u + d + w, u + d + w + w, u + d + w + d, u + d + w + d + w]
    V = [v, v + d, v + d + h]
    P = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    # (vertex indices, uv rect u1 v1 u2 v2, normal); vertices get
    # (u2,v1) (u1,v1) (u1,v2) (u2,v2) in order, as ModelPart.Polygon does
    polys = [
        ((5, 4, 0, 1), (U[1], V[0], U[2], V[1]), (0, -1, 0)),   # min y
        ((2, 3, 7, 6), (U[2], V[1], U[3], V[0]), (0, 1, 0)),    # max y
        ((0, 4, 7, 3), (U[0], V[1], U[1], V[2]), (-1, 0, 0)),   # min x
        ((1, 0, 3, 2), (U[1], V[1], U[2], V[2]), (0, 0, -1)),   # min z
        ((5, 1, 2, 6), (U[2], V[1], U[4], V[2]), (1, 0, 0)),    # max x
        ((4, 5, 6, 7), (U[4], V[1], U[5], V[2]), (0, 0, 1)),    # max z
    ]
    out = []
    for idx, (a, b, c, e), n in polys:
        corners = [P[i] for i in idx]
        uvs = [(a / tw, b / th), (c / tw, b / th), (c / tw, e / th), (a / tw, e / th)]
        out.append((corners, uvs, n))
    return out


HEAD_TEXTURES = {"skeleton": "entity/skeleton/skeleton",
                 "wither_skeleton": "entity/skeleton/wither_skeleton",
                 "zombie": "entity/zombie/zombie", "creeper": "entity/creeper/creeper",
                 "piglin": "entity/piglin/piglin", "player": "entity/player/wide/steve"}
HAT_KINDS = {"zombie", "player"}


# DyeColor texture colors (banner base layer tint).
DYE = {"white": 0xF9FFFE, "orange": 0xF9801D, "magenta": 0xC74EBD, "light_blue": 0x3AB3DA,
       "yellow": 0xFED83D, "lime": 0x80C71F, "pink": 0xF38BAA, "gray": 0x474F52,
       "light_gray": 0x9D9D97, "cyan": 0x169C9C, "purple": 0x8932B8, "blue": 0x3C44AA,
       "brown": 0x835432, "green": 0x5E7C16, "red": 0xB02E26, "black": 0x1D1D21}


def special_boxes(assets, spec):
    """[(image, boxes, tint)] layers for a supported special model, else None.
    Boxes in pixel entity space; the item's `transformation` places them."""
    kind = strip_ns(spec.get("type", ""))
    if kind == "chest":
        img = assets.texture("entity/chest/" + strip_ns(spec.get("texture", "normal")))
        if img is None:
            return None
        ts = (64, 64)
        return [(img, entity_box((1, 0, 1), (14, 10, 14), (0, 19), ts)
                 + entity_box((1, 0, 0), (14, 5, 14), (0, 0), ts, offset=(0, 9, 1))
                 + entity_box((7, -2, 14), (2, 4, 1), (0, 0), ts, offset=(0, 9, 1)), None)]
    if kind == "shulker_box":
        img = assets.texture("entity/shulker/" + strip_ns(spec.get("texture", "shulker")))
        if img is None:
            return None
        ts = (64, 64)
        return [(img, entity_box((-8, -8, -8), (16, 8, 16), (0, 28), ts, offset=(0, 24, 0))
                 + entity_box((-8, -16, -8), (16, 12, 16), (0, 0), ts, offset=(0, 24, 0)), None)]
    if kind == "banner":
        base = assets.texture("entity/banner/banner_base")
        color = assets.texture("entity/banner/base")
        if base is None:
            return None
        ts = (64, 64)
        flag = entity_box((-10, 0, -2), (20, 40, 1), (0, 0), ts, offset=(0, -32, 0))
        layers = [(base, flag + entity_box((-1, -30, -1), (2, 42, 2), (44, 0), ts)
                   + entity_box((-10, -32, -1), (20, 2, 2), (0, 42), ts), None)]
        dye = DYE.get(strip_ns(spec.get("color", "white")))
        if color is not None and dye is not None:
            layers.append((color, flag, _argb(dye)))
        return layers
    if kind == "shield":
        img = assets.texture("entity/shield/shield_base_nopattern")
        if img is None:
            return None
        ts = (64, 64)
        return [(img, entity_box((-6, -11, -2), (12, 22, 1), (0, 0), ts)
                 + entity_box((-1, -3, -1), (2, 6, 6), (26, 0), ts), None)]
    if kind == "conduit":
        img = assets.texture("entity/conduit/base")
        if img is None:
            return None
        return [(img, entity_box((-3, -3, -3), (6, 6, 6), (0, 0), (32, 16)), None)]
    if kind in ("head", "player_head"):
        head = "player" if kind == "player_head" else strip_ns(spec.get("kind", ""))
        ref = HEAD_TEXTURES.get(head)
        if spec.get("texture"):
            ref = "entity/" + strip_ns(spec["texture"])
        img = assets.texture(ref) if ref else None
        if img is None:
            return None
        ts = (img.width, img.height)
        if head == "piglin":
            boxes = (entity_box((-5, -8, -4), (10, 8, 8), (0, 0), ts)
                     + entity_box((-2, -4, -5), (4, 4, 1), (31, 1), ts)
                     + entity_box((2, -2, -5), (1, 2, 1), (2, 4), ts)
                     + entity_box((-3, -2, -5), (1, 2, 1), (2, 0), ts))
        else:
            boxes = entity_box((-4, -8, -4), (8, 8, 8), (0, 0), ts)
            if head in HAT_KINDS:
                boxes += entity_box((-4, -8, -4), (8, 8, 8), (32, 0), ts, inflate=0.25)
        return [(img, boxes, None)]
    return None


# ------------------------------------------------------------------ item tree

@dataclass
class Part:
    kind: str              # "model" | "special"
    ref: str               # model ref (model) / base model ref (special)
    tints: list
    matrix: list           # its transformation times every wrapping node's
    spec: dict = None      # the special model's "model" object


def _select_case(node):
    """A select's model in an inventory: the display_context case for "gui",
    else the fallback, else the first case."""
    cases = node.get("cases") or []
    if strip_ns(node.get("property", "")) == "display_context":
        for c in cases:
            when = c.get("when")
            when = when if isinstance(when, list) else [when]
            if "gui" in [strip_ns(w) for w in when if isinstance(w, str)]:
                return c.get("model")
    return node.get("fallback") or (cases[0].get("model") if cases else None)


def collect_parts(node, notes, depth=0, outer=None):
    """Flatten an item model definition to the parts drawn for a plain stack
    in a GUI: select takes its "gui" case / fallback / first case,
    range_dispatch its fallback (else the first entry), condition its
    on_false branch, composite all of its models. A node's transformation
    applies to everything under it."""
    if not isinstance(node, dict) or depth > 32:
        return []
    m = mat_mul(outer or mat_identity(), mat_transformation(node.get("transformation")))
    kind = strip_ns(node.get("type", ""))
    if kind == "model":
        return [Part("model", node.get("model", ""), node.get("tints", []), m)]
    if kind == "composite":
        return [p for sub in node.get("models", [])
                for p in collect_parts(sub, notes, depth + 1, m)]
    if kind == "condition":
        return collect_parts(node.get("on_false"), notes, depth + 1, m)
    if kind == "select":
        return collect_parts(_select_case(node), notes, depth + 1, m)
    if kind == "range_dispatch":
        nxt = node.get("fallback") or (node.get("entries") or [{}])[0].get("model")
        return collect_parts(nxt, notes, depth + 1, m)
    if kind == "special":
        return [Part("special", node.get("base", ""), [], m, node.get("model") or {})]
    notes.append(f"model type {kind or '?'}")
    return []


# ------------------------------------------------------------------ raster

def rasterize(quads, size=SIZE):
    """Depth-sorted, alpha-blended nearest-neighbour fill of the quads.
    View space: x right, y up, z toward the viewer; [-0.5, 0.5] = the icon."""
    frags = {}
    # A model larger than the slot (banners) is scaled down to fit, around
    # the icon's centre; everything else is drawn at the game's size.
    reach = max((max(abs(v[0]), abs(v[1])) for q in quads for v in q.verts), default=0.5)
    fit = 0.5 / reach if reach > 0.5 else 1.0
    for seq, q in enumerate(quads):
        px = [((v[0] * fit + 0.5) * size, (0.5 - v[1] * fit) * size, v[2]) for v in q.verts]
        img = q.image
        W, H = img.width, img.height
        pix = img.load()
        tris = ((0, 1, 2), (0, 2, 3))
        xs = [p[0] for p in px]
        ys = [p[1] for p in px]
        x_lo, x_hi = max(0, int(math.floor(min(xs)))), min(size - 1, int(math.ceil(max(xs))))
        y_lo, y_hi = max(0, int(math.floor(min(ys)))), min(size - 1, int(math.ceil(max(ys))))
        pre = []
        for a, b, c in tris:
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = px[a], px[b], px[c]
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-12:
                continue
            pre.append((a, b, c, ax, ay, az, bx, by, bz, cx, cy, cz, den))
        if not pre:
            continue
        for y in range(y_lo, y_hi + 1):
            sy = y + 0.5
            for x in range(x_lo, x_hi + 1):
                sx = x + 0.5
                for a, b, c, ax, ay, az, bx, by, bz, cx, cy, cz, den in pre:
                    w0 = ((by - cy) * (sx - cx) + (cx - bx) * (sy - cy)) / den
                    w1 = ((cy - ay) * (sx - cx) + (ax - cx) * (sy - cy)) / den
                    w2 = 1 - w0 - w1
                    if w0 < -1e-9 or w1 < -1e-9 or w2 < -1e-9:
                        continue
                    ua, va = q.uvs[a]
                    ub, vb = q.uvs[b]
                    uc, vc = q.uvs[c]
                    u = w0 * ua + w1 * ub + w2 * uc
                    v = w0 * va + w1 * vb + w2 * vc
                    tx = min(W - 1, max(0, int(math.floor(u * W))))
                    ty = min(H - 1, max(0, int(math.floor(v * H))))
                    r, g, bl, al = pix[tx, ty]
                    if al == 0:
                        break
                    if q.tint:
                        r, g, bl = (r * q.tint[0] // 255, g * q.tint[1] // 255,
                                    bl * q.tint[2] // 255)
                    if q.shade != 1.0:
                        r, g, bl = int(r * q.shade), int(g * q.shade), int(bl * q.shade)
                    z = w0 * az + w1 * bz + w2 * cz
                    frags.setdefault((x, y), []).append((round(z, 4), seq, (r, g, bl, al)))
                    break
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    opx = out.load()
    for (x, y), fs in frags.items():
        fs.sort()
        r = g = b = 0.0
        a = 0.0
        for _, _, (fr, fg, fb, fa) in fs:
            al = fa / 255
            r = fr * al + r * (1 - al)
            g = fg * al + g * (1 - al)
            b = fb * al + b * (1 - al)
            a = al + a * (1 - al)
        if a > 0:
            opx[x, y] = (int(round(r / a)) if a < 1 else int(round(r)),
                         int(round(g / a)) if a < 1 else int(round(g)),
                         int(round(b / a)) if a < 1 else int(round(b)),
                         int(round(a * 255)))
    return out


# ------------------------------------------------------------------ render

@dataclass
class Result:
    image: Image.Image
    problems: list         # why the icon may be wrong; [] = rendered properly
    fallback: bool = False


CENTER = mat_translate(-0.5, -0.5, -0.5)
PX = mat_scale(1 / 16, 1 / 16, 1 / 16)


def _view(display_gui, matrix):
    """pixel model space -> view: display * (transformation * (p / 16) - 0.5)."""
    return mat_mul(mat_mul(mat_mul(mat_display(display_gui), CENTER), matrix), PX)


def _particle_fallback(assets, model, problems):
    ref = texture_ref(model, model.textures.get("particle", "")) if model else None
    img = assets.texture(ref) if ref else None
    if img is None:
        problems.append("no particle texture")
        return Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    return img.resize((SIZE, SIZE), Image.NEAREST)


def render_item(assets, item_id) -> Result:
    definition = assets.item_definition(item_id)
    problems = []
    if definition is None:
        return Result(Image.new("RGBA", (SIZE, SIZE)), ["no item definition"], True)
    parts = collect_parts(definition.get("model"), problems)
    quads = []
    fallback_model = None
    for part in parts:
        model = resolve_model(assets, part.ref)
        problems += model.missing
        gui = model.display.get("gui")
        if part.kind == "special":
            got = special_boxes(assets, part.spec)
            if got is None:
                problems.append(f"special {strip_ns(part.spec.get('type', '?'))}")
                fallback_model = fallback_model or model
                continue
            to_view = _view(gui, part.matrix)
            shade_on = model.gui_light == "side"
            for img, boxes, tint in got:
                for corners, uvs, n in boxes:
                    _emit(quads, corners, uvs, n, img, tint, shade_on, to_view, mat_identity())
            continue
        tints = [tint_color(assets, t) for t in part.tints]
        to_view = _view(gui, part.matrix)
        if model.generated:
            quads += flat_quads(assets, model, tints, to_view, problems)
        elif model.elements:
            quads += block_quads(assets, model, tints, to_view, problems)
        else:
            problems.append(f"model {strip_ns(part.ref)} has no elements")
            fallback_model = fallback_model or model
    if not quads:
        return Result(_particle_fallback(assets, fallback_model, problems), problems or
                      ["nothing to draw"], True)
    return Result(rasterize(quads), problems, False)
