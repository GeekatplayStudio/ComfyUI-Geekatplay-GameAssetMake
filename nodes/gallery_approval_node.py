# =============================================================
# Geekatplay GameAssetMake — Batch Asset Gallery & Approval node
# (c) Geekatplay Studio / Vladimir Chopine
# =============================================================
import os
import json
import numpy as np
from PIL import Image
import folder_paths

GALLERY_SUBFOLDER = "geekatplay_gallery"


class GalleryApprovalNode:
    """
    Receives generated 2D concept images and the asset manifest,
    presents them to the interactive Gallery web extension, and outputs
    only approved items with their target parameters (Tripo vs Meshy, texturing, rigging).
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "asset_manifest_json": ("STRING", {"forceInput": True}),
                "approval_mode": (["Approve All (Auto)", "Manual UI Selection", "Approve Characters Only"], {"default": "Approve All (Auto)"}),
            },
            "optional": {
                "user_selection_override": ("STRING", {
                    "multiline": True,
                    "default": ""
                }),
            },
            "hidden": {
                "extra_pnginfo": "EXTRA_PNGINFO",
                "unique_id": "UNIQUE_ID"
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("approved_assets_json", "image_paths_json", "approved_count")
    FUNCTION = "process_approval"
    CATEGORY = "Geekatplay GameAssetMake/Gallery"
    OUTPUT_NODE = True

    def process_approval(self, images, asset_manifest_json, approval_mode="Approve All (Auto)", user_selection_override="", extra_pnginfo=None, unique_id=None):
        try:
            manifest = json.loads(asset_manifest_json)
        except Exception:
            manifest = []

        output_dir = os.path.join(folder_paths.get_temp_directory(), GALLERY_SUBFOLDER)
        os.makedirs(output_dir, exist_ok=True)
        batch_size = images.shape[0]

        saved_image_paths = []
        saved_image_files = []
        for i in range(batch_size):
            img_tensor = images[i]
            img_np = np.clip(img_tensor.cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
            pil_img = Image.fromarray(img_np)

            filename = f"concept_asset_{unique_id or '0'}_{i:03d}.png"
            filepath = os.path.join(output_dir, filename)
            pil_img.save(filepath)
            saved_image_paths.append(filepath)
            saved_image_files.append(filename)

        approved_manifest = []
        approved_paths = []
        gallery_items = []

        # Parse manual selection override supplied by the UI widget
        manual_override = {}
        if user_selection_override and user_selection_override.strip().startswith("{"):
            try:
                manual_override = json.loads(user_selection_override)
            except Exception:
                pass

        for idx, item in enumerate(manifest):
            if saved_image_paths:
                img_path = saved_image_paths[idx % len(saved_image_paths)]
                img_file = saved_image_files[idx % len(saved_image_files)]
            else:
                img_path = ""
                img_file = ""

            item_copy = dict(item)
            item_copy["image_path"] = img_path

            item_id = item.get("id", f"asset_{idx:02d}")

            # Data for the interactive web gallery (served via ComfyUI /view endpoint)
            gallery_items.append({
                "id": item_id,
                "name": item.get("name", item_id),
                "category": item.get("category", ""),
                "engine_target": item_copy.get("engine_target", "tripo"),
                "include_texture": item_copy.get("include_texture", True),
                "include_rigging": item_copy.get("include_rigging", False),
                "rig_type": item_copy.get("rig_type", "none"),
                "filename": img_file,
                "subfolder": GALLERY_SUBFOLDER,
                "type": "temp",
            })

            if manual_override and item_id in manual_override:
                selection = manual_override[item_id]
                if selection.get("approved", False):
                    item_copy["engine_target"] = selection.get("engine_target", item_copy.get("engine_target", "tripo"))
                    item_copy["include_texture"] = selection.get("include_texture", item_copy.get("include_texture", True))
                    item_copy["include_rigging"] = selection.get("include_rigging", item_copy.get("include_rigging", False))
                    item_copy["rig_type"] = selection.get("rig_type", item_copy.get("rig_type", "biped"))
                    approved_manifest.append(item_copy)
                    approved_paths.append(img_path)
            else:
                if approval_mode == "Approve All (Auto)":
                    approved_manifest.append(item_copy)
                    approved_paths.append(img_path)
                elif approval_mode == "Approve Characters Only":
                    if item_copy.get("category") in ["hero", "enemy", "boss", "npc"]:
                        approved_manifest.append(item_copy)
                        approved_paths.append(img_path)
                else:  # Manual UI Selection with no UI feedback yet: approve first 5 as a preview batch
                    if idx < min(5, len(manifest)):
                        approved_manifest.append(item_copy)
                        approved_paths.append(img_path)

        return {
            "ui": {"gallery_items": [gallery_items]},
            "result": (
                json.dumps(approved_manifest, indent=2),
                json.dumps(approved_paths, indent=2),
                len(approved_manifest)
            ),
        }
