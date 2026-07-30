# =============================================================
# Geekatplay GameAssetMake — Asset Placement Manager
# (c) Geekatplay Studio / Vladimir Chopine
#
# The single authority for WHAT goes into the engine, in WHAT ORDER,
# at WHAT SIZE, and WHERE:
#   - merges terrain / skydome / asset manifests into ONE ordered
#     payload (terrain first, then sky, then assets)
#   - stamps every asset with target_size_m (its intended real-world
#     size) so the Unreal importer can measure the imported mesh and
#     normalize its scale exactly — AI meshes arrive in random units
#   - snaps each asset's Z onto the terrain heightfield (samples the
#     16-bit heightmap) so things sit ON the ground, not inside hills
#   - resolves overlaps by nudging colliding assets apart
# Feed its output into ONE engine bridge node.
# =============================================================
import os
import json
import math

M_TO_CM = 100.0


def _load_heightfield(terrain_item):
    """Returns (field 2D list 0..1, size_m, height_m) or None."""
    path = terrain_item.get("heightmap16_path", "")
    if not path or not os.path.exists(path):
        return None
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(path)
        arr = np.asarray(img).astype("float64")
        arr /= 65535.0 if arr.max() > 255 else max(arr.max(), 1.0)
        return (arr,
                float(terrain_item.get("terrain_world_size_m", 500.0)),
                float(terrain_item.get("terrain_height_m", 60.0)))
    except Exception as exc:
        print(f"[Placement Manager] Could not read heightfield ({exc}); Z snapping disabled.")
        return None


def _ground_height_m(field_data, x_m, y_m):
    """Samples ground height (meters) at world x/y (terrain centered on origin)."""
    field, size_m, height_m = field_data
    h, w = field.shape
    u = min(max((x_m + size_m / 2.0) / size_m, 0.0), 1.0)
    v = min(max((y_m + size_m / 2.0) / size_m, 0.0), 1.0)
    col = min(int(u * (w - 1)), w - 1)
    row = min(int((1.0 - v) * (h - 1)), h - 1)  # image rows go top-down
    return float(field[row, col]) * height_m


def _footprint_radius_m(item):
    ts = item.get("target_size_m") or item.get("scale_override") or [1, 1, 1]
    return max(0.4, max(ts[0], ts[1]) / 2.0)


def _footprint_samples(field_data, x_m, y_m, radius_m):
    """Ground heights under an asset's footprint: centre plus four corners."""
    r = max(0.25, radius_m)
    pts = [(x_m, y_m), (x_m - r, y_m - r), (x_m + r, y_m - r),
           (x_m - r, y_m + r), (x_m + r, y_m + r)]
    return [_ground_height_m(field_data, px, py) for px, py in pts]


def _surface_normal(field_data, x_m, y_m, step_m=1.5):
    """
    (pitch_deg, roll_deg, slope_deg) of the ground under a point.
    Central differences on the heightfield; used to lay props along the slope
    instead of standing them bolt upright in the middle of a hillside.
    """
    hx0 = _ground_height_m(field_data, x_m - step_m, y_m)
    hx1 = _ground_height_m(field_data, x_m + step_m, y_m)
    hy0 = _ground_height_m(field_data, x_m, y_m - step_m)
    hy1 = _ground_height_m(field_data, x_m, y_m + step_m)
    dzdx = (hx1 - hx0) / (2.0 * step_m)
    dzdy = (hy1 - hy0) / (2.0 * step_m)
    pitch = math.degrees(math.atan(dzdx))
    roll = math.degrees(math.atan(dzdy))
    slope = math.degrees(math.atan(math.hypot(dzdx, dzdy)))
    return round(-pitch, 2), round(roll, 2), round(slope, 2)


def _site_score(field_data, cx_m, cy_m, radius_m, rings=3, per_ring=12):
    """
    How buildable the ground is around a point: peak-to-trough height spread
    plus the worst local slope. Lower is flatter.
    """
    heights, worst_slope = [_ground_height_m(field_data, cx_m, cy_m)], 0.0
    for ring in range(1, rings + 1):
        r = radius_m * ring / rings
        for i in range(per_ring):
            ang = 2.0 * math.pi * i / per_ring
            px, py = cx_m + r * math.cos(ang), cy_m + r * math.sin(ang)
            heights.append(_ground_height_m(field_data, px, py))
            worst_slope = max(worst_slope, _surface_normal(field_data, px, py)[2])
    spread = max(heights) - min(heights)
    return spread + worst_slope * 0.5, spread, worst_slope


