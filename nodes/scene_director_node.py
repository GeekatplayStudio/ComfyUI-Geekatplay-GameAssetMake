# =============================================================
# Geekatplay GameAssetMake — Scene Director
# (c) Geekatplay Studio / Vladimir Chopine
#
# One prompt ("medieval village") -> a coordinated scene plan:
#   - terrain description (feeds the terrain heightmap branch)
#   - skydome prompt (feeds the HDRI branch)
#   - asset list with PRECISE world positions/rotations/scales,
#     formatted for GameAssetPlannerNode's llm_breakdown_json input
#     so the whole existing asset pipeline runs unchanged.
#
# Layout intelligence: a local Ollama LLM when available (deep
# analysis), with a deterministic seeded layout engine as fallback
# (rings/scatter templates keyed on scene keywords) so the node
# always produces a valid plan.
# =============================================================
import json
import math
import random

try:
    import requests
except ImportError:
    requests = None

M_TO_CM = 100.0

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


def _asset(name, category, x_m, y_m, yaw=0.0, scale=None, prompt_extra=""):
    d = CATEGORY_DEFAULTS.get(category, CATEGORY_DEFAULTS["environment_prop"])
    sc = scale or d["scale"]
    return {
        "name": name,
        "category": category,
        "rig_type": d["rig"],
        "collision_type": d["collision"],
        "scale_override": sc,
        "position_m": [round(x_m, 2), round(y_m, 2)],
        "yaw_deg": round(yaw, 1),
        "prompt_extra": prompt_extra,
    }


def _ring(n, radius_m, rng, jitter=0.15):
    """n positions on a ring, facing the center, with jitter."""
    out = []
    for i in range(n):
        ang = (2 * math.pi * i / max(1, n)) + rng.uniform(-0.15, 0.15)
        r = radius_m * (1.0 + rng.uniform(-jitter, jitter))
        x, y = r * math.cos(ang), r * math.sin(ang)
        yaw = math.degrees(math.atan2(-y, -x))  # face center
        out.append((x, y, yaw))
    return out


