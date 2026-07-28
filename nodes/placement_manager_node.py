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
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("completed_3d_manifest_json", "placement_report", "asset_count")
    FUNCTION = "manage_placement"
    CATEGORY = "Geekatplay GameAssetMake/Engine-Bridge"

    def manage_placement(self, snap_to_terrain=True, resolve_overlaps=True, normalize_scales=True,
                         assets_manifest_json="", terrain_manifest_json="",
                         skydome_manifest_json="", extra_manifest_json=""):
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

        # ---- per-asset: sizes, Z snapping ----
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

            ground_m = 0.0
            if snap_to_terrain and field_data is not None:
                ground_m = _ground_height_m(field_data, x_m, y_m)
            item["ground_z_cm"] = ground_m * M_TO_CM
            item["world_placement_offset"] = [pos[0], pos[1],
                                              (ground_m + half_h_m) * M_TO_CM]

        if snap_to_terrain and field_data is not None and assets:
            report.append(f"Snapped {len(assets)} asset(s) onto the terrain surface.")

        # ---- overlap resolution (simple iterative separation) ----
        if resolve_overlaps and len(assets) > 1:
            moved = 0
            for _ in range(6):
                collided = False
                for i in range(len(assets)):
                    for j in range(i + 1, len(assets)):
                        a, b = assets[i], assets[j]
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
                            push = (min_dist - dist) / 2.0 + 0.2
                            ux, uy = dx / dist, dy / dist
                            pa[0] -= ux * push * M_TO_CM
                            pa[1] -= uy * push * M_TO_CM
                            pb[0] += ux * push * M_TO_CM
                            pb[1] += uy * push * M_TO_CM
                            # re-snap Z after the nudge
                            if snap_to_terrain and field_data is not None:
                                for it, p in ((a, pa), (b, pb)):
                                    g = _ground_height_m(field_data, p[0] / M_TO_CM, p[1] / M_TO_CM)
                                    it["ground_z_cm"] = g * M_TO_CM
                                    p[2] = (g + it["target_size_m"][2] / 2.0) * M_TO_CM
                if not collided:
                    break
            if moved:
                report.append(f"Resolved {moved} overlap(s) by nudging assets apart.")

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
