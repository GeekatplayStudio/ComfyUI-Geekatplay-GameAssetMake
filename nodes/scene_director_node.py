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

from .scene_layouts import (
    CATEGORY_DEFAULTS,
    make_asset,
    build_layout,
    detect_layout_kind,
    LAYOUT_KEYWORDS,
)

try:
    import requests
except ImportError:
    requests = None

LAYOUT_KINDS = ["auto"] + [kind for _kw, kind in LAYOUT_KEYWORDS] + ["generic"]

M_TO_CM = 100.0

# ---------------------------------------------------------------
# Environment intelligence (ported from Geekatplay ai-terrain):
# season + time-of-day parsed from the prompt drive the sun position,
# light color/intensity, seasonal terrain texture, and the sky brief.
# ---------------------------------------------------------------
SEASONS = {
    "winter": {"kw": ("winter", "snow", "frozen", "icy"), "max_elev": 22.0,
               "sunrise": 8.0, "sunset": 16.0,
               "terrain": "snow-covered ground, snow on peaks and ridges, exposed dark rock on steep slopes, frozen dirt paths",
               "sky": "cold pale winter sky"},
    "summer": {"kw": ("summer", "hot", "midsummer"), "max_elev": 65.0,
               "sunrise": 5.0, "sunset": 21.0,
               "terrain": "lush green grass, dry dirt paths, patches of wildflowers, sun-baked rock",
               "sky": "deep blue summer sky"},
    "autumn": {"kw": ("autumn", "fall", "harvest"), "max_elev": 40.0,
               "sunrise": 6.5, "sunset": 18.5,
               "terrain": "brown and orange fallen leaves, faded grass, muddy paths, moss on rock",
               "sky": "crisp autumn sky"},
    "spring": {"kw": ("spring", "bloom", "blossom"), "max_elev": 50.0,
               "sunrise": 6.0, "sunset": 19.0,
               "terrain": "fresh green grass, blossoming meadow patches, damp earth paths",
               "sky": "soft spring sky"},
}

TIMES_OF_DAY = {
    "sunrise":  {"kw": ("sunrise", "dawn", "daybreak"), "frac": 0.03},
    "morning":  {"kw": ("morning",), "frac": 0.25},
    "noon":     {"kw": ("noon", "midday"), "frac": 0.5},
    "afternoon": {"kw": ("afternoon",), "frac": 0.68},
    "golden hour": {"kw": ("golden hour",), "frac": 0.9},
    "sunset":   {"kw": ("sunset", "dusk", "evening", "twilight"), "frac": 0.97},
    "night":    {"kw": ("night", "midnight", "moonlit", "moonlight"), "frac": -1.0},
}


def parse_environment(scene_prompt):
    """Detects season and time-of-day keywords; defaults: summer / afternoon."""
    p = scene_prompt.lower()
    season = next((name for name, s in SEASONS.items() if any(k in p for k in s["kw"])), "summer")
    tod = next((name for name, t in TIMES_OF_DAY.items() if any(k in p for k in t["kw"])), "afternoon")
    return season, tod


def compute_sun(season, time_of_day):
    """
    Plausible northern-hemisphere sun position for the season/time.
    Returns dict: azimuth_deg (0=N, clockwise), elevation_deg, intensity, color_hex.
    """
    s = SEASONS[season]
    frac = TIMES_OF_DAY[time_of_day]["frac"]

    if frac < 0:  # night: dim blue moonlight
        return {"azimuth_deg": 200.0, "elevation_deg": 35.0,
                "intensity": 0.5, "color_hex": "#8FA3FF",
                "season": season, "time_of_day": time_of_day}

    elevation = max(2.0, s["max_elev"] * math.sin(frac * math.pi))
    azimuth = 90.0 + 180.0 * frac  # east -> south -> west

    if elevation < 10.0:
        color, intensity = "#FF8C42", 3.0     # low warm sun (sunrise/sunset)
    elif elevation < 25.0:
        color, intensity = "#FFD9A0", 6.0
    else:
        color, intensity = "#FFF4E0", 10.0

    return {"azimuth_deg": round(azimuth, 1), "elevation_deg": round(elevation, 1),
            "intensity": intensity, "color_hex": color,
            "season": season, "time_of_day": time_of_day}


