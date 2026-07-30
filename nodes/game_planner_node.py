# =============================================================
# Geekatplay GameAssetMake — Game Asset Planner
# (c) Geekatplay Studio / Vladimir Chopine
#
# Turns a natural-language game concept into a structured asset
# manifest. Assets are DERIVED FROM THE PROMPT: a local Ollama model
# designs the inventory when available, otherwise a genre-aware
# deterministic planner extracts the subjects you actually named and
# fills out the rest from a matching themed pool.
# =============================================================
import json
import random
import re

try:
    import requests
except ImportError:
    requests = None

ART_STYLES = [
    "Low Poly", "Stylized Low Poly", "Flat-Shaded Low Poly", "Voxel",
    "PS1 Retro Low Poly", "Pixel-Art 3D", "Realistic PBR", "Photorealistic Scan",
    "Medieval Realism", "Military Realism", "Dark Fantasy", "High Fantasy",
    "Horror Gothic", "Post-Apocalyptic", "Steampunk", "Cyberpunk", "Sci-Fi",
    "Space Opera", "Hand-Painted Cartoon", "Toon / Cel Shaded", "Anime Stylized",
    "Chibi", "Claymation / Stop Motion", "Casual Mobile", "Isometric RPG",
    "Western Frontier", "Nordic Viking", "Ancient Egyptian", "Aztec / Mayan",
    "Oriental / East Asian", "Watercolor Stylized", "Papercraft",
]

# --- Character & rigging presets -------------------------------------------
# An auto-rigger (Tripo / Meshy) needs a symmetrical, full-body, straight-on
# reference. A 3/4 "hero pose" with the arms against the torso is what most
# concept prompts produce, and it rigs badly: limbs fuse to the body and the
# skeleton comes out twisted. So the pose that gets baked into the image prompt
# follows the rigging decision instead of being the same for every asset.
RIG_PRESETS = {
    "Auto — rig by asset category": "auto",
    "Biped for every character": "biped",
    "Quadruped for every character": "quadruped",
    "No rigging (static meshes only)": "none",
}

POSE_PRESETS = {
    "Auto — rig-ready pose when rigging is on": "auto",
    "A-pose (arms lowered — best for auto-rig)": "apose",
    "T-pose (arms straight out)": "tpose",
    "Relaxed 3/4 hero pose (not rig-friendly)": "free",
}

POSE_TEXT = {
    "apose": ("full body from head to toe, symmetrical A-pose with both arms lowered "
              "and held clear of the torso, legs slightly apart, facing the camera "
              "straight on, neutral expression, nothing covering the arms or legs"),
    "tpose": ("full body from head to toe, symmetrical T-pose with both arms straight "
              "out horizontally, legs together, facing the camera straight on, neutral "
              "expression, nothing covering the arms or legs"),
    "free":  "3/4 view facing the camera, full body in frame",
}

# Category archetypes: rig, collision and a sensible real-world size (metres)
# group: "character" (auto-riggable) | "accessory" (props) | "environment" (level geometry)
CATEGORY_SPECS = {
    "hero":             {"rig": "biped",     "collision": "capsule", "size": [1.0, 1.0, 1.8],  "group": "character"},
    "npc":              {"rig": "biped",     "collision": "capsule", "size": [1.0, 1.0, 1.8],  "group": "character"},
    "enemy":            {"rig": "quadruped", "collision": "box",     "size": [1.4, 1.4, 1.2],  "group": "character"},
    "boss":             {"rig": "biped",     "collision": "box",     "size": [3.0, 3.0, 3.5],  "group": "character"},
    "weapon":           {"rig": "none",      "collision": "box",     "size": [0.3, 0.3, 1.2],  "group": "accessory"},
    "vehicle":          {"rig": "none",      "collision": "box",     "size": [4.0, 2.0, 2.0],  "group": "accessory"},
    "environment_prop": {"rig": "none",      "collision": "box",     "size": [1.0, 1.0, 1.2],  "group": "accessory"},
    "interactable":     {"rig": "none",      "collision": "box",     "size": [0.8, 0.8, 0.8],  "group": "accessory"},
    "structure":        {"rig": "none",      "collision": "box",     "size": [6.0, 6.0, 5.0],  "group": "environment"},
    # --- modular level-geometry kit (tiling pieces, group "environment") ---
    "wall":             {"rig": "none",      "collision": "box",     "size": [4.0, 0.4, 3.0],  "group": "environment"},
    "floor":            {"rig": "none",      "collision": "box",     "size": [4.0, 4.0, 0.3],  "group": "environment"},
    "ceiling":          {"rig": "none",      "collision": "box",     "size": [4.0, 4.0, 0.3],  "group": "environment"},
    "stairs":           {"rig": "none",      "collision": "complex", "size": [2.0, 4.0, 3.0],  "group": "environment"},
    "doorway":          {"rig": "none",      "collision": "box",     "size": [2.0, 0.5, 3.0],  "group": "environment"},
    "corner":           {"rig": "none",      "collision": "box",     "size": [4.0, 4.0, 3.0],  "group": "environment"},
    "column":           {"rig": "none",      "collision": "box",     "size": [0.8, 0.8, 3.0],  "group": "environment"},
    "archway":          {"rig": "none",      "collision": "box",     "size": [4.0, 0.6, 3.5],  "group": "environment"},
}

