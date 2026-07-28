import json
import random

class GameAssetPlannerNode:
    """
    Analyzes natural language game concept prompts and generates a structured game asset manifest.
    Produces 2D concept generation prompts and metadata (scale, placement, category, collision).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "game_concept_prompt": ("STRING", {
                    "multiline": True,
                    "default": "3d dungeon crawler, with one hero and spiders, dark fantasy style, stone dungeon environment"
                }),
                "target_asset_count": ("INT", {
                    "default": 12,
                    "min": 1,
                    "max": 50,
                    "step": 1
                }),
                "art_style": ([
                    "Low Poly",
                    "Stylized Low Poly",
                    "Flat-Shaded Low Poly",
                    "Voxel",
                    "PS1 Retro Low Poly",
                    "Pixel-Art 3D",
                    "Realistic PBR",
                    "Photorealistic Scan",
                    "Medieval Realism",
                    "Military Realism",
                    "Dark Fantasy",
                    "High Fantasy",
                    "Horror Gothic",
                    "Post-Apocalyptic",
                    "Steampunk",
                    "Cyberpunk",
                    "Sci-Fi",
                    "Space Opera",
                    "Hand-Painted Cartoon",
                    "Toon / Cel Shaded",
                    "Anime Stylized",
                    "Chibi",
                    "Claymation / Stop Motion",
                    "Casual Mobile",
                    "Isometric RPG",
                    "Western Frontier",
                    "Nordic Viking",
                    "Ancient Egyptian",
                    "Aztec / Mayan",
                    "Oriental / East Asian",
                    "Watercolor Stylized",
                    "Papercraft",
                ], {
                    "default": "Stylized Low Poly"
                }),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 0xffffffffffffffff
                }),
            },
            "optional": {
                "llm_breakdown_json": ("STRING", {
                    "multiline": True,
                    "default": ""
                }),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("asset_manifest_json", "prompt_list_json", "count")
    FUNCTION = "plan_assets"
    CATEGORY = "Geekatplay GameAssetMake/Planner"

    def plan_assets(self, game_concept_prompt, target_asset_count, art_style, seed, llm_breakdown_json=""):
        random.seed(seed)

        # If external LLM / RAG / MCP breakdown JSON is supplied, parse it directly
        if llm_breakdown_json and llm_breakdown_json.strip().startswith("["):
            try:
                manifest = json.loads(llm_breakdown_json)
                prompts = [item.get("prompt", item.get("name", "")) for item in manifest]
                return (json.dumps(manifest, indent=2), json.dumps(prompts, indent=2), len(manifest))
            except Exception:
                pass

        # Deterministic default game asset breakdown generator based on concept & style
        categories = ["hero", "enemy", "boss", "weapon", "environment_prop", "interactable", "structure"]
        
        # Template bank for game asset breakdown
        parsed_prompt = game_concept_prompt.lower()
        
        manifest = []
        
        # Primary Character / Hero
        hero_name = "Paladin Knight Hero" if "hero" in parsed_prompt or "crawler" in parsed_prompt else "Main Character Hero"
        manifest.append({
            "id": "asset_01",
            "name": hero_name,
            "category": "hero",
            "prompt": f"single {hero_name}, one character only, full body, 3/4 view facing the camera, isolated on pure white background, {art_style} style 3D game asset concept art, clear details, no text, no reference sheet",
            "engine_target": "tripo",
            "rig_type": "biped",
            "include_texture": True,
            "include_rigging": True,
            "scale_override": [1.0, 1.0, 1.8],
            "collision_type": "capsule",
            "world_placement_offset": [0.0, 0.0, 90.0]
        })

        # Enemies (e.g., spiders, goblins, skeletons)
        enemy_type = "Giant Dungeon Spider" if "spider" in parsed_prompt else "Dungeon Enemy Creature"
        manifest.append({
            "id": "asset_02",
            "name": enemy_type,
            "category": "enemy",
            "prompt": f"single {enemy_type}, one creature only, full body, 3/4 view facing the camera, isolated on pure white background, {art_style} style 3D game asset concept art, detailed texture, no text, no reference sheet",
            "engine_target": "tripo",
            "rig_type": "quadruped",
            "include_texture": True,
            "include_rigging": True,
            "scale_override": [1.2, 1.2, 0.8],
            "collision_type": "box",
            "world_placement_offset": [200.0, 150.0, 40.0]
        })

        # Environment & Props pool
        prop_pool = [
            ("Ancient Dungeon Chest", "interactable", [0.8, 0.6, 0.6], "box", [100.0, 300.0, 30.0]),
            ("Rusty Iron Sword", "weapon", [0.2, 0.2, 1.2], "box", [50.0, 0.0, 100.0]),
            ("Dungeon Wall Torch Stand", "environment_prop", [0.4, 0.4, 1.5], "box", [0.0, -200.0, 120.0]),
            ("Stone Pillar Column", "structure", [1.0, 1.0, 3.5], "cylinder", [300.0, -300.0, 175.0]),
            ("Wooden Barrel", "environment_prop", [0.7, 0.7, 0.9], "cylinder", [-150.0, 200.0, 45.0]),
            ("Gothic Arch Door", "structure", [1.5, 0.3, 2.8], "box", [0.0, 500.0, 140.0]),
            ("Skull Pile Prop", "environment_prop", [0.5, 0.5, 0.4], "box", [120.0, 180.0, 20.0]),
            ("Health Potion Flask", "interactable", [0.3, 0.3, 0.4], "sphere", [80.0, 310.0, 65.0]),
            ("Spider Egg Cluster", "environment_prop", [0.8, 0.8, 0.6], "sphere", [220.0, 180.0, 30.0]),
            ("Dungeon Altar Shrine", "interactable", [1.8, 1.2, 1.0], "box", [-300.0, 0.0, 50.0]),
            ("Iron Gate Grate", "structure", [1.8, 0.2, 2.5], "box", [0.0, -500.0, 125.0]),
            ("Giant Spider Boss", "boss", [3.0, 3.0, 2.0], "box", [0.0, 800.0, 100.0]),
        ]

        # Populate up to target_asset_count
        idx = 3
        while len(manifest) < target_asset_count:
            p_name, p_cat, p_scale, p_col, p_pos = prop_pool[(len(manifest) - 2) % len(prop_pool)]
            asset_id = f"asset_{idx:02d}"
            
            # Determine suitable API & rigging defaults
            rig = "biped" if p_cat in ["hero", "boss"] else ("quadruped" if "spider" in p_name.lower() else "none")
            engine = "tripo" if rig != "none" else ("meshy" if (idx % 2 == 0) else "tripo")

            manifest.append({
                "id": asset_id,
                "name": f"{p_name} {idx}",
                "category": p_cat,
                "prompt": f"single {p_name}, one object only, 3/4 view angle, isolated on pure white background, {art_style} style 3D game asset concept art, production ready, high quality texture, no text, no reference sheet",
                "engine_target": engine,
                "rig_type": rig,
                "include_texture": True,
                "include_rigging": (rig != "none"),
                "scale_override": p_scale,
                "collision_type": p_col,
                "world_placement_offset": [p_pos[0] + random.randint(-20, 20), p_pos[1] + random.randint(-20, 20), p_pos[2]]
            })
            idx += 1

        prompt_list = [item["prompt"] for item in manifest]

        return (
            json.dumps(manifest, indent=2),
            json.dumps(prompt_list, indent=2),
            len(manifest)
        )
