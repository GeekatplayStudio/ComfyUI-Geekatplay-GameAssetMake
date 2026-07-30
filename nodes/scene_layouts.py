# =============================================================
# Geekatplay GameAssetMake — Structured scene layout generators
# (c) Geekatplay Studio / Vladimir Chopine
#
# A village should read as a village: houses lined up along a street,
# facing it, with a square in the middle. A dungeon should read as a
# dungeon: rooms joined by corridors, pillars in the corners, torches
# on the walls. Scattering props inside a circle produces neither.
#
# Each generator returns (assets, layout_plan). The plan carries the
# STRUCTURE — streets, rooms, corridors, the built envelope — which is
# used twice downstream:
#   * the Layout Map draws it, so the plan is verifiable before
#     anything is generated;
#   * the Placement Manager fits the built envelope onto flat ground
#     and levels pads under the buildings.
#
# Convention: metres, origin at the scene centre, +x right / +y up,
# yaw 0 deg = facing +x, counter-clockwise.
# =============================================================
import math
import random

# Category archetypes shared with the Scene Director.
CATEGORY_DEFAULTS = {
    "hero":        {"rig": "biped", "collision": "capsule", "scale": [1.0, 1.0, 1.8]},
    "npc":         {"rig": "biped", "collision": "capsule", "scale": [1.0, 1.0, 1.8]},
    "enemy":       {"rig": "quadruped", "collision": "box", "scale": [1.2, 1.2, 1.0]},
    "boss":        {"rig": "biped", "collision": "box", "scale": [2.5, 2.5, 3.0]},
    "structure":   {"rig": "none", "collision": "box", "scale": [6.0, 6.0, 5.0]},
    "environment_prop": {"rig": "none", "collision": "box", "scale": [1.0, 1.0, 1.2]},
    "interactable": {"rig": "none", "collision": "box", "scale": [0.8, 0.8, 0.8]},
    "weapon":      {"rig": "none", "collision": "box", "scale": [0.3, 0.3, 1.2]},
}

# How each asset meets the ground.
#   building   — must stand upright on level ground; gets a flattened pad
#   prop       — small, tilts to follow the surface
#   vegetation — tilts slightly, never gets a pad
#   landmark   — upright, pad, and the layout is anchored on it
#   character  — upright, no pad
ROLE_DEFAULTS = {
    "building":   {"align_to_ground": False, "needs_flat_pad": True},
    "landmark":   {"align_to_ground": False, "needs_flat_pad": True},
    "prop":       {"align_to_ground": True,  "needs_flat_pad": False},
    "vegetation": {"align_to_ground": True,  "needs_flat_pad": False},
    "character":  {"align_to_ground": False, "needs_flat_pad": False},
}


def make_asset(name, category, x_m, y_m, yaw=0.0, scale=None,
               prompt_extra="", role="prop"):
    d = CATEGORY_DEFAULTS.get(category, CATEGORY_DEFAULTS["environment_prop"])
    r = ROLE_DEFAULTS.get(role, ROLE_DEFAULTS["prop"])
    sc = scale or d["scale"]
    return {
        "name": name,
        "category": category,
        "rig_type": d["rig"],
        "collision_type": d["collision"],
        "scale_override": list(sc),
        "position_m": [round(x_m, 2), round(y_m, 2)],
        "yaw_deg": round(yaw % 360.0, 1),
        "prompt_extra": prompt_extra,
        "placement_role": role,
        "align_to_ground": r["align_to_ground"],
        # pad big enough to cover the footprint with a margin
        "pad_radius_m": round(max(sc[0], sc[1]) * 0.75 + 1.0, 2) if r["needs_flat_pad"] else 0.0,
    }


def scatter(rng, count, half_m, min_dist_m, forbid=(), tries=40):
    """
    Blue-noise-ish points: keeps a minimum separation and stays out of the
    forbidden circles (streets, plazas, room interiors). Plain uniform random
    clumps badly and drops props on top of buildings.
    """
    pts = []
    for _ in range(count):
        for _try in range(tries):
            x, y = rng.uniform(-half_m, half_m), rng.uniform(-half_m, half_m)
            if any(math.hypot(x - fx, y - fy) < fr for fx, fy, fr in forbid):
                continue
            if any(math.hypot(x - px, y - py) < min_dist_m for px, py in pts):
                continue
            pts.append((x, y))
            break
        else:
            pts.append((rng.uniform(-half_m, half_m), rng.uniform(-half_m, half_m)))
    return pts


def _cycle(items, i):
    return items[i % len(items)]