# Modular environment kits: tiling level geometry per genre. Prompts stress the
# modular/tileable nature so the pieces snap together in the engine.
ENV_KITS = {
    "fantasy": [("Dungeon Wall Section", "wall", "carved stone block wall, flat straight section"),
                ("Dungeon Floor Tile", "floor", "flagstone floor tile, flat square slab"),
                ("Dungeon Ceiling Panel", "ceiling", "vaulted stone ceiling panel"),
                ("Stone Staircase", "stairs", "straight stone staircase flight"),
                ("Dungeon Doorway", "doorway", "arched stone doorway frame"),
                ("Wall Corner Block", "corner", "stone wall corner section, 90 degree"),
                ("Stone Column", "column", "carved stone support column"),
                ("Stone Archway", "archway", "wide stone archway")],
    "scifi":   [("Hull Wall Panel", "wall", "brushed metal hull wall panel with seams"),
                ("Deck Floor Plate", "floor", "metal grating deck floor plate"),
                ("Ceiling Duct Panel", "ceiling", "ship ceiling panel with conduits"),
                ("Metal Stairway", "stairs", "industrial metal stair flight"),
                ("Blast Door Frame", "doorway", "sci-fi sliding blast door frame"),
                ("Corridor Corner", "corner", "corridor corner junction module"),
                ("Support Strut", "column", "metal support strut column"),
                ("Airlock Arch", "archway", "airlock archway module")],
    "horror":  [("Cracked Plaster Wall", "wall", "peeling plaster wall section"),
                ("Rotten Floorboards", "floor", "warped wooden floorboard tile"),
                ("Water-Stained Ceiling", "ceiling", "damp stained ceiling panel"),
                ("Creaking Staircase", "stairs", "old wooden staircase flight"),
                ("Boarded Doorway", "doorway", "boarded-up doorway frame"),
                ("Corridor Corner", "corner", "peeling wall corner section"),
                ("Rusted Pillar", "column", "rusted iron support pillar"),
                ("Broken Archway", "archway", "crumbling brick archway")],
    "western": [("Timber Wall Section", "wall", "weathered plank wall section"),
                ("Saloon Floorboards", "floor", "worn wooden floor tile"),
                ("Wooden Ceiling Beam", "ceiling", "timber ceiling beam panel"),
                ("Wooden Staircase", "stairs", "saloon wooden stair flight"),
                ("Swinging Door Frame", "doorway", "saloon swinging door frame"),
                ("Porch Corner", "corner", "timber porch corner section"),
                ("Porch Post", "column", "wooden porch support post"),
                ("Timber Archway", "archway", "wooden entrance archway")],
    "modern":  [("Concrete Wall Panel", "wall", "smooth concrete wall section"),
                ("Tiled Floor Slab", "floor", "ceramic tiled floor slab"),
                ("Drop Ceiling Panel", "ceiling", "office drop ceiling tile"),
                ("Concrete Stairs", "stairs", "concrete stair flight with railing"),
                ("Office Doorway", "doorway", "modern door frame"),
                ("Wall Corner Unit", "corner", "concrete wall corner section"),
                ("Concrete Pillar", "column", "square concrete pillar"),
                ("Entrance Archway", "archway", "modern entrance arch")],
}

