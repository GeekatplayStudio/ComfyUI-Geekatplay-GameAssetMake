# =============================================================
# Geekatplay GameAssetMake — Local Hunyuan3D 2.1 Generator
# (c) Geekatplay Studio / Vladimir Chopine
#
# Fully LOCAL image-to-3D: no cloud API, no keys, no credits.
# Loops over the approved assets and runs the Hunyuan3D 2.1 chain
# per asset — CLIPVisionEncode -> Hunyuan3Dv2Conditioning ->
# KSampler -> VAEDecodeHunyuan3D -> VoxelToMesh -> .glb — emitting
# the same manifest the Unreal/Unity bridge nodes already consume.
#
# Model: hunyuan_3d_v2.1.safetensors in models/checkpoints,
# loaded with an ImageOnlyCheckpointLoader (MODEL/CLIP_VISION/VAE).
# =============================================================
import os
import json
import numpy as np
import torch
import folder_paths


def _load_image_as_tensor(image_path):
    """Loads an image file into a ComfyUI IMAGE tensor [1, H, W, 3]."""
    from PIL import Image, ImageOps
    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img)
    if img.mode == "RGBA":
        # Composite onto white so the alpha edge doesn't become geometry
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(background, img)
    img = img.convert("RGB")
    arr = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None, ]


