# =============================================================
# Geekatplay GameAssetMake — Single-Object Guardrail node
# (c) Geekatplay Studio / Vladimir Chopine
#
# Verifies that every concept image contains EXACTLY ONE object
# (one human/animal for rigged characters) isolated on a white
# background — the framing image-to-3D APIs require. Failed images
# are automatically regenerated with a stricter prompt (when
# model/clip/vae are connected) until they pass or retries run out.
#
# Two verification layers:
#   - heuristic: white-background blob analysis (fast, local, free)
#   - vlm: Ollama vision model cross-check (same approach as the
#     Geekatplay ComfyUI-Blender-Toolbox OllamaVision node)
# =============================================================
import io
import json
import base64
import numpy as np
import torch

try:
    import requests
except ImportError:
    requests = None

try:
    from scipy import ndimage
    _HAVE_SCIPY = True
except ImportError:
    _HAVE_SCIPY = False

RETRY_SUFFIX = (", ONLY ONE single object, solo subject, centered composition, "
                "nothing else in frame, plain pure white background, no shadow")
RETRY_NEGATIVE = ("multiple objects, two objects, group, collection, collage, grid, "
                  "reference sheet, character sheet, text, watermark, cropped, duplicate")

CHARACTER_TYPES = {"human", "person", "animal", "creature", "monster", "humanoid", "character"}


