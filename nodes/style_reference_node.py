# =============================================================
# Geekatplay GameAssetMake — Style Reference (moodboard) node
# (c) Geekatplay Studio / Vladimir Chopine — https://www.geekatplay.com
#
# Locks a whole asset pack to ONE visual style taken from a reference
# image (a moodboard, a screenshot of the game you are matching, a
# painting...). The reference is distilled into a compact style
# sentence, which is appended to every asset prompt in the manifest —
# so all concepts, guardrail retries, and multi-view renders inherit
# the same look instead of drifting per-seed.
#
# Two extraction layers (the pack's usual pattern):
#   - vlm: a local Ollama vision model describes the art style —
#     palette, rendering technique, mood, era (best quality)
#   - palette: local analysis of dominant colors, brightness,
#     saturation and contrast (free, instant, always available)
# Text extraction rather than IPAdapter conditioning is deliberate:
# it works with ANY diffusion model the workflows use (Z-Image Turbo
# included), needs no extra model downloads, and the style survives
# into the manifest where cloud 3D texturing can also see it.
# =============================================================
import io
import json
import base64
import numpy as np

try:
    import requests
except ImportError:
    requests = None


def _tensor_to_png_b64(img_tensor):
    from PIL import Image
    arr = np.clip(img_tensor.cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def vlm_style_description(img_tensor, ollama_url, ollama_model, timeout=(10, 180)):
    """One-sentence style description from a local vision model, or None."""
    if requests is None:
        return None
    prompt = (
        "Describe ONLY the visual ART STYLE of this image for reuse in other "
        "image-generation prompts: rendering technique (low poly, hand-painted, "
        "PBR realistic, cel shaded...), color palette (3-5 color words), lighting "
        "mood, and era/genre feel. Do NOT describe the objects or scene content. "
        'Reply ONLY with JSON: {"style": "<one comma-separated sentence, max 30 words>"}'
    )
    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={
                "model": ollama_model,
                "prompt": prompt,
                "images": [_tensor_to_png_b64(img_tensor)],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.2},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        style = str(data.get("style", "")).strip().rstrip(".")
        return style or None
    except Exception as exc:
        print(f"[Style Reference] Ollama unavailable ({exc}); using palette analysis.")
        return None


# Hue centers (degrees) -> color words, for the palette fallback
_HUE_WORDS = [
    (15, "warm red"), (40, "orange"), (60, "golden yellow"), (90, "yellow-green"),
    (140, "green"), (175, "teal"), (210, "sky blue"), (250, "deep blue"),
    (280, "violet"), (320, "magenta"), (350, "warm red"),
]


def palette_style_description(img_tensor):
    """
    Local, model-free style words: dominant hues + brightness/saturation/contrast
    character. Coarse compared to the VLM, but always available.
    """
    arr = img_tensor.cpu().numpy()  # [H, W, C] 0..1
    flat = arr.reshape(-1, 3)[::max(1, arr.shape[0] * arr.shape[1] // 20000)]

    mx, mn = flat.max(axis=1), flat.min(axis=1)
    val, chroma = mx, mx - mn
    sat = np.where(mx > 0, chroma / np.maximum(mx, 1e-6), 0)

    # hue in degrees, only where there is real color
    r, g, b = flat[:, 0], flat[:, 1], flat[:, 2]
    hue = np.zeros(len(flat))
    m = chroma > 0.05
    idx_r = m & (mx == r); idx_g = m & (mx == g); idx_b = m & (mx == b)
    hue[idx_r] = (60 * ((g - b) / np.maximum(chroma, 1e-6)))[idx_r] % 360
    hue[idx_g] = (60 * ((b - r) / np.maximum(chroma, 1e-6)) + 120)[idx_g]
    hue[idx_b] = (60 * ((r - g) / np.maximum(chroma, 1e-6)) + 240)[idx_b]

    words = []
    if m.sum() > len(flat) * 0.05:
        hist, edges = np.histogram(hue[m], bins=12, range=(0, 360))
        for bin_i in np.argsort(hist)[::-1][:2]:
            if hist[bin_i] < m.sum() * 0.10:
                break
            center = (edges[bin_i] + edges[bin_i + 1]) / 2
            words.append(min(_HUE_WORDS, key=lambda hw: min(abs(hw[0] - center),
                                                            360 - abs(hw[0] - center)))[1])
    else:
        words.append("muted monochrome")

    mean_v, mean_s, std_v = float(val.mean()), float(sat[m].mean() if m.any() else 0), float(val.std())
    words.append("bright high-key lighting" if mean_v > 0.65 else
                 "dark moody lighting" if mean_v < 0.35 else "balanced natural lighting")
    words.append("vivid saturated colors" if mean_s > 0.55 else
                 "desaturated muted palette" if mean_s < 0.25 else "soft natural palette")
    words.append("high contrast" if std_v > 0.28 else "low contrast")

    # de-duplicate while keeping order
    seen, out = set(), []
    for w_ in words:
        if w_ not in seen:
            seen.add(w_)
            out.append(w_)
    return ", ".join(out) + " color scheme"


class StyleReferenceNode:
    """
    Reference image -> style sentence -> appended to every prompt in the
    manifest, keeping a generated asset pack visually consistent.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "reference_image": ("IMAGE",),
                "extraction": (["vlm+palette", "vlm", "palette"], {"default": "vlm+palette",
                    "tooltip": "vlm = Ollama vision model writes the style sentence (best). "
                               "palette = local color/lighting analysis, no services. "
                               "vlm+palette tries the VLM and falls back to palette."}),
                "style_strength": (["subtle (append once)", "strong (lead the prompt)"],
                    {"default": "subtle (append once)",
                     "tooltip": "subtle appends the style at the end of each prompt; strong "
                                "puts it right after the subject so it dominates the render."}),
            },
            "optional": {
                "asset_manifest_json": ("STRING", {"forceInput": True}),
                "extra_style_notes": ("STRING", {"multiline": True, "default": "",
                    "tooltip": "Your own style words, appended after the extracted ones "
                               "(e.g. 'hand-painted seams, thick black outlines')."}),
                "ollama_url": ("STRING", {"default": "http://127.0.0.1:11434"}),
                "ollama_model": ("STRING", {"default": "gemma3:12b"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("style_text", "styled_manifest_json", "styled_prompt_list_json")
    FUNCTION = "extract_style"
    CATEGORY = "Geekatplay GameAssetMake/Concepts"

    def extract_style(self, reference_image, extraction="vlm+palette",
                      style_strength="subtle (append once)",
                      asset_manifest_json="", extra_style_notes="",
                      ollama_url="http://127.0.0.1:11434", ollama_model="gemma3:12b"):
        ref = reference_image[0]

        style = None
        if extraction in ("vlm", "vlm+palette"):
            style = vlm_style_description(ref, ollama_url, ollama_model)
        if not style and extraction in ("palette", "vlm+palette"):
            style = palette_style_description(ref)
        if not style:
            style = "consistent game asset art style"

        notes = extra_style_notes.strip().rstrip(".")
        if notes:
            style = f"{style}, {notes}"
        print(f"[Style Reference] Style locked: {style}")

        manifest = []
        if asset_manifest_json:
            try:
                parsed = json.loads(asset_manifest_json)
                if isinstance(parsed, list):
                    manifest = parsed
            except Exception:
                manifest = []

        styled_manifest = []
        for item in manifest:
            entry = dict(item)
            prompt = str(entry.get("prompt", "")).strip()
            if prompt and style.lower() not in prompt.lower():
                if style_strength.startswith("strong"):
                    # inject right after the subject clause so the style leads
                    head, sep, tail = prompt.partition(", ")
                    prompt = f"{head}, in the style of {style}{sep}{tail}" if sep else \
                             f"{prompt}, in the style of {style}"
                else:
                    prompt = f"{prompt}, in the style of {style}"
            entry["prompt"] = prompt
            entry["style_reference"] = style
            styled_manifest.append(entry)

        prompt_list = [e.get("prompt", "") for e in styled_manifest]
        if styled_manifest:
            print(f"[Style Reference] Applied to {len(styled_manifest)} asset prompt(s).")

        return (style,
                json.dumps(styled_manifest, indent=2),
                json.dumps(prompt_list, indent=2))
