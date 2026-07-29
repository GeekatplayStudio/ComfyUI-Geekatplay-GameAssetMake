# =============================================================
# Geekatplay GameAssetMake — Terrain Upscaler
# (c) Geekatplay Studio / Vladimir Chopine
#
# Raises a generated heightmap to game-engine resolution.
#
# Height data is NOT an image: an ESRGAN-style upscaler invents
# high-frequency texture that becomes spikes and stair-stepping in the
# resulting geometry. So the default path is a high-quality Lanczos
# resample (properly antialiased) plus optional multi-octave fractal
# detail, slope-masked so micro-relief lands on slopes rather than on
# flat ground — the way terrain tools add detail.
#
# An AI upscale model can still be used when you want its detail; the
# result is then gently low-pass filtered so it stays usable as geometry.
# =============================================================
import numpy as np
import torch

# Practical ceiling: most engines cap textures at 8192, and Unreal's landscape
# sizes top out around 8129, so going past this costs memory for nothing.
MAX_SIDE = 8192


def _to_numpy(image):
    return image[0].detach().cpu().numpy().astype(np.float32)


def _lanczos_resize(arr, size):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None          # these are legitimately huge
    channels = []
    for c in range(arr.shape[2]):
        plane = np.clip(arr[..., c] * 255.0, 0, 255).astype(np.uint8)
        img = Image.fromarray(plane, mode="L").resize((size, size), Image.LANCZOS)
        channels.append(np.asarray(img).astype(np.float32) / 255.0)
    return np.stack(channels, axis=-1)


def _fractal_detail(size, octaves, seed):
    """
    Deterministic multi-octave value noise in 0..1, built by smoothly
    upsampling small random grids — believable micro-relief, no hallucination.
    """
    from PIL import Image
    rng = np.random.default_rng(seed)
    total = np.zeros((size, size), dtype=np.float32)
    amplitude, norm = 1.0, 0.0
    base = 8
    for o in range(octaves):
        grid = max(2, min(size, base << o))
        noise = rng.random((grid, grid)).astype(np.float32)
        img = Image.fromarray((noise * 255).astype(np.uint8), mode="L")
        layer = np.asarray(img.resize((size, size), Image.BICUBIC)).astype(np.float32) / 255.0
        total += layer * amplitude
        norm += amplitude
        amplitude *= 0.5
    total /= max(norm, 1e-6)
    return total - total.mean()


class TerrainUpscaleNode:
    """
    Heightmap -> high-resolution heightmap for game-engine terrain,
    with antialiased resampling and optional slope-aware fractal detail.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "heightmap": ("IMAGE",),
                "upscale_factor": (["2x", "4x", "6x", "8x", "max (8192)"], {"default": "4x"}),
                "method": ([
                    "Lanczos + fractal detail (best for geometry)",
                    "Lanczos only (smoothest)",
                    "AI model + smoothing (needs upscale_model)",
                ], {"default": "Lanczos + fractal detail (best for geometry)"}),
                "detail_amount": ("FLOAT", {"default": 0.06, "min": 0.0, "max": 0.5, "step": 0.01,
                    "tooltip": "Height of the added micro-relief, as a fraction of the full "
                               "height range. Small values look natural; large ones get noisy."}),
                "detail_octaves": ("INT", {"default": 5, "min": 1, "max": 8,
                    "tooltip": "More octaves = finer detail layered on top."}),
                "slope_masking": ("FLOAT", {"default": 0.8, "min": 0.0, "max": 1.0, "step": 0.05,
                    "tooltip": "1.0 keeps detail on slopes and off flat ground (valleys and "
                               "plateaus stay smooth), 0 spreads it everywhere."}),
                "smooth_after": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 3.0, "step": 0.1,
                    "tooltip": "Low-pass applied after upscaling. Raise it if an AI model "
                               "introduced speckle that shows up as spikes in the mesh."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
            },
            "optional": {
                "upscale_model": ("UPSCALE_MODEL",),
            },
        }

    RETURN_TYPES = ("IMAGE", "INT")
    RETURN_NAMES = ("heightmap", "resolution")
    FUNCTION = "upscale"
    CATEGORY = "Geekatplay GameAssetMake/Environment"

    def upscale(self, heightmap, upscale_factor="4x",
                method="Lanczos + fractal detail (best for geometry)",
                detail_amount=0.06, detail_octaves=5, slope_masking=0.8,
                smooth_after=0.0, seed=0, upscale_model=None):
        arr = _to_numpy(heightmap)
        src = arr.shape[0]

        if upscale_factor.startswith("max"):
            target = MAX_SIDE
        else:
            target = src * int(upscale_factor.rstrip("x"))
        if target > MAX_SIDE:
            print(f"[Terrain Upscale] {target}px requested; clamped to {MAX_SIDE}px "
                  f"(engine texture/landscape ceiling).")
            target = MAX_SIDE

        # ---- 1. raise resolution -----------------------------------------
        if method.startswith("AI model") and upscale_model is not None:
            from comfy_extras.nodes_upscale_model import ImageUpscaleWithModel
            up = ImageUpscaleWithModel().upscale(upscale_model, heightmap)[0]
            arr = _to_numpy(up)
            print(f"[Terrain Upscale] AI model produced {arr.shape[0]}px.")
            if arr.shape[0] != target:
                arr = _lanczos_resize(arr, target)
            smooth_after = max(smooth_after, 0.6)   # AI detail must be tamed for geometry
        else:
            if method.startswith("AI model"):
                print("[Terrain Upscale] No upscale_model connected — using Lanczos instead.")
            arr = _lanczos_resize(arr, target)

        field = arr.mean(axis=2)
        lo, hi = float(field.min()), float(field.max())
        if hi > lo:
            field = (field - lo) / (hi - lo)

        # ---- 2. fractal micro-relief, kept off flat ground ---------------
        if method.startswith("Lanczos + fractal") and detail_amount > 0.0:
            gy, gx = np.gradient(field)
            slope = np.sqrt(gx ** 2 + gy ** 2)
            smax = float(slope.max())
            slope = slope / smax if smax > 1e-6 else slope
            mask = (1.0 - slope_masking) + slope_masking * np.clip(slope * 3.0, 0.0, 1.0)
            field = field + _fractal_detail(target, int(detail_octaves), seed) * float(detail_amount) * mask
            print(f"[Terrain Upscale] Added {detail_octaves}-octave detail "
                  f"(amount {detail_amount}, slope masking {slope_masking}).")

        # ---- 3. optional low-pass so geometry stays clean ----------------
        passes = int(round(float(smooth_after) * 2))
        for _ in range(passes):
            p = np.pad(field, 1, mode="edge")
            field = (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] +
                     p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:] +
                     p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]) / 9.0
        if passes:
            print(f"[Terrain Upscale] Applied {passes} smoothing pass(es).")

        lo, hi = float(field.min()), float(field.max())
        if hi > lo:
            field = (field - lo) / (hi - lo)

        out = np.repeat(field[..., None].astype(np.float32), 3, axis=2)
        print(f"[Terrain Upscale] {src}px -> {target}px "
              f"({target / max(src, 1):.1f}x, Lanczos antialiased).")
        return (torch.from_numpy(out)[None, ], target)
