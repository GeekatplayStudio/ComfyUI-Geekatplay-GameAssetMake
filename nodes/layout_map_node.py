# =============================================================
# Geekatplay GameAssetMake — Layout Map Preview
# (c) Geekatplay Studio / Vladimir Chopine
#
# Renders the Scene Director's placement plan as a visible top-down
# map: grid with meter coordinates, one marker per asset colored by
# category, an arrow for facing (yaw), and name + (x, y) labels —
# so the layout is verifiable BEFORE anything is generated or placed.
# =============================================================
import os
import json
import numpy as np
import torch
import folder_paths

CATEGORY_COLORS = {
    "hero": (66, 135, 245),
    "npc": (98, 182, 255),
    "enemy": (220, 53, 69),
    "boss": (160, 20, 40),
    "structure": (139, 94, 60),
    "environment_prop": (46, 160, 67),
    "interactable": (255, 193, 7),
    "weapon": (150, 150, 160),
}


class LayoutMapPreviewNode:
    """
    scene_layout_json -> top-down layout map IMAGE (+ saved PNG),
    with meter grid, category-colored markers, yaw arrows, and labels.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "scene_layout_json": ("STRING", {"forceInput": True}),
                "map_size_px": ("INT", {"default": 1024, "min": 512, "max": 4096, "step": 128}),
                "show_labels": ("BOOLEAN", {"default": True}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("layout_map", "map_path")
    FUNCTION = "render_map"
    CATEGORY = "Geekatplay GameAssetMake/Planner"
    OUTPUT_NODE = True

    def render_map(self, scene_layout_json, map_size_px=1024, show_labels=True):
        from PIL import Image, ImageDraw, ImageFont

        layout = json.loads(scene_layout_json)
        size_m = float(layout.get("scene_size_m", 200.0))
        assets = layout.get("assets", [])
        env = layout.get("environment", {})

        S = int(map_size_px)
        img = Image.new("RGB", (S, S), (24, 26, 32))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", max(11, S // 85))
            font_big = ImageFont.truetype("arial.ttf", max(15, S // 55))
        except Exception:
            font = font_big = ImageFont.load_default()

        def to_px(x_m, y_m):
            # world origin at center; +x right, +y up on the map
            return (S / 2 + (x_m / size_m) * S * 0.9,
                    S / 2 - (y_m / size_m) * S * 0.9)

        # --- grid every 25 m (or 10% of scene) ---
        step = 25.0 if size_m <= 400 else size_m / 10.0
        n = int(size_m / 2 / step)
        for i in range(-n, n + 1):
            m = i * step
            x0, y0 = to_px(m, -size_m / 2)
            x1, y1 = to_px(m, size_m / 2)
            col = (60, 64, 74) if i else (110, 118, 134)
            draw.line([x0, y0, x1, y1], fill=col, width=1 if i else 2)
            x0, y0 = to_px(-size_m / 2, m)
            x1, y1 = to_px(size_m / 2, m)
            draw.line([x0, y0, x1, y1], fill=col, width=1 if i else 2)
            if i and abs(m) >= step:
                px, py = to_px(m, 0)
                draw.text((px + 2, S / 2 + 2), f"{m:.0f}m", fill=(120, 126, 140), font=font)

        # --- layout structure: streets, plazas, rooms, corridors ---
        # Drawn under the markers so the plan is readable as a place — a village
        # with streets, a dungeon with rooms — instead of a cloud of dots.
        plan = layout.get("layout_plan") or {}

        def m_to_px_len(v_m):
            return max(1.0, (v_m / size_m) * S * 0.9)

        for road in plan.get("roads", []):
            pts = [to_px(px, py) for px, py in road.get("points", [])]
            if len(pts) < 2:
                continue
            w = m_to_px_len(float(road.get("width_m", 6.0)))
            draw.line([c for p in pts for c in p], fill=(74, 66, 54), width=int(w))
            draw.line([c for p in pts for c in p], fill=(104, 92, 74), width=max(1, int(w * 0.12)))

        for plaza in plan.get("plazas", []):
            cx_m, cy_m = plaza.get("center", [0, 0])
            pr = m_to_px_len(float(plaza.get("radius_m", 5.0)))
            px, py = to_px(cx_m, cy_m)
            draw.ellipse([px - pr, py - pr, px + pr, py + pr],
                         fill=(58, 54, 46), outline=(112, 104, 86), width=2)

        for corr in plan.get("corridors", []):
            pts = [to_px(px, py) for px, py in corr.get("points", [])]
            if len(pts) < 2:
                continue
            draw.line([c for p in pts for c in p], fill=(58, 60, 70),
                      width=int(m_to_px_len(float(corr.get("width_m", 3.0)))))

        for room in plan.get("rooms", []):
            x0, y0, x1, y1 = room.get("rect", [0, 0, 0, 0])
            a, b = to_px(x0, y1), to_px(x1, y0)      # y flips on the map
            draw.rectangle([a[0], a[1], b[0], b[1]],
                           fill=(44, 46, 56), outline=(120, 126, 142), width=2)
            if show_labels and room.get("name"):
                draw.text((a[0] + 4, a[1] + 3), room["name"], fill=(150, 156, 172), font=font)

        # --- assets ---
        r = max(5, S // 110)
        # Labels are decluttered: a dense scene stacks 20 captions on top of each
        # other and the map becomes unreadable. Landmarks and buildings get first
        # claim on the space, then props fill in wherever there is still room.
        label_anchors = []
        min_label_gap = max(14, S // 46)

        def claim_label_slot(px, py):
            for ax, ay in label_anchors:
                if abs(px - ax) < min_label_gap * 4 and abs(py - ay) < min_label_gap:
                    return False
            label_anchors.append((px, py))
            return True

        role_rank = {"landmark": 0, "building": 1, "character": 2, "prop": 3, "vegetation": 4}
        assets = sorted(assets, key=lambda a: role_rank.get(a.get("placement_role"), 3))

        for a in assets:
            x_m, y_m = a.get("position_m", [0, 0])
            yaw = float(a.get("yaw_deg", 0.0))
            cat = a.get("category", "environment_prop")
            color = CATEGORY_COLORS.get(cat, (200, 200, 200))
            px, py = to_px(x_m, y_m)

            # footprint hint from scale (x extent in meters)
            sc = a.get("scale_override", [1, 1, 1])
            foot = max(r, (sc[0] / size_m) * S * 0.9 / 2)

            draw.ellipse([px - foot, py - foot, px + foot, py + foot],
                         outline=color, width=2)
            draw.ellipse([px - r / 2, py - r / 2, px + r / 2, py + r / 2], fill=color)

            # yaw arrow (0 deg = +x, counterclockwise in world; map y flipped)
            import math
            ax = px + math.cos(math.radians(yaw)) * foot * 1.6
            ay = py - math.sin(math.radians(yaw)) * foot * 1.6
            draw.line([px, py, ax, ay], fill=color, width=2)

            if show_labels and claim_label_slot(px + foot + 3, py - r):
                label = f"{a.get('name', '?')} ({x_m:.0f}, {y_m:.0f})"
                draw.text((px + foot + 3, py - r), label, fill=(225, 228, 235), font=font)

        # --- header: scene + environment summary ---
        title = layout.get("scene_prompt", "scene")[:60]
        sun = env.get("sun", {})
        env_line = ""
        if sun:
            env_line = (f"{env.get('season', '?')} {env.get('time_of_day', '?')}  |  "
                        f"sun az {sun.get('azimuth_deg', '?')}° el {sun.get('elevation_deg', '?')}°  "
                        f"{sun.get('color_hex', '')}")
        draw.rectangle([0, 0, S, int(S * 0.055)], fill=(15, 16, 20))
        draw.text((8, 4), f"LAYOUT: {title}", fill=(255, 255, 255), font=font_big)
        if env_line:
            draw.text((8, int(S * 0.055) - S // 60 - 4), env_line, fill=(255, 200, 120), font=font)

        # sun direction compass (azimuth 0 = North = up)
        if sun and "azimuth_deg" in sun:
            import math
            cx, cy, cr = S - S // 12, S // 12 + int(S * 0.055), S // 16
            draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], outline=(255, 200, 120), width=2)
            az = math.radians(float(sun["azimuth_deg"]))
            sx = cx + math.sin(az) * cr
            sy = cy - math.cos(az) * cr
            draw.line([cx, cy, sx, sy], fill=(255, 170, 60), width=3)
            draw.text((cx - cr, cy + cr + 2), "sun", fill=(255, 200, 120), font=font)

        # --- save + return as IMAGE tensor ---
        out_dir = os.path.join(folder_paths.get_output_directory(), "game_environment")
        os.makedirs(out_dir, exist_ok=True)
        map_path = os.path.join(out_dir, "scene_layout_map.png")
        img.save(map_path)
        print(f"[GameAssetMake Layout Map] {len(assets)} assets rendered -> {map_path}")

        tensor = torch.from_numpy(np.array(img).astype(np.float32) / 255.0)[None, ]
        return (tensor, map_path)