class LocalHunyuan3DGeneratorNode:
    """
    Generates 3D meshes locally with Hunyuan3D 2.1 for every approved asset.
    Drop-in replacement for the cloud 3D Generator: same input/output contract.
    """

    @classmethod
    def INPUT_TYPES(cls):
        try:
            import comfy.samplers
            samplers = comfy.samplers.KSampler.SAMPLERS
            schedulers = comfy.samplers.KSampler.SCHEDULERS
        except Exception:
            samplers, schedulers = ["euler"], ["normal"]
        return {
            "required": {
                "model": ("MODEL",),
                "clip_vision": ("CLIP_VISION",),
                "vae": ("VAE",),
                "approved_assets_json": ("STRING", {"forceInput": True}),
                "steps": ("INT", {"default": 30, "min": 1, "max": 200}),
                "cfg": ("FLOAT", {"default": 5.0, "min": 0.0, "max": 20.0, "step": 0.1}),
                "sampler_name": (samplers, {"default": "euler"}),
                "scheduler": (schedulers, {"default": "normal"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "latent_resolution": ("INT", {"default": 4096, "min": 1, "max": 8192, "step": 256,
                                              "tooltip": "Hunyuan3D latent token count — higher is more detail, slower."}),
                "octree_resolution": ("INT", {"default": 256, "min": 16, "max": 1024, "step": 16,
                                              "tooltip": "Voxel grid resolution used when decoding to geometry."}),
                "mesh_algorithm": (["surface net", "basic"], {"default": "surface net"}),
                "mesh_threshold": ("FLOAT", {"default": 0.6, "min": -1.0, "max": 1.0, "step": 0.01}),
            },
            "optional": {
                "num_chunks": ("INT", {"default": 8000, "min": 1000, "max": 500000, "step": 1000}),
                "crop_mode": (["center", "none"], {"default": "center"}),
            },
        }

    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("completed_3d_manifest_json", "asset_count")
    FUNCTION = "generate_local"
    CATEGORY = "Geekatplay GameAssetMake/3D-Generator"
    OUTPUT_NODE = True

    def generate_local(self, model, clip_vision, vae, approved_assets_json,
                       steps=30, cfg=5.0, sampler_name="euler", scheduler="normal",
                       seed=0, latent_resolution=4096, octree_resolution=256,
                       mesh_algorithm="surface net", mesh_threshold=0.6,
                       num_chunks=8000, crop_mode="center"):
        import nodes as comfy_nodes
        from comfy_extras.nodes_hunyuan3d import (
            EmptyLatentHunyuan3Dv2, Hunyuan3Dv2Conditioning,
            VAEDecodeHunyuan3D, VoxelToMesh,
        )
        from comfy_extras.nodes_model_advanced import ModelSamplingAuraFlow
        from comfy_extras.nodes_save_3d import save_glb, get_mesh_batch_item

        try:
            assets = json.loads(approved_assets_json)
        except Exception:
            assets = []
        if not assets:
            raise RuntimeError(
                "Local Hunyuan3D Generator received no assets. Connect the Gallery "
                "Approval node's 'approved_assets_json' output."
            )

        out_dir = os.path.join(folder_paths.get_output_directory(), "3d_game_assets")
        os.makedirs(out_dir, exist_ok=True)

        # Hunyuan3D expects the AuraFlow sampling shift applied once, up front.
        patched_model = ModelSamplingAuraFlow().patch_aura(model, 1.0)[0]

        try:
            import comfy.utils
            pbar = comfy.utils.ProgressBar(len(assets))
        except Exception:
            pbar = None

        print(f"[GameAssetMake] Local Hunyuan3D: generating {len(assets)} meshes "
              f"(no API, no credits)...")

        completed, results = [], []
        for idx, item in enumerate(assets):
            asset_id = item.get("id", f"asset_{idx:02d}")
            asset_name = item.get("name", f"GameAsset_{idx}")
            img_path = item.get("image_path", "")
            safe_name = str(asset_name).replace(" ", "_")
            dest_path = os.path.join(out_dir, f"{asset_id}_{safe_name}.glb")

            completed_item = dict(item)
            completed_item["model_format"] = "GLB"
            completed_item["engine_used"] = "hunyuan3d_local"
            completed_item["model_path"] = dest_path

            if not img_path or not os.path.exists(img_path):
                completed_item["generation_status"] = "SKIPPED_NO_SOURCE_IMAGE"
                completed.append(completed_item)
                results.append({"id": asset_id, "name": asset_name,
                                "engine": "hunyuan3d_local", "status": "SKIPPED_NO_SOURCE_IMAGE",
                                "model_path": dest_path, "format": "GLB"})
                print(f"[GameAssetMake]   [{idx + 1}/{len(assets)}] {asset_name} — no source image, skipped")
                if pbar:
                    pbar.update(1)
                continue

            try:
                image = _load_image_as_tensor(img_path)
                vision_out = comfy_nodes.CLIPVisionEncode().encode(
                    clip_vision, image, crop_mode)[0]
                positive, negative = Hunyuan3Dv2Conditioning.execute(vision_out).result
                latent = EmptyLatentHunyuan3Dv2.execute(latent_resolution, 1).result[0]

                item_seed = (seed + idx * 1013904223) & 0xffffffffffffffff
                sampled = comfy_nodes.common_ksampler(
                    patched_model, item_seed, steps, cfg, sampler_name, scheduler,
                    positive, negative, latent, denoise=1.0)[0]

                voxels = VAEDecodeHunyuan3D.execute(
                    vae, sampled, num_chunks, octree_resolution).result[0]
                mesh = VoxelToMesh.execute(voxels, mesh_algorithm, mesh_threshold).result[0]

                vertices, faces, v_colors, uvs = get_mesh_batch_item(mesh, 0)
                if vertices.shape[0] == 0 or faces.shape[0] == 0:
                    raise RuntimeError("Hunyuan3D produced an empty mesh "
                                       "(try lowering mesh_threshold)")

                save_glb(vertices, faces, dest_path, {},
                         uvs=uvs, vertex_colors=v_colors,
                         texture_image=None,
                         unlit=getattr(mesh, "unlit", False))

                status = "LOCAL_SUCCESS"
                print(f"[GameAssetMake]   [{idx + 1}/{len(assets)}] {asset_name} — "
                      f"{vertices.shape[0]} verts / {faces.shape[0]} faces -> {os.path.basename(dest_path)}")
            except Exception as exc:
                status = f"ERROR: {exc}"
                print(f"[GameAssetMake]   [{idx + 1}/{len(assets)}] {asset_name} — FAILED: {exc}")

            completed_item["generation_status"] = status
            completed.append(completed_item)
            results.append({"id": asset_id, "name": asset_name, "engine": "hunyuan3d_local",
                            "status": status, "model_path": dest_path, "format": "GLB"})
            if pbar:
                pbar.update(1)

        ok = sum(1 for r in results if r["status"] == "LOCAL_SUCCESS")
        print(f"[GameAssetMake] Local Hunyuan3D done: {ok}/{len(assets)} meshes generated.")

        return {
            "ui": {"generated_models": [results]},
            "result": (json.dumps(completed, indent=2), len(completed)),
        }