# Genre-themed fill props, used only AFTER the prompt's own subjects are used.
GENRE_POOLS = {
    "scifi": {
        "keywords": ("sci-fi", "scifi", "science fiction", "space", "alien", "rocket",
                     "spaceship", "starship", "robot", "android", "cyber", "laser",
                     "futuristic", "planet", "mars", "orbital", "retro-futur"),
        "hero": "Space Explorer",
        "enemy": "Alien Creature",
        "props": [("Landing Rocket", "vehicle", [4.0, 4.0, 12.0]),
                  ("Supply Crate", "interactable", [1.0, 1.0, 1.0]),
                  ("Antenna Array", "structure", [2.0, 2.0, 5.0]),
                  ("Alien Rock Formation", "environment_prop", [2.5, 2.5, 3.0]),
                  ("Plasma Rifle", "weapon", [0.2, 0.2, 1.1]),
                  ("Habitat Dome", "structure", [8.0, 8.0, 4.0]),
                  ("Fuel Barrel", "environment_prop", [0.8, 0.8, 1.2]),
                  ("Control Console", "interactable", [1.4, 0.8, 1.2]),
                  ("Solar Panel", "structure", [3.0, 0.3, 2.0]),
                  ("Alien Plant", "environment_prop", [1.2, 1.2, 2.0])],
    },
    "fantasy": {
        "keywords": ("fantasy", "dungeon", "medieval", "castle", "knight", "wizard",
                     "dragon", "orc", "elf", "sword", "magic", "crawler"),
        "hero": "Knight Hero",
        "enemy": "Dungeon Creature",
        "props": [("Treasure Chest", "interactable", [0.9, 0.6, 0.6]),
                  ("Iron Sword", "weapon", [0.2, 0.2, 1.1]),
                  ("Wall Torch", "environment_prop", [0.4, 0.4, 1.2]),
                  ("Stone Pillar", "structure", [1.2, 1.2, 4.0]),
                  ("Wooden Barrel", "environment_prop", [0.8, 0.8, 1.0]),
                  ("Arched Door", "structure", [1.6, 0.4, 2.8]),
                  ("Potion Flask", "interactable", [0.2, 0.2, 0.3]),
                  ("Stone Altar", "interactable", [1.8, 1.2, 1.0]),
                  ("Iron Gate", "structure", [2.0, 0.3, 2.6]),
                  ("Old Oak Tree", "environment_prop", [3.0, 3.0, 7.0])],
    },
    "horror": {
        "keywords": ("horror", "zombie", "haunted", "ghost", "creepy", "nightmare", "asylum"),
        "hero": "Survivor",
        "enemy": "Undead Creature",
        "props": [("Rusted Gurney", "environment_prop", [2.0, 0.9, 0.9]),
                  ("Flickering Lamp", "environment_prop", [0.4, 0.4, 1.6]),
                  ("Boarded Window", "structure", [1.4, 0.2, 1.6]),
                  ("Blood-Stained Chair", "environment_prop", [0.6, 0.6, 1.1]),
                  ("Old Wheelchair", "environment_prop", [0.8, 1.0, 1.2]),
                  ("Broken Piano", "environment_prop", [1.6, 0.8, 1.2]),
                  ("Rusty Crowbar", "weapon", [0.1, 0.1, 0.9]),
                  ("Cracked Mirror", "environment_prop", [1.0, 0.2, 1.8])],
    },
    "western": {
        "keywords": ("western", "cowboy", "wild west", "saloon", "frontier", "desert town"),
        "hero": "Gunslinger",
        "enemy": "Outlaw",
        "props": [("Saloon Building", "structure", [10.0, 8.0, 6.0]),
                  ("Water Tower", "structure", [4.0, 4.0, 8.0]),
                  ("Wooden Wagon", "vehicle", [3.5, 1.8, 2.0]),
                  ("Hitching Post", "environment_prop", [2.0, 0.3, 1.2]),
                  ("Revolver", "weapon", [0.3, 0.1, 0.2]),
                  ("Cactus", "environment_prop", [1.0, 1.0, 2.5]),
                  ("Whiskey Barrel", "environment_prop", [0.8, 0.8, 1.0]),
                  ("Wanted Poster Board", "interactable", [1.2, 0.2, 2.0])],
    },
    "modern": {
        "keywords": ("modern", "city", "urban", "street", "office", "school", "apartment"),
        "hero": "Protagonist",
        "enemy": "Hostile Figure",
        "props": [("Street Lamp", "structure", [0.4, 0.4, 5.0]),
                  ("Parked Car", "vehicle", [4.4, 1.9, 1.5]),
                  ("Trash Bin", "environment_prop", [0.7, 0.7, 1.1]),
                  ("Bus Stop Shelter", "structure", [3.0, 1.5, 2.6]),
                  ("Park Bench", "environment_prop", [1.8, 0.7, 0.9]),
                  ("Traffic Cone", "environment_prop", [0.4, 0.4, 0.7]),
                  ("Vending Machine", "interactable", [1.0, 0.8, 1.9]),
                  ("Fire Hydrant", "environment_prop", [0.4, 0.4, 0.8])],
    },
}