def deterministic_layout(scene_prompt, target_asset_count, scene_size_m, seed):
    """Keyword-templated seeded layout. Always succeeds."""
    rng = random.Random(seed)
    p = scene_prompt.lower()
    half = scene_size_m * 0.4
    assets = []

    if any(k in p for k in ("village", "town", "hamlet", "settlement")):
        theme = "medieval village" if "medieval" in p or "midevel" in p else "village"
        assets.append(_asset("Village Well", "interactable", 0, 0, 0,
                             scale=[1.5, 1.5, 2.0], prompt_extra="stone water well with wooden roof"))
        n_houses = max(3, min(8, target_asset_count // 3))
        for i, (x, y, yaw) in enumerate(_ring(n_houses, half * 0.45, rng), 1):
            assets.append(_asset(f"{theme.title()} House {i}", "structure", x, y, yaw,
                                 prompt_extra="timber-framed cottage with thatched roof"))
        assets.append(_asset("Village Church", "structure", half * 0.7, 0, 180,
                             scale=[8.0, 8.0, 12.0], prompt_extra="small stone church with bell tower"))
        props = [("Hay Cart", "environment_prop", "wooden hay cart"),
                 ("Market Stall", "structure", "wooden market stall with canvas awning"),
                 ("Water Barrel", "environment_prop", "wooden water barrel"),
                 ("Firewood Pile", "environment_prop", "stacked firewood pile"),
                 ("Wooden Fence", "environment_prop", "wooden fence segment"),
                 ("Old Oak Tree", "environment_prop", "large old oak tree"),
                 ("Stone Lantern Post", "environment_prop", "lantern on a wooden post"),
                 ("Chicken Coop", "structure", "small wooden chicken coop")]
        i = 0
        while len(assets) < target_asset_count and props:
            nm, cat, extra = props[i % len(props)]
            x, y = rng.uniform(-half, half), rng.uniform(-half, half)
            assets.append(_asset(f"{nm} {i + 1}", cat, x, y, rng.uniform(0, 360), prompt_extra=extra))
            i += 1
        terrain = ("gentle rolling green meadow with a flat central clearing for a village, "
                   "a dirt road crossing, low hills at the edges")
        sky = ("equirectangular 360 degree panorama of a clear late-afternoon sky over "
               "countryside, warm golden light, scattered cumulus clouds")

    elif any(k in p for k in ("dungeon", "castle", "fort", "ruin")):
        assets.append(_asset("Central Altar", "interactable", 0, 0, 0,
                             prompt_extra="ancient stone altar"))
        for i, (x, y, yaw) in enumerate(_ring(max(4, target_asset_count // 3), half * 0.5, rng), 1):
            assets.append(_asset(f"Stone Pillar {i}", "structure", x, y, yaw,
                                 scale=[1.2, 1.2, 4.0], prompt_extra="cracked stone pillar"))
        props = [("Treasure Chest", "interactable", "iron-bound treasure chest"),
                 ("Wall Torch", "environment_prop", "wall torch sconce"),
                 ("Skeleton Pile", "environment_prop", "pile of old bones"),
                 ("Iron Gate", "structure", "rusty iron gate")]
        i = 0
        while len(assets) < target_asset_count:
            nm, cat, extra = props[i % len(props)]
            x, y = rng.uniform(-half, half), rng.uniform(-half, half)
            assets.append(_asset(f"{nm} {i + 1}", cat, x, y, rng.uniform(0, 360), prompt_extra=extra))
            i += 1
        terrain = "cracked stone dungeon floor terrain, sunken center, raised broken edges"
        sky = ("equirectangular 360 degree panorama of a dark stormy night sky, "
               "moonlight through clouds, ominous atmosphere")

    elif any(k in p for k in ("forest", "camp", "woods")):
        assets.append(_asset("Campfire", "interactable", 0, 0, 0, prompt_extra="stone-ring campfire"))
        for i, (x, y, yaw) in enumerate(_ring(3, 6, rng), 1):
            assets.append(_asset(f"Canvas Tent {i}", "structure", x, y, yaw,
                                 scale=[3.0, 3.0, 2.5], prompt_extra="canvas camping tent"))
        i = 0
        while len(assets) < target_asset_count:
            x, y = rng.uniform(-half, half), rng.uniform(-half, half)
            if abs(x) < 8 and abs(y) < 8:
                continue
            assets.append(_asset(f"Pine Tree {i + 1}", "environment_prop", x, y,
                                 rng.uniform(0, 360), scale=[3.0, 3.0, 8.0],
                                 prompt_extra="tall pine tree"))
            i += 1
        terrain = "forest floor terrain with a flat clearing in the center, gentle slopes"
        sky = ("equirectangular 360 degree panorama of dawn sky above a forest, "
               "soft mist, pale sunrise")

    else:
        # Generic scatter for unknown themes
        assets.append(_asset("Central Landmark", "structure", 0, 0, 0,
                             prompt_extra=f"central landmark of {scene_prompt}"))
        i = 0
        while len(assets) < target_asset_count:
            x, y = rng.uniform(-half, half), rng.uniform(-half, half)
            assets.append(_asset(f"Scene Prop {i + 1}", "environment_prop", x, y,
                                 rng.uniform(0, 360),
                                 prompt_extra=f"prop belonging to {scene_prompt}"))
            i += 1
        terrain = f"natural terrain suited to: {scene_prompt}"
        sky = (f"equirectangular 360 degree panorama sky matching the mood of: {scene_prompt}, "
               f"seamless horizontal wrap")

    return {"terrain_description": terrain, "skydome_prompt": sky,
            "assets": assets[:target_asset_count], "layout_source": "deterministic"}


def ollama_layout(scene_prompt, target_asset_count, scene_size_m, ollama_url, ollama_model, seed):
    """Asks a local LLM for the full scene plan. Returns None on any failure."""
    if requests is None:
        return None
    schema_hint = {
        "terrain_description": "one sentence describing the ground shape for a heightmap",
        "skydome_prompt": "one sentence describing the sky as an equirectangular 360 panorama",
        "assets": [{
            "name": "unique name", "category": "structure|environment_prop|interactable|npc|enemy|hero",
            "prompt_extra": "short visual description of this single object",
            "position_m": [0.0, 0.0], "yaw_deg": 0.0,
            "scale_override": [1.0, 1.0, 1.0],
        }],
    }
    prompt = (
        f"You are a game level designer. Plan a game scene for: \"{scene_prompt}\".\n"
        f"The scene is {scene_size_m:.0f}x{scene_size_m:.0f} meters, origin at the center; "
        f"positions are [x, y] meters within +/-{scene_size_m * 0.45:.0f}. "
        f"Design EXACTLY {target_asset_count} assets with a sensible, realistic layout "
        f"(buildings face roads or the center, props cluster logically, keep spacing so objects don't overlap). "
        f"Answer ONLY with JSON exactly matching this schema:\n{json.dumps(schema_hint)}"
    )
    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={"model": ollama_model, "prompt": prompt, "stream": False,
                  "format": "json", "options": {"temperature": 0.4, "seed": seed}},
            timeout=(10, 600),
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])

        raw_assets = data.get("assets") or []
        if not raw_assets:
            return None
        assets = []
        half = scene_size_m * 0.45
        for i, a in enumerate(raw_assets[:target_asset_count]):
            cat = str(a.get("category", "environment_prop"))
            if cat not in CATEGORY_DEFAULTS:
                cat = "environment_prop"
            pos = a.get("position_m") or [0, 0]
            x = max(-half, min(half, float(pos[0])))
            y = max(-half, min(half, float(pos[1] if len(pos) > 1 else 0)))
            base = _asset(str(a.get("name", f"Asset {i+1}")), cat, x, y,
                          float(a.get("yaw_deg", 0.0)),
                          prompt_extra=str(a.get("prompt_extra", "")))
            sc = a.get("scale_override")
            if isinstance(sc, list) and len(sc) == 3:
                base["scale_override"] = [float(v) for v in sc]
            assets.append(base)

        return {
            "terrain_description": str(data.get("terrain_description", "")) or None,
            "skydome_prompt": str(data.get("skydome_prompt", "")) or None,
            "assets": assets,
            "layout_source": f"ollama:{ollama_model}",
        }
    except Exception as exc:
        print(f"[Scene Director] Ollama layout failed ({exc}); using deterministic layout.")
        return None


class SceneDirectorNode:
    """
    Prompt -> full scene plan: terrain description, skydome prompt, and a
    positioned asset breakdown for the existing GameAssetMake pipeline.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "scene_prompt": ("STRING", {"multiline": True,
                    "default": "medieval village in a green valley"}),
                "target_asset_count": ("INT", {"default": 14, "min": 3, "max": 50}),
                "art_style": ("STRING", {"default": "Stylized Low Poly"}),
                "scene_size_m": ("FLOAT", {"default": 200.0, "min": 20.0, "max": 5000.0, "step": 10.0}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "use_ollama": ("BOOLEAN", {"default": True,
                    "label_on": "Ollama deep layout analysis", "label_off": "Deterministic templates only"}),
            },
            "optional": {
                "ollama_url": ("STRING", {"default": "http://127.0.0.1:11434"}),
                # 7B fits alongside loaded diffusion models; 30B may OOM the GPU
                "ollama_model": ("STRING", {"default": "qwen2.5:7b"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("llm_breakdown_json", "terrain_description", "skydome_prompt",
                    "scene_layout_json", "asset_count")
    FUNCTION = "direct_scene"
    CATEGORY = "Geekatplay GameAssetMake/Planner"

    def direct_scene(self, scene_prompt, target_asset_count=14, art_style="Stylized Low Poly",
                     scene_size_m=200.0, seed=0, use_ollama=True,
                     ollama_url="http://127.0.0.1:11434", ollama_model="qwen2.5:7b"):
        fallback = deterministic_layout(scene_prompt, target_asset_count, scene_size_m, seed)
        layout = None
        if use_ollama:
            print(f"[Scene Director] Asking {ollama_model} to design the scene layout...")
            layout = ollama_layout(scene_prompt, target_asset_count, scene_size_m,
                                   ollama_url, ollama_model, seed)
        if layout is None:
            layout = fallback
        # Fill any gaps the LLM left from the deterministic plan
        layout["terrain_description"] = layout.get("terrain_description") or fallback["terrain_description"]
        layout["skydome_prompt"] = layout.get("skydome_prompt") or fallback["skydome_prompt"]

        # --- expand to the GameAssetPlannerNode llm_breakdown_json schema ---
        breakdown = []
        for i, a in enumerate(layout["assets"]):
            x_m, y_m = a["position_m"]
            sc = a["scale_override"]
            extra = (a.get("prompt_extra") or "").strip()
            subject = f"{a['name']}" + (f", {extra}" if extra else "")
            breakdown.append({
                "id": f"asset_{i + 1:02d}",
                "name": a["name"],
                "category": a["category"],
                "prompt": (f"single {subject}, one object only, 3/4 view facing the camera, "
                           f"isolated on pure white background, {art_style} style 3D game "
                           f"asset concept art, no text, no reference sheet"),
                "engine_target": "tripo",
                "rig_type": a["rig_type"],
                "include_texture": True,
                "include_rigging": a["rig_type"] != "none",
                "scale_override": sc,
                "collision_type": a["collision_type"],
                # meters -> engine cm; z lifts the pivot to half object height
                "world_placement_offset": [x_m * M_TO_CM, y_m * M_TO_CM, sc[2] * M_TO_CM / 2.0],
                "world_rotation_yaw": a["yaw_deg"],
            })

        layout_full = {
            "scene_prompt": scene_prompt,
            "scene_size_m": scene_size_m,
            "art_style": art_style,
            "layout_source": layout["layout_source"],
            "terrain_description": layout["terrain_description"],
            "skydome_prompt": layout["skydome_prompt"],
            "assets": layout["assets"],
        }

        print(f"[Scene Director] Plan ready ({layout['layout_source']}): "
              f"{len(breakdown)} assets, terrain + skydome briefs prepared.")

        return (json.dumps(breakdown, indent=2),
                layout["terrain_description"],
                layout["skydome_prompt"],
                json.dumps(layout_full, indent=2),
                len(breakdown))
