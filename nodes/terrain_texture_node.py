# =============================================================
# Geekatplay GameAssetMake — Terrain Texture From Heightmap
# (c) Geekatplay Studio / Vladimir Chopine
#
# Generates the terrain's colour map FROM the heightfield itself, so it
# aligns 1:1 with the elevation — the two-step heightmap -> matching
# texture idea from the author's ai-terrain project, done locally and
# deterministically instead of via an image model that can drift.
#
# Elevation AND slope drive the material bands (water -> sand -> grass
# -> rock on steep faces -> snow on peaks), with season palettes. An
# optional tileable detail texture is multiplied over the top to add
# surface grain without breaking alignment.
# =============================================================
import numpy as np
import torch

# (height_stop, RGB) control points, low -> high. Blended smoothly.
# Top stops stay ROCK-coloured, never white — snow is added separately above
# the snow line, otherwise high ground goes double-white and everything reads
# as a glacier regardless of season.
SEASON_PALETTES = {
    "summer": [(0.00, (46, 82, 120)),    # water
               (0.06, (196, 178, 128)),  # sand / shore
               (0.18, (92, 138, 60)),    # grass
               (0.58, (68, 102, 46)),    # darker upland grass
               (0.82, (124, 116, 104)),  # rock
               (1.00, (146, 140, 132))], # grey summit rock
    "autumn": [(0.00, (52, 76, 104)),
               (0.06, (176, 150, 106)),
               (0.18, (146, 110, 54)),
               (0.58, (128, 84, 42)),
               (0.82, (118, 106, 94)),
               (1.00, (140, 134, 126))],
    "winter": [(0.00, (74, 96, 118)),    # cold water
               (0.06, (186, 190, 198)),
               (0.18, (214, 220, 230)),  # snow field
               (0.58, (228, 234, 242)),
               (0.82, (176, 178, 184)),  # wind-blown rock
               (1.00, (196, 198, 204))],
    "spring":  [(0.00, (50, 92, 130)),
                (0.06, (188, 176, 132)),
                (0.18, (110, 160, 70)),
                (0.58, (80, 128, 54)),
                (0.82, (128, 122, 110)),
                (1.00, (150, 144, 136))],
}

ROCK_COLOR = {
    "summer": (112, 106, 98),
    "autumn": (108, 98, 88),
    "winter": (126, 126, 132),
    "spring": (114, 108, 100),
}


def _ramp(height, palette):
    """Smoothly blends palette control points over a 0..1 height field."""
    h, w = height.shape
    out = np.zeros((h, w, 3), dtype=np.float32)
    stops = [s for s, _ in palette]
    colors = [np.array(c, dtype=np.float32) / 255.0 for _, c in palette]

    for i in range(len(stops) - 1):
        lo, hi = stops[i], stops[i + 1]
        mask = (height >= lo) & (height <= hi)
        if not mask.any():
            continue
        span = max(hi - lo, 1e-6)
        t = ((height - lo) / span)[mask][:, None]
        t = t * t * (3.0 - 2.0 * t)  # smoothstep
        out[mask] = colors[i] * (1.0 - t) + colors[i + 1] * t

    out[height < stops[0]] = colors[0]
    out[height > stops[-1]] = colors[-1]
    return out