# LLM category synonyms. Models answer the planner's schema with the word they
# would naturally use ("character", "monster", "prop") rather than the exact enum,
# and an unrecognised category used to fall through to name-based guessing — which
# turned LLM-designed characters into props and stripped their rigging.
_CATEGORY_ALIASES = {
    "character": "hero", "protagonist": "hero", "player": "hero", "human": "hero",
    "humanoid": "hero", "person": "hero", "people": "hero", "figure": "hero",
    "villager": "npc", "ally": "npc", "friendly": "npc", "merchant": "npc",
    "vendor": "npc", "civilian": "npc", "quest_giver": "npc",
    "monster": "enemy", "creature": "enemy", "beast": "enemy", "animal": "enemy",
    "mob": "enemy", "foe": "enemy", "hostile": "enemy", "undead": "enemy",
    "miniboss": "boss", "mini_boss": "boss", "final_boss": "boss",
    "gun": "weapon", "melee": "weapon", "tool": "weapon", "equipment": "weapon",
    "gear": "weapon", "armor": "weapon", "armour": "weapon",
    "ship": "vehicle", "transport": "vehicle", "mount": "vehicle",
    "building": "structure", "architecture": "structure", "landmark": "structure",
    "terrain": "structure", "scenery": "structure",
    "prop": "environment_prop", "item": "environment_prop",
    "object": "environment_prop", "decoration": "environment_prop",
    "decor": "environment_prop", "furniture": "environment_prop",
    "vegetation": "environment_prop", "plant": "environment_prop",
    "nature": "environment_prop", "misc": "environment_prop",
    "container": "interactable", "pickup": "interactable", "loot": "interactable",
    "collectible": "interactable", "consumable": "interactable",
    "usable": "interactable", "puzzle": "interactable",
}


def normalize_category(raw, name):
    """Map whatever the LLM answered onto a real category, guessing only as a last resort."""
    cat = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if cat in CATEGORY_SPECS:
        return cat
    return _CATEGORY_ALIASES.get(cat) or classify_subject(name)


# Words that describe STYLE rather than an object, stripped from subject phrases.
_STYLE_WORDS = {
    "3d", "2d", "game", "style", "styled", "art", "scene", "environment", "level",
    "retro", "modern", "realistic", "stylized", "low", "poly", "lowpoly", "cartoon",
    "dark", "bright", "detailed", "high", "quality", "concept", "with", "and", "the",
    "a", "an", "of", "in", "on", "at", "for", "featuring", "including", "some",
    "sci-fi", "scifi", "fantasy", "horror", "western", "cyberpunk", "steampunk",
}

# Words describing the SETTING or genre rather than an object you can model.
_SETTING_WORDS = {
    "crawler", "town", "city", "village", "world", "planet", "level", "map",
    "setting", "adventure", "simulator", "shooter", "rpg", "platformer",
    "roguelike", "survival", "sandbox", "arena", "dungeon", "wild", "west",
    "environment", "landscape", "biome",
}

# Hints decide an asset's category, and the category decides its group — which is
# what the gallery uses to offer auto-rigging. A character that lands outside the
# "character" group silently loses its rig option, so these lists are deliberately
# generous about job titles and fantasy archetypes: "Blacksmith", "Sheriff",
# "Skeleton King" and "Maintenance Robot" are all people, not props.
_CHARACTER_HINTS = (
    "girl", "woman", "man", "boy", "hero", "heroine", "pilot", "soldier", "warrior",
    "knight", "explorer", "captain", "pinup", "person", "character", "astronaut",
    "cowboy", "survivor", "boss", "child", "human", "elf", "dwarf", "gnome",
    "halfling", "fairy", "angel", "god", "goddess", "titan",
    # job titles / roles
    "elder", "merchant", "vendor", "trader", "blacksmith", "smith", "guard",
    "guardian", "sentinel", "warden", "villager", "peasant", "farmer", "miner",
    "baker", "butcher", "innkeeper", "keeper", "owner", "shopkeeper", "bartender",
    "sailor", "fisherman", "worker", "engineer", "scientist", "doctor", "nurse",
    "scholar", "sage", "alchemist", "officer", "commander", "general", "chief",
    "sheriff", "deputy", "marshal", "agent", "detective", "trooper", "marine",
    "scout", "dancer", "singer", "priest", "monk", "cleric", "nun",
    # fantasy / rpg archetypes
    "mage", "wizard", "witch", "sorcerer", "sorceress", "necromancer", "shaman",
    "druid", "bard", "paladin", "ranger", "archer", "hunter", "thief", "rogue",
    "assassin", "bandit", "outlaw", "pirate", "gladiator", "samurai", "ninja",
    "viking", "barbarian", "adventurer", "mercenary", "nomad", "king", "queen",
    "prince", "princess", "lord", "lady", "baron", "duke", "emperor", "chieftain",
    # constructed humanoids — biped rigs, not props
    "robot", "android", "cyborg", "automaton", "droid",
)
_CREATURE_HINTS = (
    "alien", "monster", "creature", "beast", "dragon", "spider", "zombie", "wolf",
    "insect", "worm", "slime", "goblin", "orc", "troll", "ogre", "skeleton",
    "ghoul", "ghost", "spirit", "wraith", "banshee", "lich", "vampire", "werewolf",
    "mummy", "demon", "devil", "golem", "elemental", "gargoyle", "harpy",
    "minotaur", "centaur", "hydra", "kraken", "griffin", "wyvern", "drake",
    "serpent", "snake", "lizard", "scorpion", "mutant", "abomination", "hound",
    "dog", "boar", "bear", "horse", "deer", "bird", "crow", "raven", "fish",
    "shark", "tentacle", "mimic", "swarm",
)
_VEHICLE_HINTS = ("rocket", "ship", "spaceship", "starship", "car", "truck", "tank",
                  "wagon", "boat", "plane", "shuttle", "mech")
