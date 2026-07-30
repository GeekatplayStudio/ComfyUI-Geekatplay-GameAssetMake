# =============================================================
# Geekatplay GameAssetMake — Batch Concept Generator (per-asset loop)
# (c) Geekatplay Studio / Vladimir Chopine
#
# Generates ONE image per asset by looping over the planner's prompt
# list, encoding and sampling each prompt separately. This replaces
# feeding the whole prompt-list JSON into a single CLIPTextEncode,
# which produced one combined prompt and put every asset into every
# image. Each item gets its own seed, and (optionally) its own
# single-object verification + retry before moving to the next.
# =============================================================
import json
import torch

from .asset_verify_node import (
    heuristic_check,
    vlm_check,
    RETRY_SUFFIX,
    RETRY_NEGATIVE,
    CHARACTER_TYPES,
)


def parse_prompt_items(prompt_list_json, asset_manifest_json=""):
    """
    Accepts either the planner's prompt_list_json (array of strings) or an
    asset_manifest_json (array of dicts). Returns [{prompt, name, is_character}].
    """
    items = []

    manifest = []
    if asset_manifest_json:
        try:
            parsed = json.loads(asset_manifest_json)
            if isinstance(parsed, list):
                manifest = parsed
        except Exception:
            manifest = []

    raw = []
    try:
        parsed = json.loads(prompt_list_json)
        if isinstance(parsed, list):
            raw = parsed
        elif isinstance(parsed, str):
            raw = [parsed]
    except Exception:
        # Not JSON — treat the whole thing as one plain prompt
        if str(prompt_list_json).strip():
            raw = [str(prompt_list_json).strip()]

    for idx, entry in enumerate(raw):
        if isinstance(entry, dict):
            prompt = entry.get("prompt", "")
            name = entry.get("name", f"asset_{idx:02d}")
            meta = entry
        else:
            prompt = str(entry)
            meta = manifest[idx] if idx < len(manifest) else {}
            name = meta.get("name", f"asset_{idx:02d}")
        if not str(prompt).strip():
            continue
        is_character = bool(meta.get("include_rigging")) or \
            meta.get("category") in ("hero", "enemy", "boss", "npc")
        items.append({"prompt": str(prompt), "name": name, "is_character": is_character})

    return items