class TerrainTextureFromHeightmapNode:
    """
    Heightmap IMAGE -> terrain colour map aligned exactly to the elevation,
    with slope-aware rock and season palettes.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "heightmap": ("IMAGE",),
                "season": (list(SEASON_PALETTES.keys()), {"default": "summer"}),
                "resolution": ("INT", {"default": 1024, "min": 256, "max": 4096, "step": 128}),
                "slope_rock_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 3.0, "step": 0.05,
                                                  "tooltip": "How strongly steep faces turn to bare rock."}),
                "snow_line": ("FLOAT", {"default": 0.88, "min": 0.0, "max": 1.0, "step": 0.01,
                                        "tooltip": "Normalized height above which snow accumulates "
                                                   "(winter lowers it automatically). 1.0 = no snow."}),
                "detail_strength": ("FLOAT", {"default": 0.35, "min": 0.0, "max": 1.0, "step": 0.05,
                                              "tooltip": "Blend amount for the optional tileable detail texture."}),
                "detail_tiling": ("INT", {"default": 8, "min": 1, "max": 64,
                                          "tooltip": "How many times the detail texture repeats across the terrain."}),
                "normal_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 8.0, "step": 0.1,
                                             "tooltip": "Surface relief in the normal map."}),
                "normal_format": (["DirectX (Unreal)", "OpenGL (Unity/glTF)"],
                                  {"default": "DirectX (Unreal)",
                                   "tooltip": "Green channel direction. Unreal expects DirectX."}),
                "equalize": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0, "step": 0.05,
                                       "tooltip": "Spread the bands by AREA rather than raw brightness. "
                                                  "Generated heightmaps are shaded reliefs with skewed "
                                                  "histograms; equalizing keeps snow on the true peaks "
                                                  "instead of half the map."}),
            },
            "optional": {
                "detail_texture": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "IMAGE")
    RETURN_NAMES = ("terrain_texture", "normal_map", "roughness_map", "ao_map")
    FUNCTION = "build_texture"
    CATEGORY = "Geekatplay GameAssetMake/Environment"

    def build_texture(self, heightmap, season="summer", resolution=1024,
                      slope_rock_strength=1.0, snow_line=0.88,
                      detail_strength=0.35, detail_tiling=8,
                      normal_strength=1.0, normal_format="DirectX (Unreal)",
                      equalize=0.8, detail_texture=None):
        from PIL import Image

        # --- heightfield, resampled to the texture resolution ---
        arr = heightmap[0].cpu().numpy()
        gray = arr.mean(axis=2) if arr.ndim == 3 else arr
        img = Image.fromarray(np.clip(gray * 255.0, 0, 255).astype(np.uint8))
        img = img.resize((resolution, resolution), Image.LANCZOS)
        field = np.asarray(img).astype(np.float32) / 255.0

        lo, hi = float(field.min()), float(field.max())
        if hi > lo:
            field = (field - lo) / (hi - lo)

        # Displacement uses the raw field; colour banding uses an optionally
        # area-equalized copy so each band covers a predictable share of the map.
        band_field = field
        if equalize > 0.0:
            flat = field.ravel()
            ranks = np.empty_like(flat)
            ranks[np.argsort(flat, kind="stable")] = np.linspace(0.0, 1.0, flat.size)
            band_field = field * (1.0 - equalize) + ranks.reshape(field.shape) * equalize

        # --- base colour from elevation ---
        palette = SEASON_PALETTES.get(season, SEASON_PALETTES["summer"])
        color = _ramp(band_field, palette)

        # --- slope: steep faces become bare rock ---
        gy, gx = np.gradient(field)
        slope = np.sqrt(gx ** 2 + gy ** 2)
        smax = float(slope.max())
        if smax > 1e-6:
            slope = slope / smax
        rock_mask = np.clip(slope * 2.4 * float(slope_rock_strength), 0.0, 1.0)[..., None]
        rock = np.array(ROCK_COLOR.get(season, ROCK_COLOR["summer"]), dtype=np.float32) / 255.0
        color = color * (1.0 - rock_mask) + rock * rock_mask

        # --- snow above the snow line (flat areas hold it, steep faces shed it) ---
        line = float(snow_line) * (0.55 if season == "winter" else 1.0)
        snow_amt = np.clip((band_field - line) / max(1.0 - line, 1e-6), 0.0, 1.0)
        snow_amt = snow_amt * (1.0 - np.clip(slope * 1.8, 0.0, 1.0))   # not on cliffs
        # gamma > 1 keeps snow to the true peaks instead of creeping downhill
        snow_amt = (snow_amt ** 1.4)[..., None]
        snow = np.array([0.95, 0.96, 0.98], dtype=np.float32)
        color = color * (1.0 - snow_amt) + snow * snow_amt

        # --- optional tileable detail, multiplied over the top ---
        if detail_texture is not None and detail_strength > 0.0:
            det = detail_texture[0].cpu().numpy()
            det_img = Image.fromarray(np.clip(det * 255.0, 0, 255).astype(np.uint8)[..., :3])
            tile = max(1, int(detail_tiling))
            det_img = det_img.resize((resolution // tile, resolution // tile), Image.LANCZOS)
            tiled = np.tile(np.asarray(det_img).astype(np.float32) / 255.0, (tile, tile, 1))
            tiled = tiled[:resolution, :resolution, :3]
            if tiled.shape[0] == resolution and tiled.shape[1] == resolution:
                lum = tiled.mean(axis=2, keepdims=True)
                # centre the detail on 1.0 so it modulates rather than darkens
                modulate = 1.0 + (lum - lum.mean()) * 2.0 * float(detail_strength)
                color = np.clip(color * modulate, 0.0, 1.0)

        # subtle ambient occlusion in the valleys for depth
        cavity = np.clip(1.0 - (field - field.mean()) * 0.35, 0.85, 1.15)[..., None]
        color = np.clip(color * cavity, 0.0, 1.0)

        # ---------------------------------------------------------------
        # PBR maps. The normal map is computed from the ACTUAL heightfield,
        # so it is mathematically exact rather than inferred from a photo.
        # ---------------------------------------------------------------
        # gradients in texel space, scaled by relief strength
        nx = -gx * 8.0 * float(normal_strength)
        ny = -gy * 8.0 * float(normal_strength)
        nz = np.ones_like(nx)
        length = np.sqrt(nx * nx + ny * ny + nz * nz)
        nx, ny, nz = nx / length, ny / length, nz / length
        if normal_format.startswith("DirectX"):
            ny = -ny          # Unreal expects green-down
        normal = np.stack([nx * 0.5 + 0.5, ny * 0.5 + 0.5, nz * 0.5 + 0.5], axis=-1)

        # Roughness per material band: water smooth, snow soft, rock/grass rough
        rough = np.full_like(field, 0.85, dtype=np.float32)
        rough = np.where(band_field < 0.06, 0.12, rough)                 # water
        rough = np.where(band_field > 0.82, 0.78, rough)                 # rock
        rough = rough * (1.0 - snow_amt[..., 0] * 0.35)                  # snow is smoother
        rough = np.clip(rough + (slope - 0.5) * 0.08, 0.05, 1.0)
        roughness = np.repeat(rough[..., None], 3, axis=2)

        # Ambient occlusion from local cavity: field vs a blurred field
        pad = np.pad(field, 2, mode="edge")
        blur = (pad[:-4, 2:-2] + pad[4:, 2:-2] + pad[2:-2, :-4] + pad[2:-2, 4:] +
                pad[2:-2, 2:-2]) / 5.0
        ao = np.clip(0.5 + (field - blur) * 6.0, 0.35, 1.0).astype(np.float32)
        ao_map = np.repeat(ao[..., None], 3, axis=2)

        snow_pct = 100.0 * float((snow_amt > 0.05).mean())
        print(f"[GameAssetMake Terrain Texture] {resolution}x{resolution} {season} PBR set built "
              f"from the heightfield: albedo + normal ({normal_format.split()[0]}) + roughness + AO "
              f"(slope rock x{slope_rock_strength}, {snow_pct:.1f}% snow, equalize {equalize})")

        def to_tensor(a):
            return torch.from_numpy(np.clip(a, 0.0, 1.0).astype(np.float32))[None, ]

        return (to_tensor(color), to_tensor(normal), to_tensor(roughness), to_tensor(ao_map))