_WEAPON_HINTS = ("sword", "gun", "rifle", "pistol", "blaster", "axe", "bow",
                 "dagger", "spear", "hammer", "revolver", "shield", "crossbow",
                 "mace", "staff", "wand", "knife", "blade")
_STRUCTURE_HINTS = ("building", "house", "tower", "castle", "dome", "station",
                    "base", "temple", "church", "wall", "gate", "bridge", "saloon",
                    "hut", "cabin", "shed", "barn", "fort", "keep", "tomb", "shrine")
# Explicit prop head-nouns. Without these, "Woman Statue" and "Human Skull" are
# classified as characters (the person word is the only hint present), get an
# A-pose concept prompt, and are offered a skeleton in the gallery.
_PROP_HINTS = (
    "statue", "skull", "bone", "corpse", "remains", "effigy", "doll", "mannequin",
    "portrait", "painting", "poster", "banner", "sign", "bust", "totem", "idol",
    "bowl", "plate", "cup", "mug", "bottle", "jar", "vase", "pot", "crate",
    "barrel", "chest", "box", "sack", "book", "scroll", "candle", "torch",
    "lantern", "lamp", "rug", "carpet", "table", "chair", "stool", "bench", "bed",
    "shelf", "cabinet", "rock", "stone", "boulder", "tree", "bush", "flower",
    "log", "stump", "fence", "post", "cart", "anvil", "forge", "well", "fountain",
    # display props built in the SHAPE of a person — a rig would be meaningless
    "armor", "armour", "stand", "rack", "dummy", "helmet", "mask", "costume",
)


# Humanoid enemies. The "enemy" archetype assumes a quadruped skeleton, which is
# right for a wolf or a giant spider but wrong for most fantasy enemies: a zombie,
# orc or lich stands on two legs, and a quadruped rig turns one into a tangle.
# "giant" is deliberately absent — a Giant Spider is still a spider.
_BIPED_CREATURE_HINTS = (
    "zombie", "skeleton", "ghoul", "lich", "vampire", "mummy", "werewolf",
    "demon", "devil", "goblin", "orc", "troll", "ogre", "golem", "gargoyle",
    "minotaur", "harpy", "abomination", "mutant", "wraith", "banshee", "ghost",
    "spirit", "elemental", "alien", "humanoid",
)


def rig_spec_for(category_spec, phrase):
    """
    The archetype's skeleton, corrected for humanoid enemies. Returns the spec
    unchanged for everything that is not a defaulted-to-quadruped enemy.
    """
    if category_spec.get("rig") != "quadruped":
        return category_spec
    text = str(phrase).lower()
    if any(re.search(r"\b" + re.escape(h), text) for h in _BIPED_CREATURE_HINTS):
        biped = dict(category_spec)
        biped["rig"] = "biped"
        return biped
    return category_spec


def resolve_rig(category_spec, rig_preset):
    """(rig_type, include_rigging) for one asset under the chosen rigging preset."""
    mode = RIG_PRESETS.get(rig_preset, "auto")
    # Only characters are ever auto-rigged: a rigged crate is meaningless and
    # costs an extra API call per asset. Every non-character category already
    # carries rig "none" in CATEGORY_SPECS.
    if mode == "none" or category_spec.get("group") != "character":
        return "none", False
    if mode in ("biped", "quadruped"):
        return mode, True
    rig = category_spec["rig"]
    return rig, rig != "none"


def resolve_pose(pose_preset, include_rigging):
    """Pose wording for a character image, following the rigging decision on Auto."""
    mode = POSE_PRESETS.get(pose_preset, "auto")
    if mode == "auto":
        mode = "apose" if include_rigging else "free"
    return POSE_TEXT.get(mode, POSE_TEXT["free"])


def detect_genre(prompt):
    p = prompt.lower()
    best, score = "fantasy", 0
    for name, pool in GENRE_POOLS.items():
        hits = sum(1 for k in pool["keywords"] if k in p)
        if hits > score:
            best, score = name, hits
    return best if score else "fantasy"