# ---------------------------------------------------------------- village
VILLAGE_HOUSES = [
    ("Timber Cottage", "timber-framed cottage with thatched roof"),
    ("Stone Cottage", "small stone cottage with wooden shutters"),
    ("Longhouse", "long timber house with a steep shingled roof"),
    ("Blacksmith Workshop", "blacksmith workshop with a forge chimney"),
    ("Village Bakery", "bakery with a stone oven chimney"),
    ("Storage Barn", "wooden barn with large double doors"),
]
VILLAGE_FRONTAGE = [
    ("Water Barrel", "environment_prop", "wooden water barrel", [0.8, 0.8, 1.0]),
    ("Firewood Pile", "environment_prop", "stacked firewood pile", [1.4, 0.8, 1.0]),
    ("Hay Cart", "environment_prop", "wooden hay cart", [2.6, 1.4, 1.6]),
    ("Market Stall", "structure", "wooden market stall with canvas awning", [2.5, 2.0, 2.5]),
    ("Lantern Post", "environment_prop", "lantern on a wooden post", [0.4, 0.4, 3.0]),
    ("Wooden Fence", "environment_prop", "wooden fence segment", [2.5, 0.3, 1.2]),
]


def village_layout(rng, count, scene_size_m, theme="village"):
    """Houses in rows facing a main street, a square at the crossing, church closing the view."""
    half = scene_size_m * 0.42
    street_w = 8.0
    street_len = min(half * 1.8, scene_size_m * 0.8)
    house_w, house_d = 8.0, 7.0
    # front of a house sits this far back from the street centre line
    setback = street_w / 2.0 + 2.5
    row_y = setback + house_d / 2.0
    plaza_r = 13.0

    assets, plan_roads, plan_plazas = [], [], []

    plan_roads.append({"points": [[-street_len / 2, 0.0], [street_len / 2, 0.0]],
                       "width_m": street_w, "name": "Main Street"})
    cross_len = min(half * 1.2, scene_size_m * 0.55)
    plan_roads.append({"points": [[0.0, -cross_len / 2], [0.0, cross_len / 2]],
                       "width_m": street_w * 0.75, "name": "Cross Lane"})
    plan_plazas.append({"center": [0.0, 0.0], "radius_m": plaza_r, "name": "Village Square"})

    # The square anchors the village.
    assets.append(make_asset(f"{theme.title()} Well", "interactable", 0, 0, 0,
                             scale=[1.6, 1.6, 2.0], role="landmark",
                             prompt_extra="stone water well with a wooden roof and bucket"))

    # Church closes the far end of the main street and faces back down it.
    church_x = street_len / 2 - 12.0
    assets.append(make_asset(f"{theme.title()} Church", "structure", church_x, 0.0, 180.0,
                             scale=[9.0, 13.0, 14.0], role="building",
                             prompt_extra="small stone church with a bell tower"))

    # Houses in two rows, set back from the street, each facing it.
    budget = max(0, count - len(assets))
    n_houses = max(2, min(10, int(budget * 0.45)))
    slots, step = [], house_w + 5.0
    per_side = math.ceil(n_houses / 2)
    for i in range(per_side):
        # start outside the square, march outward along the street
        x = plaza_r + house_w / 2 + i * step
        for side in (1, -1):
            if x > street_len / 2 - 16.0:
                continue
            slots.append((-x if side < 0 else x, side))
    rng.shuffle(slots)

    for i in range(min(n_houses, len(slots))):
        x, side = slots[i]
        y = row_y * (1 if (i % 2 == 0) else -1)
        nm, extra = _cycle(VILLAGE_HOUSES, i)
        # north row faces -y (yaw 270), south row faces +y (yaw 90) — onto the street
        yaw = 270.0 if y > 0 else 90.0
        assets.append(make_asset(f"{nm} {i + 1}", "structure",
                                 x + rng.uniform(-1.0, 1.0), y, yaw,
                                 scale=[house_w, house_d, 6.0], role="building",
                                 prompt_extra=extra))

    # Frontage props: on the street side of each house, never in the roadway.
    houses = [a for a in assets if a["placement_role"] == "building" and "Church" not in a["name"]]
    fi = 0
    while len(assets) < count and houses:
        h = houses[fi % len(houses)]
        hx, hy = h["position_m"]
        nm, cat, extra, sc = _cycle(VILLAGE_FRONTAGE, fi)
        front = setback - 1.2                       # just off the kerb
        y = front if hy > 0 else -front
        x = hx + rng.uniform(-house_w / 2, house_w / 2)
        assets.append(make_asset(f"{nm} {fi + 1}", cat, x, y,
                                 (270.0 if y > 0 else 90.0) + rng.uniform(-12, 12),
                                 scale=sc, role="prop", prompt_extra=extra))
        fi += 1
        if fi > count * 2:
            break

    # Trees and fields fill the land OUTSIDE the built strip.
    forbid = [(0.0, 0.0, plaza_r + 4.0)]
    forbid += [(a["position_m"][0], a["position_m"][1], 7.0) for a in assets]
    for i, (x, y) in enumerate(scatter(rng, max(0, count - len(assets)), half, 9.0, forbid)):
        if abs(y) < row_y + house_d:      # keep the street corridor clear
            y += math.copysign(row_y + house_d + 4.0, y or 1.0)
        assets.append(make_asset(f"Old Oak Tree {i + 1}", "environment_prop",
                                 x, y, rng.uniform(0, 360), scale=[4.0, 4.0, 8.0],
                                 role="vegetation", prompt_extra="large old oak tree"))

    plan = {
        "kind": "village",
        "roads": plan_roads,
        "plazas": plan_plazas,
        "rooms": [],
        "corridors": [],
        "build_area": {"center": [0.0, 0.0],
                       "radius_m": round(min(half, street_len / 2 + 10.0), 1)},
    }
    terrain = ("gentle rolling green meadow with a wide flat clearing in the centre for a "
               "village, a dirt road crossing it, low hills only near the edges")
    sky = ("equirectangular 360 degree panorama of a clear late-afternoon sky over "
           "countryside, warm golden light, scattered cumulus clouds")
    return assets, plan, terrain, sky