def find_flat_site(field_data, radius_m, search_steps=9):
    """
    Best centre for the built area. The terrain heightmap is generated from a
    text description, so nothing guarantees the middle of the map is level —
    dropping a village onto whatever happens to be at the origin is what puts
    houses half-buried in a hillside. Searching for the flattest patch that
    fits the built envelope, then moving the whole layout there, keeps the
    street/room geometry intact while putting it on ground it can stand on.
    """
    _field, size_m, _height_m = field_data
    limit = max(0.0, size_m / 2.0 - radius_m)
    if limit <= 0.0:
        return (0.0, 0.0) + _site_score(field_data, 0.0, 0.0, radius_m)[1:]

    best, best_score = (0.0, 0.0), None
    best_detail = (0.0, 0.0)
    for iy in range(search_steps):
        for ix in range(search_steps):
            cx = -limit + 2.0 * limit * ix / max(1, search_steps - 1)
            cy = -limit + 2.0 * limit * iy / max(1, search_steps - 1)
            score, spread, slope = _site_score(field_data, cx, cy, radius_m)
            if best_score is None or score < best_score:
                best, best_score, best_detail = (cx, cy), score, (spread, slope)
    return best[0], best[1], best_detail[0], best_detail[1]


class AssetPlacementManagerNode:
    """
    Merges all scene manifests into one ordered, scale-correct,
    terrain-snapped payload for the engine bridge.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "snap_to_terrain": ("BOOLEAN", {"default": True,
                    "label_on": "Snap asset Z to terrain surface", "label_off": "Keep manifest Z"}),
                "resolve_overlaps": ("BOOLEAN", {"default": True,
                    "label_on": "Nudge overlapping assets apart", "label_off": "Keep positions as planned"}),
                "normalize_scales": ("BOOLEAN", {"default": True,
                    "label_on": "Engine normalizes mesh size to target_size_m",
                    "label_off": "Raw import scale"}),
            },
            "optional": {
                "assets_manifest_json": ("STRING", {"forceInput": True}),
                "terrain_manifest_json": ("STRING", {"forceInput": True}),
                "skydome_manifest_json": ("STRING", {"forceInput": True}),
                "extra_manifest_json": ("STRING", {"forceInput": True}),
                # Scene Director's street/room plan. Optional: without it the
                # per-asset terrain fitting below still runs, only the "move the
                # whole built area to flat ground" step is skipped.
                "scene_layout_json": ("STRING", {"forceInput": True}),
                # Appended at the END: widgets_values is a positional array, so
                # inserting these earlier would shift the values of every
                # workflow saved before they existed.
                "fit_layout_to_terrain": ("BOOLEAN", {"default": True,
                    "label_on": "Move the built area onto the flattest ground",
                    "label_off": "Keep the layout centred on the origin"}),
                "level_building_pads": ("BOOLEAN", {"default": True,
                    "label_on": "Level a pad under each building",
                    "label_off": "Buildings sit on raw terrain"}),
                "align_props_to_slope": ("BOOLEAN", {"default": True,
                    "label_on": "Tilt props to follow the ground",
                    "label_off": "Everything stands upright"}),
                "max_building_slope_deg": ("FLOAT", {"default": 12.0, "min": 0.0, "max": 45.0,
                    "step": 1.0,
                    "tooltip": "Buildings on ground steeper than this are reported and, when "
                               "pad levelling is on, get a flattened pad to stand on."}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("completed_3d_manifest_json", "placement_report", "asset_count")
    FUNCTION = "manage_placement"
    CATEGORY = "Geekatplay GameAssetMake/Engine-Bridge"

    def manage_placement(self, snap_to_terrain=True, resolve_overlaps=True, normalize_scales=True,
                         assets_manifest_json="", terrain_manifest_json="",
                         skydome_manifest_json="", extra_manifest_json="",
                         scene_layout_json="", fit_layout_to_terrain=True,
                         level_building_pads=True, align_props_to_slope=True,
                         max_building_slope_deg=12.0):
        def parse(src):
            if not src:
                return []
            try:
                data = json.loads(src)
                return data if isinstance(data, list) else []
            except Exception:
                return []

        terrain = parse(terrain_manifest_json)
        skydome = parse(skydome_manifest_json)
        assets = parse(assets_manifest_json) + parse(extra_manifest_json)

        report = []
        field_data = None
        if terrain:
            field_data = _load_heightfield(terrain[0])
            if field_data is not None:
                report.append(f"Terrain heightfield loaded ({terrain[0].get('name')}): "
                              f"{field_data[1]:.0f}m world, {field_data[2]:.0f}m height range.")

        # ---- fit the whole built area onto flat ground ----
        # Done BEFORE any per-asset work: it is a rigid translation of the entire
        # layout, so the street/room geometry the Scene Director built survives
        # intact — only where it sits on the map changes.
        layout_plan = {}
        if scene_layout_json:
            try:
                layout_plan = (json.loads(scene_layout_json) or {}).get("layout_plan") or {}
            except Exception:
                layout_plan = {}

        shift_x_m = shift_y_m = 0.0
        build_area = layout_plan.get("build_area") or {}
        if fit_layout_to_terrain and field_data is not None and build_area and assets:
            radius_m = float(build_area.get("radius_m", 0.0))
            if radius_m > 0.0:
                cx, cy, spread, slope = find_flat_site(field_data, radius_m)
                origin = build_area.get("center", [0.0, 0.0])
                shift_x_m = cx - float(origin[0])
                shift_y_m = cy - float(origin[1])
                if abs(shift_x_m) > 0.01 or abs(shift_y_m) > 0.01:
                    for item in assets:
                        p = item.get("world_placement_offset", [0.0, 0.0, 0.0])
                        p[0] += shift_x_m * M_TO_CM
                        p[1] += shift_y_m * M_TO_CM
                    report.append(
                        f"Moved the {layout_plan.get('kind', 'scene')} layout "
                        f"({radius_m:.0f} m across) to the flattest ground at "
                        f"({cx:+.0f}, {cy:+.0f}) m — {spread:.1f} m of height spread, "
                        f"worst slope {slope:.0f}°.")
                else:
                    report.append(f"Built area already sits on the flattest ground "
                                  f"({spread:.1f} m spread, worst slope {slope:.0f}°).")
                layout_plan["site_offset_m"] = [round(shift_x_m, 2), round(shift_y_m, 2)]

        # ---- per-asset: sizes, ground fitting, slope alignment ----
        steep, padded, tilted = [], 0, 0
        for item in assets:
            # intended real-world size: prefer explicit target_size_m; legacy
            # manifests carried it in scale_override (meters)
            if "target_size_m" not in item:
                sc = item.get("scale_override", [1.0, 1.0, 1.0])
                item["target_size_m"] = sc
            if normalize_scales:
                # engine measures the imported mesh and scales it to target_size_m;
                # actor scale multiplier stays neutral
                item["normalize_to_target"] = True
                item["scale_override"] = [1.0, 1.0, 1.0]

            pos = item.get("world_placement_offset", [0.0, 0.0, 0.0])
            x_m, y_m = pos[0] / M_TO_CM, pos[1] / M_TO_CM
            half_h_m = item["target_size_m"][2] / 2.0
            role = item.get("placement_role", "prop")

            ground_m = 0.0
            if snap_to_terrain and field_data is not None:
                # Sample the whole footprint, not just the pivot. A single centre
                # sample leaves the uphill corner of anything wide buried in the
                # slope, which is what makes buildings look sunk into hillsides.
                foot_r = _footprint_radius_m(item)
                samples = _footprint_samples(field_data, x_m, y_m, foot_r)
                pitch, roll, slope_deg = _surface_normal(field_data, x_m, y_m)
                item["ground_slope_deg"] = slope_deg

                if role in ("building", "landmark") and level_building_pads \
                        and item.get("pad_radius_m", 0.0) > 0.0:
                    # Level a pad: the engine flattens the terrain to this height
                    # over pad_radius_m, and the building stands upright on it.
                    ground_m = sum(samples) / len(samples)
                    item["terrain_pad"] = {
                        "center_m": [round(x_m, 2), round(y_m, 2)],
                        "radius_m": round(float(item["pad_radius_m"]), 2),
                        "height_m": round(ground_m, 3),
                        "falloff_m": round(float(item["pad_radius_m"]) * 0.6, 2),
                    }
                    padded += 1
                else:
                    # Everything else sits on the HIGHEST point under its
                    # footprint so no corner sinks below the surface.
                    ground_m = max(samples)

                if role in ("building", "landmark") and slope_deg > max_building_slope_deg:
                    steep.append((item.get("name", "?"), slope_deg))

                if align_props_to_slope and item.get("align_to_ground", role in ("prop", "vegetation")):
                    item["world_rotation_pitch"] = pitch
                    item["world_rotation_roll"] = roll
                    tilted += 1
                else:
                    item["world_rotation_pitch"] = 0.0
                    item["world_rotation_roll"] = 0.0

            item["ground_z_cm"] = ground_m * M_TO_CM
            item["world_placement_offset"] = [pos[0], pos[1],
                                              (ground_m + half_h_m) * M_TO_CM]

        if snap_to_terrain and field_data is not None and assets:
            report.append(f"Fitted {len(assets)} asset(s) to the terrain surface "
                          f"(footprint-sampled).")
            if padded:
                report.append(f"Levelled {padded} building pad(s) so structures stand upright.")
            if tilted:
                report.append(f"Tilted {tilted} prop(s)/tree(s) to follow the ground slope.")
            if steep:
                worst = ", ".join(f"{n} ({s:.0f}°)" for n, s in sorted(
                    steep, key=lambda t: -t[1])[:4])
                report.append(f"{len(steep)} building(s) on ground steeper than "
                              f"{max_building_slope_deg:.0f}°: {worst}"
                              + ("" if level_building_pads else
                                 " — enable pad levelling or flatten the terrain there."))
        elif snap_to_terrain and field_data is None and assets:
            report.append("No terrain heightfield connected — assets keep their planned Z.")

        # ---- overlap resolution (simple iterative separation) ----
        # Buildings are ANCHORED: their positions are the layout — a house belongs
        # on its plot facing the street, a pillar on its room corner. Nudging them
        # is what dissolved a village back into a scatter. Only loose props move,
        # and when a prop collides with a building the prop takes the whole push.
        def _anchored(item):
            return item.get("placement_role") in ("building", "landmark")

        if resolve_overlaps and len(assets) > 1:
            moved = 0
            for _ in range(6):
                collided = False
                for i in range(len(assets)):
                    for j in range(i + 1, len(assets)):
                        a, b = assets[i], assets[j]
                        if _anchored(a) and _anchored(b):
                            continue                     # both fixed by the layout
                        pa, pb = a["world_placement_offset"], b["world_placement_offset"]
                        dx = (pb[0] - pa[0]) / M_TO_CM
                        dy = (pb[1] - pa[1]) / M_TO_CM
                        dist = math.hypot(dx, dy)
                        min_dist = (_footprint_radius_m(a) + _footprint_radius_m(b)) * 0.9
                        if dist < min_dist:
                            collided = True
                            moved += 1
                            if dist < 0.01:
                                dx, dy, dist = 1.0, 0.0, 1.0
                            push = (min_dist - dist) + 0.2
                            ux, uy = dx / dist, dy / dist
                            # split the push, unless one side is anchored
                            wa = 0.0 if _anchored(a) else (1.0 if _anchored(b) else 0.5)
                            wb = 0.0 if _anchored(b) else (1.0 if _anchored(a) else 0.5)
                            pa[0] -= ux * push * wa * M_TO_CM
                            pa[1] -= uy * push * wa * M_TO_CM
                            pb[0] += ux * push * wb * M_TO_CM
                            pb[1] += uy * push * wb * M_TO_CM
                            # re-fit Z after the nudge, same rules as above
                            if snap_to_terrain and field_data is not None:
                                for it, p, w in ((a, pa, wa), (b, pb, wb)):
                                    if not w:
                                        continue
                                    mx, my = p[0] / M_TO_CM, p[1] / M_TO_CM
                                    g = max(_footprint_samples(field_data, mx, my,
                                                               _footprint_radius_m(it)))
                                    it["ground_z_cm"] = g * M_TO_CM
                                    p[2] = (g + it["target_size_m"][2] / 2.0) * M_TO_CM
                                    if align_props_to_slope and it.get("align_to_ground"):
                                        pitch, roll, slope_deg = _surface_normal(field_data, mx, my)
                                        it["world_rotation_pitch"] = pitch
                                        it["world_rotation_roll"] = roll
                                        it["ground_slope_deg"] = slope_deg
                if not collided:
                    break
            if moved:
                report.append(f"Resolved {moved} overlap(s) by nudging loose props "
                              f"(buildings held on their layout positions).")

        # ---- terrain & skydome metadata ----
        for t in terrain:
            t["normalize_to_target"] = False
            t["verify_world_size"] = True  # importer checks imported size vs terrain_world_size_m

        merged = terrain + skydome + assets  # ORDER: ground first, sky, then assets
        for i, item in enumerate(merged):
            item["placement_order"] = i

        report.insert(0, f"Placement plan: {len(terrain)} terrain, {len(skydome)} skydome, "
                         f"{len(assets)} asset(s) — ordered, "
                         f"{'scale-normalized' if normalize_scales else 'raw scale'}.")
        report_text = "\n".join(report)
        print("[Placement Manager]\n  " + report_text.replace("\n", "\n  "))

        return (json.dumps(merged, indent=2), report_text, len(merged))