def classify_subject(phrase):
    """
    Category for a phrase from the prompt or an LLM-supplied asset name.

    Two rules, both of which exist because the obvious approach misclassified
    characters and cost them their rigging option in the gallery:

    1. Everything from the first preposition on describes the SETTING, not the
       asset, so it is cut away first: "rocket on the alien planet" is a rocket
       (vehicle), not an alien (enemy).
    2. Within what is left, the LAST hint wins, because English compound names
       put the head noun last: a "Saloon Owner" is an owner (character) who
       happens to work in a saloon, and a "Skeleton King" is a king. Taking the
       earliest hint instead classified both as scenery. Ties at the same
       position go to the longer hint, so "Wooden Bowl" is a bowl, not a bow.

    Hints match at a WORD START only. Plain substring search made "Command
    Console" a character (because "man" occurs inside "Command") and "Goblin
    Shaman" matched on the same accident rather than on "shaman".
    """
    head = re.split(r"\b(?:on|in|at|inside|near|under|over|beside|of|with|for)\b",
                    phrase.lower(), maxsplit=1)[0]
    best_cat, best_key = "environment_prop", (-1, 0)
    for cat, hints in (("hero", _CHARACTER_HINTS), ("enemy", _CREATURE_HINTS),
                       ("vehicle", _VEHICLE_HINTS), ("weapon", _WEAPON_HINTS),
                       ("structure", _STRUCTURE_HINTS),
                       ("environment_prop", _PROP_HINTS)):
        for h in hints:
            last = None
            for match in re.finditer(r"\b" + re.escape(h), head):
                last = match
            if last is not None and (last.start(), len(h)) > best_key:
                best_cat, best_key = cat, (last.start(), len(h))
    return best_cat


def extract_subjects(prompt):
    """
    Pulls the concrete things the user actually named out of the prompt.
    "Retro sci-fi, rocket on the alien planet, pinup girl in retro scifi suit"
    -> [("Rocket", "rocket on the alien planet"),
        ("Pinup Girl", "pinup girl in retro scifi suit")]

    Returns (name, description): the name is trimmed at the first preposition so
    a single ASSET is requested rather than a whole scene, while the description
    keeps the full phrase for the image prompt.
    """
    parts = re.split(r"[,;.]| with | and | featuring ", prompt, flags=re.IGNORECASE)
    subjects, seen = [], set()

    for raw in parts:
        phrase = raw.strip()
        if not phrase:
            continue
        words = re.findall(r"[A-Za-z0-9'-]+", phrase)
        # drop leading articles so we get "Hero", not "A Hero"
        while words and words[0].lower() in ("a", "an", "the"):
            words = words[1:]
        if not words:
            continue

        meaningful = [w for w in words if w.lower() not in _STYLE_WORDS]
        if not meaningful:
            continue                      # pure style, e.g. "retro sci-fi"
        # A phrase made only of SETTING words describes where the game happens,
        # not an object: "3d dungeon crawler", "wild west town" are not assets.
        # Genre keywords are deliberately NOT used here — they include real
        # objects like "rocket" and "sword" that must survive.
        if all(w.lower() in _SETTING_WORDS for w in meaningful):
            continue

        full = " ".join(words[:8]).strip()
        head = re.split(r"\b(?:on|in|at|inside|near|under|over|beside|of)\b",
                        full, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        name = head if len(head) > 2 else full
        key = name.lower()
        if key and key not in seen:
            seen.add(key)
            subjects.append((name, full))
    return subjects


def title_case(text):
    """
    Readable asset name. LLMs often answer with 'PinupGirlSuit' or
    'rocket_landing_pad'; split those before casing so the name (and the image
    prompt built from it) reads as words rather than one mashed token.
    """
    text = re.sub(r"[_\-]+", " ", str(text)).strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)      # camelCase -> camel Case
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)    # HTTPServer -> HTTP Server
    small = {"of", "the", "in", "on", "a", "an", "and"}
    words = [w for w in text.split() if w]
    return " ".join(w if w.isupper() and len(w) > 1
                    else (w.capitalize() if (i == 0 or w.lower() not in small) else w.lower())
                    for i, w in enumerate(words))