class BatchConceptGeneratorNode:
    """
    Loops over the asset prompt list and renders one concept image per asset,
    optionally verifying each is a single object on white and retrying failures.
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
                "model": ("MODEL",),
                "clip": ("CLIP",),
                "vae": ("VAE",),
                "prompt_list_json": ("STRING", {"forceInput": True}),
                "negative_prompt": ("STRING", {"multiline": True,
                    "default": "multiple objects, group, collage, grid, reference sheet, "
                               "text, watermark, cropped, blurry, low quality"}),
                "width": ("INT", {"default": 1024, "min": 256, "max": 4096, "step": 64}),
                "height": ("INT", {"default": 1024, "min": 256, "max": 4096, "step": 64}),
                "steps": ("INT", {"default": 8, "min": 1, "max": 100}),
                "cfg": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 20.0, "step": 0.1}),
                "sampler_name": (samplers, {"default": "euler"}),
                "scheduler": (schedulers, {"default": "simple"}),
                "seed": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 0xffffffffffffffff,
                    "control_after_generate": True,
                }),
                "latent_channels": ([16, 4], {"default": 16}),
            },
            "optional": {
                "asset_manifest_json": ("STRING", {"forceInput": True}),
                "verify_single_object": ("BOOLEAN", {"default": True,
                    "label_on": "Verify + retry each asset", "label_off": "No verification"}),
                "verification": (["heuristic", "vlm", "heuristic+vlm"], {"default": "heuristic+vlm"}),
                "max_retries": ("INT", {"default": 2, "min": 0, "max": 5}),
                "ollama_url": ("STRING", {"default": "http://127.0.0.1:11434"}),
                "ollama_model": ("STRING", {"default": "gemma3:12b"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING", "INT")
    RETURN_NAMES = ("images", "generation_report_json", "image_count")
    FUNCTION = "generate_batch"
    CATEGORY = "Geekatplay GameAssetMake/Concepts"

    def _render_one(self, model, clip, vae, positive_text, negative_text,
                    width, height, channels, seed, steps, cfg, sampler_name, scheduler):
        import nodes as comfy_nodes
        encoder = comfy_nodes.CLIPTextEncode()
        positive = encoder.encode(clip, positive_text)[0]
        negative = encoder.encode(clip, negative_text)[0]
        latent = {"samples": torch.zeros([1, int(channels), height // 8, width // 8])}
        sampled = comfy_nodes.common_ksampler(
            model, seed, steps, cfg, sampler_name, scheduler,
            positive, negative, latent, denoise=1.0)[0]
        image = vae.decode(sampled["samples"])  # [1, H, W, C]
        return image[0]

    def _check(self, img, is_character, verification, ollama_url, ollama_model):
        details = {}
        ok = True
        if verification in ("heuristic", "heuristic+vlm"):
            ok, details["heuristic"] = heuristic_check(img)
        if verification in ("vlm", "heuristic+vlm"):
            vlm_ok, details["vlm"] = vlm_check(img, is_character, ollama_url, ollama_model)
            if vlm_ok is None:
                details["vlm"]["fallback"] = "heuristic only"
            else:
                ok = ok and vlm_ok
        return ok, details

    def generate_batch(self, model, clip, vae, prompt_list_json, negative_prompt,
                       width=1024, height=1024, steps=8, cfg=1.0,
                       sampler_name="euler", scheduler="simple", seed=0, latent_channels=16,
                       asset_manifest_json="", verify_single_object=True,
                       verification="heuristic+vlm", max_retries=2,
                       ollama_url="http://127.0.0.1:11434", ollama_model="gemma3:12b"):
        items = parse_prompt_items(prompt_list_json, asset_manifest_json)
        if not items:
            raise RuntimeError(
                "Batch Concept Generator received no prompts. Connect the Asset Planner's "
                "'prompt_list_json' output to this node's 'prompt_list_json' input."
            )

        try:
            import comfy.utils
            pbar = comfy.utils.ProgressBar(len(items))
        except Exception:
            pbar = None

        print(f"[GameAssetMake] Generating {len(items)} concept images — one per asset...")

        images, report = [], []
        for idx, item in enumerate(items):
            item_seed = (seed + idx * 1013904223) & 0xffffffffffffffff
            prompt, name = item["prompt"], item["name"]
            is_character = item["is_character"]

            img = self._render_one(model, clip, vae, prompt, negative_prompt,
                                   width, height, latent_channels, item_seed,
                                   steps, cfg, sampler_name, scheduler)

            passed, details, attempts = True, {}, 0
            if verify_single_object:
                passed, details = self._check(img, is_character, verification,
                                              ollama_url, ollama_model)
                while not passed and attempts < max_retries:
                    attempts += 1
                    retry_seed = (item_seed + attempts * 7919) & 0xffffffffffffffff
                    print(f"[GameAssetMake]   '{name}' rejected "
                          f"({details.get('heuristic', details.get('vlm', {})).get('reason', '?')}) "
                          f"— retry {attempts}/{max_retries}")
                    img = self._render_one(model, clip, vae, prompt + RETRY_SUFFIX,
                                           negative_prompt + ", " + RETRY_NEGATIVE,
                                           width, height, latent_channels, retry_seed,
                                           steps, cfg, sampler_name, scheduler)
                    passed, details = self._check(img, is_character, verification,
                                                  ollama_url, ollama_model)

            images.append(img)
            report.append({
                "index": idx, "name": name, "is_character": is_character,
                "passed": bool(passed), "retries_used": attempts,
                "seed": item_seed, "details": details,
            })
            status = "ok" if passed else "FAILED verification"
            print(f"[GameAssetMake]   [{idx + 1}/{len(items)}] {name} — {status}")
            if pbar:
                pbar.update(1)

        result = torch.stack(images, dim=0)
        print(f"[GameAssetMake] Done: {result.shape[0]} images "
              f"({sum(1 for r in report if not r['passed'])} failed verification)")

        return {
            "ui": {"guardrail_report": [report]},
            "result": (result, json.dumps(report, indent=2), len(images)),
        }