def build_terrain_texture_prompt(season, base_terrain_desc, art_style):
    """
    Top-down satellite ground texture matched to the terrain and season —
    the two-step heightmap->matching-texture idea from ai-terrain.
    """
    seasonal = SEASONS[season]["terrain"]
    return (f"strictly top-down orthographic satellite texture map of terrain, {seasonal}, "
            f"terrain features: {base_terrain_desc}, {art_style} style, seamless, "
            f"no lighting, no shadows, no perspective, no horizon, 1:1 aspect")

def deterministic_layout(scene_prompt, target_asset_count, scene_size_m, seed, layout_kind="auto"):
    """
    Structured, seeded layout. Always succeeds. The heavy lifting lives in
    scene_layouts.py — a village gets streets and rows of houses facing them,
    a dungeon gets rooms joined by corridors, rather than props scattered in a
    circle.
    """
    assets, plan, terrain, sky = build_layout(
        scene_prompt, target_asset_count, scene_size_m, seed, layout_kind)
    return {"terrain_description": terrain, "skydome_prompt": sky,
            "assets": assets, "layout_plan": plan,
            "layout_source": f"structured:{plan['kind']}"}


def ollama_dress_layout(layout, scene_prompt, ollama_url, ollama_model, seed):
    """
    Lets a local LLM decide WHAT stands in the scene while the template keeps
    WHERE it stands.

    Asking the LLM for coordinates was the old approach and it is what made
    layouts read as random: a model handed a blank 200x200 m field returns
    plausible-sounding numbers that do not line up into streets, leave no
    corridors, and routinely overlap. The structured template already produces
    a coherent street/room graph, so the model is used for the part it is
    actually good at — naming and describing the buildings and props that
    belong in THIS scene. Slots are filled by role, so a house slot is always
    replaced by a building.

    Mutates `layout` in place; returns True when anything was applied.
    """
    if requests is None:
        return False

    slots = layout["assets"]
    roles = {}
    for i, a in enumerate(slots):
        roles.setdefault(a["placement_role"], []).append(i)
    wanted = {r: len(idx) for r, idx in roles.items() if r in ("building", "prop", "vegetation")}
    if not wanted:
        return False

    schema = {r: [{"name": "short unique asset name",
                   "description": "short visual description of this single object"}]
              for r in wanted}
    ask = (
        f"You are a game art director dressing a level for: \"{scene_prompt}\".\n"
        f"The level layout is already fixed. Name the objects that belong in it.\n"
        + "\n".join(f"- {r}: EXACTLY {n} item(s)" for r, n in wanted.items()) + "\n"
        f"'building' items are walk-up structures, 'prop' items are small objects, "
        f"'vegetation' items are plants or trees. Everything must fit the setting. "
        f"Reply ONLY with JSON matching: {json.dumps(schema)}"
    )
    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={"model": ollama_model, "prompt": ask, "stream": False,
                  "format": "json", "options": {"temperature": 0.5, "seed": seed}},
            timeout=(10, 600),
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception as exc:
        print(f"[Scene Director] Ollama dressing failed ({exc}); keeping the template's assets.")
        return False

    applied = 0
    for role, indices in roles.items():
        items = data.get(role)
        if not isinstance(items, list):
            continue
        for slot_i, item in zip(indices, items):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            # Position, yaw, scale, role and pad all stay as the template set them.
            slots[slot_i]["name"] = name
            desc = str(item.get("description", "")).strip()
            if desc:
                slots[slot_i]["prompt_extra"] = desc
            applied += 1

    if applied:
        layout["layout_source"] += f"+ollama:{ollama_model}"
        print(f"[Scene Director] Ollama named {applied} asset(s); "
              f"positions stay on the {layout['layout_plan']['kind']} template.")
    return bool(applied)


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
                    "label_on": "Ollama names the assets", "label_off": "Template assets only"}),
            },
            "optional": {
                "ollama_url": ("STRING", {"default": "http://127.0.0.1:11434"}),
                # 7B fits alongside loaded diffusion models; 30B may OOM the GPU
                "ollama_model": ("STRING", {"default": "qwen2.5:7b"}),
                # Appended at the END of the optional block: widgets_values is a
                # positional array, so inserting it earlier would shift the values
                # of every workflow saved before this input existed.
                "layout_kind": (LAYOUT_KINDS, {"default": "auto",
                    "tooltip": "Which layout template lays the scene out. 'auto' picks "
                               "from the prompt: village -> streets with houses facing "
                               "them, dungeon -> rooms joined by corridors, camp -> "
                               "clearing ringed by tents."}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("llm_breakdown_json", "terrain_description", "terrain_texture_prompt",
                    "skydome_prompt", "scene_layout_json", "environment_json", "asset_count")
    FUNCTION = "direct_scene"
    CATEGORY = "Geekatplay GameAssetMake/Planner"

    def direct_scene(self, scene_prompt, target_asset_count=14, art_style="Stylized Low Poly",
                     scene_size_m=200.0, seed=0, use_ollama=True,
                     ollama_url="http://127.0.0.1:11434", ollama_model="qwen2.5:7b",
                     layout_kind="auto"):
        # The structured template always runs: it owns the geometry (streets,
        # rooms, corridors, facings) so the scene is coherent even with no LLM.
        layout = deterministic_layout(scene_prompt, target_asset_count,
                                      scene_size_m, seed, layout_kind)
        if use_ollama:
            print(f"[Scene Director] Asking {ollama_model} to name the "
                  f"{layout['layout_plan']['kind']} scene's assets...")
            ollama_dress_layout(layout, scene_prompt, ollama_url, ollama_model, seed)

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
                # intended real-world size in meters — the Placement Manager and
                # Unreal importer normalize the imported mesh to this; the actor
                # scale multiplier stays neutral
                "target_size_m": sc,
                "scale_override": [1.0, 1.0, 1.0],
                "collision_type": a["collision_type"],
                # meters -> engine cm; z lifts the pivot to half object height
                "world_placement_offset": [x_m * M_TO_CM, y_m * M_TO_CM, sc[2] * M_TO_CM / 2.0],
                "world_rotation_yaw": a["yaw_deg"],
                # How this asset meets the ground. The Placement Manager reads
                # these to level a pad under buildings and tilt props to follow
                # the slope instead of dropping everything to a single Z.
                "placement_role": a.get("placement_role", "prop"),
                "align_to_ground": a.get("align_to_ground", True),
                "pad_radius_m": a.get("pad_radius_m", 0.0),
            })

        # --- environment: season + time-of-day drive sun / textures / sky ---
        season, time_of_day = parse_environment(scene_prompt)
        sun = compute_sun(season, time_of_day)
        terrain_texture_prompt = build_terrain_texture_prompt(
            season, layout["terrain_description"], art_style)
        sky_prompt = (f"equirectangular 360 degree panorama, {SEASONS[season]['sky']} at "
                      f"{time_of_day}, {layout['skydome_prompt']}, "
                      f"sun at {sun['elevation_deg']:.0f} degrees elevation, "
                      f"seamless horizontal wrap, no ground objects")

        environment = {
            "season": season,
            "time_of_day": time_of_day,
            "sun": sun,
        }

        layout_full = {
            "scene_prompt": scene_prompt,
            "scene_size_m": scene_size_m,
            "art_style": art_style,
            "layout_source": layout["layout_source"],
            "terrain_description": layout["terrain_description"],
            "terrain_texture_prompt": terrain_texture_prompt,
            "skydome_prompt": sky_prompt,
            "environment": environment,
            # Street/room geometry: drawn by the Layout Map and used by the
            # Placement Manager to sit the built area on flat ground.
            "layout_plan": layout["layout_plan"],
            "assets": layout["assets"],
        }

        plan = layout["layout_plan"]
        print(f"[Scene Director] Plan ready ({layout['layout_source']}): "
              f"{len(breakdown)} assets | {season} {time_of_day} | "
              f"sun az {sun['azimuth_deg']} el {sun['elevation_deg']} {sun['color_hex']}")
        print(f"[Scene Director] Layout '{plan['kind']}': {len(plan['roads'])} road(s), "
              f"{len(plan['rooms'])} room(s), {len(plan['corridors'])} corridor(s), "
              f"built area radius {plan['build_area']['radius_m']:.0f} m.")

        return (json.dumps(breakdown, indent=2),
                layout["terrain_description"],
                terrain_texture_prompt,
                sky_prompt,
                json.dumps(layout_full, indent=2),
                json.dumps(environment, indent=2),
                len(breakdown))