def ollama_asset_plan(prompt, count, art_style, url, model, seed):
    """Asks a local LLM to design the asset inventory. None on any failure."""
    if requests is None:
        return None
    schema = [{"name": "short asset name", "category":
               "hero|npc|enemy|boss|weapon|vehicle|structure|environment_prop|interactable",
               "description": "short visual description of this single object"}]
    ask = (f"You are a game art director. For the game concept: \"{prompt}\"\n"
           f"List EXACTLY {count} individual 3D game assets that belong in it. "
           f"Every asset must fit the concept's setting - do not invent unrelated items. "
           f"Include the characters, vehicles and props the concept mentions. "
           f"Reply ONLY with a JSON array matching: {json.dumps(schema)}")
    try:
        resp = requests.post(f"{url.rstrip('/')}/api/generate",
                             json={"model": model, "prompt": ask, "stream": False,
                                   "format": "json", "options": {"temperature": 0.5, "seed": seed}},
                             timeout=(10, 600))
        resp.raise_for_status()
        text = resp.json().get("response", "")
        start, end = text.find("["), text.rfind("]") + 1
        if start < 0:                      # some models wrap it in an object
            obj = json.loads(text[text.find("{"):text.rfind("}") + 1])
            data = next((v for v in obj.values() if isinstance(v, list)), [])
        else:
            data = json.loads(text[start:end])
        out = []
        for item in data[:count]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            cat = normalize_category(item.get("category"), name)
            out.append({"name": title_case(name), "category": cat,
                        "description": str(item.get("description", "")).strip()})
        return out or None
    except Exception as exc:
        print(f"[Asset Planner] Ollama plan failed ({exc}); using the prompt-derived planner.")
        return None


def deterministic_plan(prompt, count, seed):
    """Prompt-derived plan: your named subjects first, then genre-matched fill."""
    rng = random.Random(seed)
    genre = detect_genre(prompt)
    pool = GENRE_POOLS[genre]
    plan, used = [], set()

    for name, description in extract_subjects(prompt):
        if len(plan) >= count:
            break
        key = name.lower()
        if key in used:
            continue
        used.add(key)
        plan.append({"name": title_case(name),
                     "category": classify_subject(description),
                     "description": description})

    # guarantee a playable character if the prompt implied one and none was found
    if not any(p["category"] in ("hero", "npc") for p in plan) and len(plan) < count:
        plan.append({"name": pool["hero"], "category": "hero", "description": pool["hero"]})

    props = list(pool["props"])
    rng.shuffle(props)
    i = 0
    while len(plan) < count:
        name, cat, size = props[i % len(props)]
        suffix = f" {i // len(props) + 2}" if i >= len(props) else ""
        plan.append({"name": f"{name}{suffix}", "category": cat,
                     "description": name, "size": size})
        i += 1

    print(f"[Asset Planner] Genre '{genre}' detected; "
          f"{len(used)} subject(s) taken from your prompt, rest filled from the {genre} pool.")
    return plan


