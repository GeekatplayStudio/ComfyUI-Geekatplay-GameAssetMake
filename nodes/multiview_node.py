# =============================================================
# Geekatplay GameAssetMake — Orthographic Multi-View Generator
# (c) Geekatplay Studio / Vladimir Chopine — https://www.geekatplay.com
#
# Renders an orthographic turnaround (front / left / right / back)
# for every asset in the manifest, reusing the SAME per-asset seed for
# every view so the views describe one consistent object rather than
# four different ones. A single 3/4 concept image leaves the far side
# of an asset to the 3D model's imagination; a turnaround pins it down.
#
# Views are saved to output/multiview/ and their paths written into the
# manifest (view_image_paths), so multiview-capable image-to-3D APIs —
# and human artists — can pick them up. The manifest passes through
# otherwise unchanged, so this node can sit anywhere on the STRING
# manifest chain without disturbing the pipeline.
# =============================================================
import os
import json
import numpy as np
import torch

import folder_paths

from .batch_concept_node import parse_prompt_items

VIEW_SETS = {
    "front + back": ("front", "back"),
    "front + left + right": ("front", "left", "right"),
    "front + left + right + back (full turnaround)": ("front", "left", "right", "back"),
}

# The view phrase REPLACES the "3/4 view facing the camera" wording the planner
# bakes into asset prompts — two view instructions in one prompt fight each other.
VIEW_TEXT = {
    "front": "orthographic front view, facing the camera straight on",
    "left": "orthographic left side profile view, perfect side-on silhouette",
    "right": "orthographic right side profile view, perfect side-on silhouette",
    "back": "orthographic back view, seen directly from behind",
}

_VIEW_WORDINGS = (
    "3/4 view facing the camera",
    "straight-on 3/4 view",
)


class OrthographicMultiViewNode:
    """
    Turns each asset prompt into a consistent orthographic turnaround sheet:
    one image per view, same seed per asset, view paths added to the manifest.
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
                "asset_manifest_json": ("STRING", {"forceInput": True}),
                "views": (list(VIEW_SETS), {"default": "front + left + right + back (full turnaround)",
                    "tooltip": "Which orthographic views to render for every asset. "
                               "All views of one asset share one seed, so they stay consistent."}),
                "negative_prompt": ("STRING", {"multiline": True,
                    "default": "multiple objects, group, collage, grid, reference sheet, "
                               "perspective distortion, text, watermark, cropped, blurry"}),
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
        }

    RETURN_TYPES = ("IMAGE", "STRING", "INT")
    RETURN_NAMES = ("view_images", "manifest_with_views_json", "view_count")
    FUNCTION = "generate_views"
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
        return vae.decode(sampled["samples"])[0]  # [H, W, C]

    @staticmethod
    def _view_prompt(base_prompt, view):
        """Swap the generic 3/4-view wording for this view's orthographic wording."""
        prompt = base_prompt
        for phrase in _VIEW_WORDINGS:
            prompt = prompt.replace(phrase, VIEW_TEXT[view])
        if VIEW_TEXT[view] not in prompt:
            prompt = f"{prompt}, {VIEW_TEXT[view]}"
        return prompt

    def generate_views(self, model, clip, vae, asset_manifest_json,
                       views="front + left + right + back (full turnaround)",
                       negative_prompt="", width=1024, height=1024, steps=8, cfg=1.0,
                       sampler_name="euler", scheduler="simple", seed=0, latent_channels=16):
        try:
            manifest = json.loads(asset_manifest_json)
            if not isinstance(manifest, list):
                manifest = []
        except Exception:
            manifest = []
        if not manifest:
            raise RuntimeError(
                "Orthographic Multi-View received no assets. Connect an asset manifest "
                "(the Planner's or Extractor's 'asset_manifest_json', or the Gallery's "
                "'approved_assets_json')."
            )

        view_names = VIEW_SETS[views]
        out_dir = os.path.join(folder_paths.get_output_directory(), "multiview")
        os.makedirs(out_dir, exist_ok=True)

        try:
            import comfy.utils
            pbar = comfy.utils.ProgressBar(len(manifest) * len(view_names))
        except Exception:
            pbar = None

        from PIL import Image
        print(f"[Multi-View] {len(manifest)} asset(s) x {len(view_names)} views...")

        frames, out_manifest = [], []
        for idx, item in enumerate(manifest):
            entry = dict(item)
            name = entry.get("name", f"asset_{idx:02d}")
            asset_id = entry.get("id", f"asset_{idx:02d}")
            base_prompt = entry.get("prompt", name)
            asset_seed = (seed + idx * 1013904223) & 0xffffffffffffffff

            view_paths = {}
            for view in view_names:
                img = self._render_one(model, clip, vae,
                                       self._view_prompt(base_prompt, view),
                                       negative_prompt, width, height,
                                       latent_channels, asset_seed,
                                       steps, cfg, sampler_name, scheduler)
                frames.append(img)
                path = os.path.join(
                    out_dir, f"{asset_id}_{name.replace(' ', '_')}_{view}.png")
                Image.fromarray(np.clip(img.cpu().numpy() * 255.0, 0, 255)
                                .astype(np.uint8)).save(path)
                view_paths[view] = path
                if pbar:
                    pbar.update(1)

            entry["view_image_paths"] = view_paths
            out_manifest.append(entry)
            print(f"[Multi-View]   [{idx + 1}/{len(manifest)}] {name} — "
                  f"{'/'.join(view_names)} saved.")

        result = torch.stack(frames, dim=0)
        print(f"[Multi-View] Done: {result.shape[0]} view images in {out_dir}")
        return (result, json.dumps(out_manifest, indent=2), int(result.shape[0]))
