# =============================================================
# Geekatplay GameAssetMake — Environment Asset Export node
# (c) Geekatplay Studio / Vladimir Chopine
#
# Packages terrain heightmaps, skydome HDRIs, and tileable textures
# as bridge-compatible manifests so the SAME Unreal/Unity bridge
# nodes deliver them into the engine alongside 3D models.
# Terrain heightmaps are written as 16-bit grayscale PNG (the format
# Unreal's Landscape and Unity's Terrain tools import directly).
# =============================================================
import os
import json
import numpy as np
import folder_paths


class EnvironmentAssetExportNode:
    """
    Turns an environment image (or an already-saved file such as SaveFakeHDRI's
    EXR output) into a completed-asset manifest for the engine bridge nodes.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "asset_type": (["terrain_heightmap", "skydome_hdri", "tileable_texture"], {"default": "terrain_heightmap"}),
                "asset_name": ("STRING", {"default": "Terrain_01"}),
            },
            "optional": {
                "image": ("IMAGE",),
                "file_path": ("STRING", {"default": "", "forceInput": True}),
                "terrain_world_size_m": ("FLOAT", {"default": 1000.0, "min": 10.0, "max": 100000.0, "step": 10.0}),
                "terrain_height_m": ("FLOAT", {"default": 200.0, "min": 1.0, "max": 20000.0, "step": 1.0}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("completed_3d_manifest_json", "file_path")
    FUNCTION = "export_environment"
    CATEGORY = "Geekatplay GameAssetMake/Environment"
    OUTPUT_NODE = True

    def export_environment(self, asset_type, asset_name, image=None, file_path="",
                           terrain_world_size_m=1000.0, terrain_height_m=200.0):
        from PIL import Image as PILImage

        out_dir = os.path.join(folder_paths.get_output_directory(), "game_environment")
        os.makedirs(out_dir, exist_ok=True)
        safe_name = asset_name.strip().replace(" ", "_") or "Environment_Asset"

        if file_path and os.path.exists(file_path):
            final_path = file_path
        elif image is not None:
            arr = image[0].cpu().numpy()
            if asset_type == "terrain_heightmap":
                # 16-bit grayscale PNG — the heightmap format both engines import
                gray = arr.mean(axis=2) if arr.ndim == 3 else arr
                gray16 = np.clip(gray * 65535.0, 0, 65535).astype(np.uint16)
                final_path = os.path.join(out_dir, f"{safe_name}_heightmap16.png")
                PILImage.fromarray(gray16, mode="I;16").save(final_path)
            else:
                rgb = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
                final_path = os.path.join(out_dir, f"{safe_name}.png")
                PILImage.fromarray(rgb).save(final_path)
        else:
            raise RuntimeError("EnvironmentAssetExport needs either an 'image' input or a valid 'file_path'.")

        category = {
            "terrain_heightmap": "terrain",
            "skydome_hdri": "skydome",
            "tileable_texture": "texture",
        }[asset_type]

        manifest = [{
            "id": f"env_{safe_name.lower()}",
            "name": safe_name,
            "category": category,
            "model_path": final_path,
            "model_format": os.path.splitext(final_path)[1].lstrip(".").upper(),
            "rig_type": "none",
            "include_rigging": False,
            "scale_override": [1.0, 1.0, 1.0],
            "collision_type": "none",
            "world_placement_offset": [0.0, 0.0, 0.0],
            "generation_status": "ENV_EXPORT_OK",
            "terrain_world_size_m": terrain_world_size_m,
            "terrain_height_m": terrain_height_m,
        }]

        print(f"[GameAssetMake Environment] Exported {category} '{safe_name}' -> {final_path}")
        return (json.dumps(manifest, indent=2), final_path)