def _tensor_to_png_b64(img_tensor):
    from PIL import Image
    arr = np.clip(img_tensor.cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def heuristic_check(img_tensor, white_thresh=0.90, min_blob_frac=0.02):
    """
    Blob analysis on the non-white mask.
    Returns (passed, detail) — passes when there is exactly one significant
    foreground blob and the image border region is white.
    """
    arr = img_tensor.cpu().numpy()  # [H, W, C] in 0..1
    h, w = arr.shape[:2]
    brightness = arr.max(axis=2)
    fg_mask = brightness < white_thresh

    # Border whiteness: sample a 4% frame around the edges
    m = max(2, int(min(h, w) * 0.04))
    border = np.concatenate([
        brightness[:m, :].ravel(), brightness[-m:, :].ravel(),
        brightness[:, :m].ravel(), brightness[:, -m:].ravel(),
    ])
    border_white = float((border > white_thresh).mean())

    fg_frac = float(fg_mask.mean())
    if fg_frac < 0.005:
        return False, {"reason": "image nearly empty", "blobs": 0, "border_white": border_white}
    if fg_frac > 0.90:
        return False, {"reason": "background not white", "blobs": -1, "border_white": border_white}

    if _HAVE_SCIPY:
        labeled, n = ndimage.label(fg_mask)
        if n == 0:
            return False, {"reason": "no object found", "blobs": 0, "border_white": border_white}
        sizes = ndimage.sum(fg_mask, labeled, range(1, n + 1)) / float(h * w)
        significant = int((sizes >= min_blob_frac).sum())
        blobs = significant
    else:
        blobs = 1  # cannot count without scipy; rely on border/coverage checks

    passed = (blobs == 1) and (border_white >= 0.75)
    reason = "ok" if passed else (
        f"{blobs} separate objects detected" if blobs > 1 else "object touches frame / background not white"
    )
    return passed, {"reason": reason, "blobs": blobs, "border_white": round(border_white, 3)}


def vlm_check(img_tensor, is_character, ollama_url, ollama_model, timeout=(10, 180)):
    """
    Asks a local Ollama vision model to verify the framing.
    Returns (passed, detail) or (None, detail) when Ollama is unavailable.
    """
    if requests is None:
        return None, {"reason": "requests not available"}

    subject_rule = (
        "The subject must be exactly ONE human or ONE animal/creature (for auto-rigging)."
        if is_character else
        "The subject must be exactly ONE object or ONE creature."
    )
    prompt = (
        "You are validating a game-asset concept image for image-to-3D conversion. "
        f"{subject_rule} "
        "Answer ONLY with JSON: {\"object_count\": <int, number of distinct separate subjects/objects>, "
        "\"background_is_white\": <bool>, \"subject_type\": \"human|animal|creature|object\"}. "
        "Ignore shadows and reflections when counting."
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
                "options": {"temperature": 0.0},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        start, end = text.find("{"), text.rfind("}") + 1
        data = json.loads(text[start:end])

        count = int(data.get("object_count", 0))
        white = bool(data.get("background_is_white", False))
        subject = str(data.get("subject_type", "object")).lower()

        passed = (count == 1) and white
        if passed and is_character and subject not in CHARACTER_TYPES:
            passed = False
            reason = f"rigged asset must be a single human/animal, VLM saw '{subject}'"
        else:
            reason = "ok" if passed else f"VLM: object_count={count}, white_background={white}"
        return passed, {"reason": reason, "object_count": count,
                        "background_is_white": white, "subject_type": subject}
    except Exception as exc:
        return None, {"reason": f"Ollama unavailable ({exc})"}


class SingleObjectGuardrailNode:
    """
    Guardrail between concept generation and the approval gallery: verifies
    one-object-on-white framing per image and regenerates failures in place.
    """

    @classmethod
    def INPUT_TYPES(cls):
        try:
            import comfy.samplers
            samplers = comfy.samplers.KSampler.SAMPLERS
            schedulers = comfy.samplers.KSampler.SCHEDULERS
        except Exception:
            samplers, schedulers = ["euler"], ["simple"]
        return {
            "required": {
                "images": ("IMAGE",),
                "asset_manifest_json": ("STRING", {"forceInput": True}),
                "verification": (["heuristic", "vlm", "heuristic+vlm"], {"default": "heuristic+vlm"}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
                "steps": ("INT", {"default": 8, "min": 1, "max": 100}),
                "cfg": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 20.0, "step": 0.1}),
                "sampler_name": (samplers, {"default": "euler"}),
                "scheduler": (schedulers, {"default": "simple"}),
                "latent_channels": ([16, 4], {"default": 16}),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 0xffffffffffffffff,
                    "control_after_generate": True,
                }),
            },
            "optional": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
                "vae": ("VAE",),
                "ollama_url": ("STRING", {"default": "http://127.0.0.1:11434"}),
                "ollama_model": ("STRING", {"default": "gemma3"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "BOOLEAN")
    RETURN_NAMES = ("verified_images", "asset_manifest_json", "verification_report_json", "all_passed")
    FUNCTION = "verify"
    CATEGORY = "Geekatplay GameAssetMake/Guardrails"

    def _verify_one(self, img, is_character, verification, ollama_url, ollama_model):
        details = {}
        heuristic_ok = True
        if verification in ("heuristic", "heuristic+vlm"):
            heuristic_ok, details["heuristic"] = heuristic_check(img)
        vlm_ok = True
        if verification in ("vlm", "heuristic+vlm"):
            result, details["vlm"] = vlm_check(img, is_character, ollama_url, ollama_model)
            if result is None:
                # VLM offline: fall back to whatever the heuristic said
                vlm_ok = True
                details["vlm"]["fallback"] = "heuristic only"
            else:
                vlm_ok = result
        return heuristic_ok and vlm_ok, details

    def _regenerate(self, model, clip, vae, prompt, width, height, channels, seed,
                    steps, cfg, sampler_name, scheduler):
        import nodes as comfy_nodes
        pos = comfy_nodes.CLIPTextEncode().encode(clip, prompt + RETRY_SUFFIX)[0]
        neg = comfy_nodes.CLIPTextEncode().encode(clip, RETRY_NEGATIVE)[0]
        latent = {"samples": torch.zeros([1, channels, height // 8, width // 8])}
        out = comfy_nodes.common_ksampler(
            model, seed, steps, cfg, sampler_name, scheduler, pos, neg, latent, denoise=1.0)[0]
        return vae.decode(out["samples"])  # [1, H, W, C]

    def verify(self, images, asset_manifest_json, verification="heuristic+vlm", max_retries=2,
               steps=8, cfg=1.0, sampler_name="euler", scheduler="simple",
               latent_channels=16, seed=0,
               model=None, clip=None, vae=None,
               ollama_url="http://127.0.0.1:11434", ollama_model="gemma3"):
        try:
            manifest = json.loads(asset_manifest_json)
        except Exception:
            manifest = []

        can_regen = model is not None and clip is not None and vae is not None
        batch = images.shape[0]
        height, width = int(images.shape[1]), int(images.shape[2])

        out_images = []
        report = []
        all_passed = True

        for i in range(batch):
            img = images[i]
            item = manifest[i] if i < len(manifest) else {}
            is_character = bool(item.get("include_rigging")) or \
                item.get("category") in ("hero", "enemy", "boss", "npc")
            name = item.get("name", f"image_{i}")
            prompt = item.get("prompt", "")

            passed, details = self._verify_one(img, is_character, verification, ollama_url, ollama_model)
            attempts = 0

            while not passed and can_regen and attempts < max_retries and prompt:
                attempts += 1
                retry_seed = (seed + i * 1000 + attempts * 77) & 0xffffffffffffffff
                print(f"[Guardrail] '{name}' failed ({details}); regenerating "
                      f"(attempt {attempts}/{max_retries}, seed {retry_seed})...")
                try:
                    regen = self._regenerate(model, clip, vae, prompt, width, height,
                                             int(latent_channels), retry_seed,
                                             steps, cfg, sampler_name, scheduler)
                    img = regen[0]
                    passed, details = self._verify_one(img, is_character, verification,
                                                       ollama_url, ollama_model)
                except Exception as exc:
                    print(f"[Guardrail] regeneration failed: {exc}")
                    break

            out_images.append(img)
            all_passed = all_passed and passed
            report.append({
                "index": i,
                "name": name,
                "is_character": is_character,
                "passed": bool(passed),
                "retries_used": attempts,
                "details": details,
            })
            status = "PASS" if passed else "FAIL"
            print(f"[Guardrail] {status} '{name}' after {attempts} retr{'y' if attempts == 1 else 'ies'}")

        result_images = torch.stack(out_images, dim=0)
        report_json = json.dumps(report, indent=2)

        return {
            "ui": {"guardrail_report": [report]},
            "result": (result_images, asset_manifest_json, report_json, all_passed),
        }