# ---------------------------------------------------------------- dungeon
DUNGEON_ROOM_PROPS = [
    ("Treasure Chest", "interactable", "iron-bound treasure chest", [1.1, 0.7, 0.8]),
    ("Skeleton Pile", "environment_prop", "pile of old bones", [1.2, 1.2, 0.4]),
    ("Stone Sarcophagus", "environment_prop", "cracked stone sarcophagus", [2.4, 1.1, 1.0]),
    ("Rubble Heap", "environment_prop", "heap of collapsed masonry", [1.6, 1.6, 0.8]),
]


def dungeon_layout(rng, count, scene_size_m):
    """A room grid joined by corridors — pillars in the corners, torches on the walls."""
    half = scene_size_m * 0.42
    room_w, room_d = 18.0, 15.0
    gap = 12.0                                    # corridor length between rooms
    pitch_x, pitch_y = room_w + gap, room_d + gap

    cols = max(2, min(3, int((half * 2) // pitch_x)))
    rows = max(2, min(3, int((half * 2) // pitch_y)))
    ox = -(cols - 1) * pitch_x / 2.0
    oy = -(rows - 1) * pitch_y / 2.0
    centers = [(ox + c * pitch_x, oy + r * pitch_y, r, c)
               for r in range(rows) for c in range(cols)]

    assets, rooms, corridors = [], [], []
    for cx, cy, r, c in centers:
        rooms.append({"rect": [round(cx - room_w / 2, 2), round(cy - room_d / 2, 2),
                               round(cx + room_w / 2, 2), round(cy + room_d / 2, 2)],
                      "name": f"Chamber {r * cols + c + 1}"})
    # corridors: east neighbour and north neighbour of every room
    by_rc = {(r, c): (cx, cy) for cx, cy, r, c in centers}
    for (r, c), (cx, cy) in by_rc.items():
        if (r, c + 1) in by_rc:
            nx, ny = by_rc[(r, c + 1)]
            corridors.append({"points": [[cx + room_w / 2, cy], [nx - room_w / 2, ny]],
                              "width_m": 4.0})
        if (r + 1, c) in by_rc:
            nx, ny = by_rc[(r + 1, c)]
            corridors.append({"points": [[cx, cy + room_d / 2], [nx, ny - room_d / 2]],
                              "width_m": 4.0})

    # The centre room holds the altar.
    mid = centers[len(centers) // 2]
    assets.append(make_asset("Central Altar", "interactable", mid[0], mid[1], 0.0,
                             scale=[2.2, 1.4, 1.2], role="landmark",
                             prompt_extra="ancient carved stone altar"))

    # Pillars inset from each room's corners — that reads as built architecture.
    inset_x, inset_y = room_w / 2 - 3.0, room_d / 2 - 3.0
    pillar_budget = max(4, int((count - 1) * 0.35))
    placed = 0
    # Corner-major, not room-major: one corner in EVERY chamber before starting
    # the next corner. Room-major spent the whole budget on the first two rooms
    # and left the rest of the dungeon bare.
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        if placed >= pillar_budget:
            break
        for cx, cy, _r, _c in centers:
            if placed >= pillar_budget:
                break
            placed += 1
            assets.append(make_asset(f"Stone Pillar {placed}", "structure",
                                     cx + sx * inset_x, cy + sy * inset_y, 0.0,
                                     scale=[1.3, 1.3, 5.0], role="building",
                                     prompt_extra="cracked stone support pillar"))

    # Doorway frames where corridors meet rooms.
    for i, corr in enumerate(corridors, 1):
        if len(assets) >= count:
            break
        (ax, ay), (bx, by) = corr["points"]
        horizontal = abs(bx - ax) > abs(by - ay)
        assets.append(make_asset(f"Dungeon Doorway {i}", "structure",
                                 ax, ay, 0.0 if horizontal else 90.0,
                                 scale=[1.0, 4.0, 4.0] if horizontal else [4.0, 1.0, 4.0],
                                 role="building", prompt_extra="arched stone doorway frame"))

    # Torches along the corridor walls.
    ti = 0
    for corr in corridors:
        if len(assets) >= count:
            break
        (ax, ay), (bx, by) = corr["points"]
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
        horizontal = abs(bx - ax) > abs(by - ay)
        off = corr["width_m"] / 2.0
        ti += 1
        assets.append(make_asset(f"Wall Torch {ti}", "environment_prop",
                                 mx + (0 if horizontal else off),
                                 my + (off if horizontal else 0),
                                 90.0 if horizontal else 0.0,
                                 scale=[0.4, 0.4, 1.0], role="prop",
                                 prompt_extra="iron wall torch sconce with flame"))

    # Remaining props hug the room walls rather than floating mid-floor.
    pi = 0
    while len(assets) < count:
        cx, cy, _r, _c = centers[pi % len(centers)]
        nm, cat, extra, sc = _cycle(DUNGEON_ROOM_PROPS, pi)
        wall = pi % 4
        if wall == 0:
            x, y, yaw = cx + rng.uniform(-inset_x, inset_x), cy - room_d / 2 + 1.4, 90.0
        elif wall == 1:
            x, y, yaw = cx + room_w / 2 - 1.4, cy + rng.uniform(-inset_y, inset_y), 180.0
        elif wall == 2:
            x, y, yaw = cx + rng.uniform(-inset_x, inset_x), cy + room_d / 2 - 1.4, 270.0
        else:
            x, y, yaw = cx - room_w / 2 + 1.4, cy + rng.uniform(-inset_y, inset_y), 0.0
        assets.append(make_asset(f"{nm} {pi + 1}", cat, x, y, yaw,
                                 scale=sc, role="prop", prompt_extra=extra))
        pi += 1
        if pi > count * 3:
            break

    plan = {
        "kind": "dungeon",
        "roads": [],
        "plazas": [],
        "rooms": rooms,
        "corridors": corridors,
        "build_area": {"center": [0.0, 0.0],
                       "radius_m": round(math.hypot(cols * pitch_x, rows * pitch_y) / 2.0, 1)},
    }
    terrain = ("flat cracked stone dungeon floor, almost level throughout, "
               "shallow sunken centre, broken raised rubble only at the outer edges")
    sky = ("equirectangular 360 degree panorama of a dark stormy night sky, "
           "moonlight through heavy clouds, ominous atmosphere")
    return assets, plan, terrain, sky


# ------------------------------------------------------------- forest camp
def camp_layout(rng, count, scene_size_m):
    """Tents ringing a campfire in a clearing; forest packed outside it."""
    half = scene_size_m * 0.42
    clearing_r = 14.0
    assets = [make_asset("Campfire", "interactable", 0, 0, 0, scale=[1.8, 1.8, 0.6],
                         role="landmark", prompt_extra="stone-ring campfire with burning logs")]

    n_tents = max(3, min(6, int(count * 0.25)))
    for i in range(n_tents):
        ang = 2 * math.pi * i / n_tents + rng.uniform(-0.12, 0.12)
        r = clearing_r * 0.62
        x, y = r * math.cos(ang), r * math.sin(ang)
        assets.append(make_asset(f"Canvas Tent {i + 1}", "structure", x, y,
                                 math.degrees(math.atan2(-y, -x)),   # opening faces the fire
                                 scale=[3.2, 3.2, 2.6], role="building",
                                 prompt_extra="canvas camping tent"))

    camp_props = [("Log Bench", "environment_prop", "split log bench", [2.0, 0.5, 0.5]),
                  ("Supply Crate", "interactable", "wooden supply crate", [0.9, 0.9, 0.9]),
                  ("Cooking Pot", "interactable", "iron cooking pot on a tripod", [0.8, 0.8, 1.0])]
    for i in range(min(3, max(0, count - len(assets)))):
        ang = 2 * math.pi * (i + 0.5) / 3
        r = clearing_r * 0.34
        nm, cat, extra, sc = camp_props[i % len(camp_props)]
        assets.append(make_asset(f"{nm} {i + 1}", cat, r * math.cos(ang), r * math.sin(ang),
                                 math.degrees(math.atan2(-math.sin(ang), -math.cos(ang))),
                                 scale=sc, role="prop", prompt_extra=extra))

    forbid = [(0.0, 0.0, clearing_r)]
    for i, (x, y) in enumerate(scatter(rng, max(0, count - len(assets)), half, 7.0, forbid)):
        assets.append(make_asset(f"Pine Tree {i + 1}", "environment_prop", x, y,
                                 rng.uniform(0, 360), scale=[3.0, 3.0, 9.0],
                                 role="vegetation", prompt_extra="tall pine tree"))

    plan = {"kind": "camp", "roads": [], "rooms": [], "corridors": [],
            "plazas": [{"center": [0.0, 0.0], "radius_m": clearing_r, "name": "Clearing"}],
            "build_area": {"center": [0.0, 0.0], "radius_m": clearing_r + 4.0}}
    terrain = ("forest floor terrain with a flat clearing in the centre, gentle slopes around it")
    sky = ("equirectangular 360 degree panorama of a dawn sky above a forest, "
           "soft mist, pale sunrise")
    return assets, plan, terrain, sky


# ------------------------------------------------------------------ generic
def generic_layout(rng, count, scene_size_m, scene_prompt):
    """No template matched: a central landmark with evenly spread props around it."""
    half = scene_size_m * 0.42
    assets = [make_asset("Central Landmark", "structure", 0, 0, 0, role="landmark",
                         prompt_extra=f"central landmark of {scene_prompt}")]
    forbid = [(0.0, 0.0, 10.0)]
    for i, (x, y) in enumerate(scatter(rng, max(0, count - 1), half, 8.0, forbid)):
        assets.append(make_asset(f"Scene Prop {i + 1}", "environment_prop", x, y,
                                 rng.uniform(0, 360), role="prop",
                                 prompt_extra=f"prop belonging to {scene_prompt}"))
    plan = {"kind": "generic", "roads": [], "rooms": [], "corridors": [], "plazas": [],
            "build_area": {"center": [0.0, 0.0], "radius_m": round(half, 1)}}
    terrain = f"natural terrain suited to: {scene_prompt}"
    sky = (f"equirectangular 360 degree panorama sky matching the mood of: {scene_prompt}, "
           f"seamless horizontal wrap")
    return assets, plan, terrain, sky


LAYOUT_KEYWORDS = (
    (("village", "town", "hamlet", "settlement", "market"), "village"),
    (("dungeon", "castle", "fort", "crypt", "ruin", "catacomb", "temple"), "dungeon"),
    (("forest", "camp", "woods", "campsite", "clearing"), "camp"),
)


def detect_layout_kind(scene_prompt):
    p = scene_prompt.lower()
    for keywords, kind in LAYOUT_KEYWORDS:
        if any(k in p for k in keywords):
            return kind
    return "generic"


def build_layout(scene_prompt, count, scene_size_m, seed, kind="auto"):
    """
    (assets, layout_plan, terrain_description, skydome_prompt) for the scene.
    `kind` forces a template; "auto" picks one from the prompt keywords.
    """
    rng = random.Random(seed)
    if kind in (None, "", "auto"):
        kind = detect_layout_kind(scene_prompt)

    if kind == "village":
        theme = "medieval village" if "medieval" in scene_prompt.lower() else "village"
        assets, plan, terrain, sky = village_layout(rng, count, scene_size_m, theme)
    elif kind == "dungeon":
        assets, plan, terrain, sky = dungeon_layout(rng, count, scene_size_m)
    elif kind == "camp":
        assets, plan, terrain, sky = camp_layout(rng, count, scene_size_m)
    else:
        assets, plan, terrain, sky = generic_layout(rng, count, scene_size_m, scene_prompt)

    return assets[:count], plan, terrain, sky