class GameAssetPlannerNode:
    """
    Natural-language game concept -> structured asset manifest whose assets
    actually match the concept (Ollama-designed, prompt-derived fallback).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "game_concept_prompt": ("STRING", {
                    "multiline": True,
                    "default": "Retro sci-fi, rocket on an alien planet, pinup girl in a retro sci-fi suit"
                }),
                "target_asset_count": ("INT", {"default": 12, "min": 1, "max": 50, "step": 1}),
                "environment_pieces": ("INT", {"default": 4, "min": 0, "max": 12, "step": 1,
                    "tooltip": "Modular level geometry to include (walls, floors, ceilings, "
                               "stairs, doorways, corners). 0 = props and characters only."}),
                "art_style": (ART_STYLES, {"default": "Stylized Low Poly"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "use_ollama": ("BOOLEAN", {"default": True,
                    "label_on": "Ollama designs the asset list",
                    "label_off": "Prompt-derived planner only"}),
            },
            "optional": {
                "llm_breakdown_json": ("STRING", {"multiline": True, "default": ""}),
                "ollama_url": ("STRING", {"default": "http://127.0.0.1:11434"}),
                "ollama_model": ("STRING", {"default": "qwen2.5:7b"}),
                # Appended at the END of the optional block on purpose: widgets_values
                # is a positional array, so inserting these anywhere else would shift
                # every value in workflows that were saved before they existed.
                "rigging": (list(RIG_PRESETS), {
                    "default": "Auto — rig by asset category",
                    "tooltip": "Which characters get an auto-rigged skeleton. Only "
                               "characters are ever rigged — props and level geometry "
                               "are always static meshes."}),
                "character_pose": (list(POSE_PRESETS), {
                    "default": "Auto — rig-ready pose when rigging is on",
                    "tooltip": "Pose requested in the concept image for characters. "
                               "Auto-riggers need a symmetrical full-body A/T-pose; a "
                               "3/4 hero pose fuses the limbs to the torso and rigs badly."}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("asset_manifest_json", "prompt_list_json", "count")
    FUNCTION = "plan_assets"
    CATEGORY = "Geekatplay GameAssetMake/Planner"

    def plan_assets(self, game_concept_prompt, target_asset_count, art_style, seed,
                    environment_pieces=4, use_ollama=True, llm_breakdown_json="",
                    ollama_url="http://127.0.0.1:11434", ollama_model="qwen2.5:7b",
                    rigging="Auto — rig by asset category",
                    character_pose="Auto — rig-ready pose when rigging is on"):
        rng = random.Random(seed)

        # 1. An upstream planner (e.g. the Scene Director) wins outright.
        if llm_breakdown_json and llm_breakdown_json.strip().startswith("["):
            try:
                manifest = json.loads(llm_breakdown_json)
                prompts = [it.get("prompt", it.get("name", "")) for it in manifest]
                return (json.dumps(manifest, indent=2), json.dumps(prompts, indent=2), len(manifest))
            except Exception:
                pass

        # 2. Ollama designs the inventory from the concept, 3. else prompt-derived.
        env_count = max(0, min(int(environment_pieces), target_asset_count))
        subject_count = max(1, target_asset_count - env_count)

        plan = None
        if use_ollama:
            print(f"[Asset Planner] Asking {ollama_model} to design {subject_count} "
                  f"assets for: {game_concept_prompt[:60]}...")
            plan = ollama_asset_plan(game_concept_prompt, subject_count,
                                     art_style, ollama_url, ollama_model, seed)
        if not plan:
            plan = deterministic_plan(game_concept_prompt, subject_count, seed)

        # Modular level geometry so the scene has walls/floors/ceilings to build with
        if env_count:
            kit = ENV_KITS.get(detect_genre(game_concept_prompt), ENV_KITS["fantasy"])
            for name, cat, desc in kit[:env_count]:
                plan.append({"name": name, "category": cat,
                             "description": f"{desc}, modular tileable game kit piece"})
            print(f"[Asset Planner] + {min(env_count, len(kit))} modular environment piece(s).")

        manifest = []
        for idx, entry in enumerate(plan[:target_asset_count], start=1):
            cat = entry["category"] if entry["category"] in CATEGORY_SPECS else "environment_prop"
            spec = CATEGORY_SPECS[cat]
            size = entry.get("size", spec["size"])
            desc = (entry.get("description") or entry["name"]).strip()
            subject = entry["name"] if desc.lower() == entry["name"].lower() else f"{entry['name']}, {desc}"

            group = spec.get("group", "accessory")
            rig_type, include_rigging = resolve_rig(rig_spec_for(spec, subject), rigging)

            if group == "environment":
                asset_prompt = (
                    f"single modular {subject}, one game-kit piece only, straight-on 3/4 view, "
                    f"isolated on pure white background, {art_style} style 3D game asset, "
                    f"flat clean edges so it tiles with neighbouring pieces, "
                    f"no text, no reference sheet")
            elif group == "character":
                # The pose is what makes or breaks auto-rigging, so it leads the prompt.
                asset_prompt = (
                    f"single {subject}, one character only, "
                    f"{resolve_pose(character_pose, include_rigging)}, "
                    f"isolated on pure white background, {art_style} style 3D game "
                    f"asset concept art, production ready, no text, no reference sheet")
            else:
                asset_prompt = (
                    f"single {subject}, one object only, 3/4 view facing the camera, "
                    f"isolated on pure white background, {art_style} style 3D game "
                    f"asset concept art, production ready, no text, no reference sheet")

            manifest.append({
                "id": f"asset_{idx:02d}",
                "name": entry["name"],
                "category": cat,
                "asset_group": group,
                "prompt": asset_prompt,
                "engine_target": "tripo",
                "rig_type": rig_type,
                "include_texture": True,
                "include_rigging": include_rigging,
                "target_size_m": size,
                "scale_override": [1.0, 1.0, 1.0],
                "collision_type": spec["collision"],
                "world_placement_offset": [
                    rng.uniform(-40.0, 40.0) * 100.0,
                    rng.uniform(-40.0, 40.0) * 100.0,
                    size[2] * 100.0 / 2.0,
                ],
                "world_rotation_yaw": rng.uniform(0.0, 360.0),
            })

        prompt_list = [item["prompt"] for item in manifest]
        rigged = [m for m in manifest if m["include_rigging"]]
        print(f"[Asset Planner] {len(manifest)} assets planned: "
              f"{', '.join(m['name'] for m in manifest[:6])}"
              f"{' ...' if len(manifest) > 6 else ''}")
        if rigged:
            kinds = sorted({m["rig_type"] for m in rigged})
            print(f"[Asset Planner] Rigging '{rigging}' -> {len(rigged)} character(s) "
                  f"as {'/'.join(kinds)}; pose preset '{character_pose}'.")
        else:
            print(f"[Asset Planner] Rigging '{rigging}' -> no rigged assets "
                  f"(all static meshes).")

        return (json.dumps(manifest, indent=2), json.dumps(prompt_list, indent=2), len(manifest))
