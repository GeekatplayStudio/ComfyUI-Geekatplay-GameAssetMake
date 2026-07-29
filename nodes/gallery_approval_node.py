# =============================================================
# Geekatplay GameAssetMake — Batch Asset Gallery & Approval node
# (c) Geekatplay Studio / Vladimir Chopine
# =============================================================
import os
import json
import hashlib
import numpy as np
from PIL import Image
import folder_paths

try:
    from comfy_execution.graph_utils import ExecutionBlocker
except ImportError:      # older ComfyUI
    ExecutionBlocker = None


def batch_signature(images, manifest):
    """
    Identifies THIS set of concept images. When new images are generated the
    signature changes, so a previous approval can never silently auto-pass a
    fresh batch — you always review what you are actually looking at.
    """
    h = hashlib.sha1()
    h.update(str(len(manifest)).encode())
    h.update(str(tuple(images.shape)).encode())
    arr = images.detach().cpu().numpy()
    # a few deterministic samples are enough to fingerprint the batch cheaply
    for i in range(arr.shape[0]):
        frame = arr[i]
        h.update(np.asarray([frame.mean(), frame.std(),
                             frame[::7, ::7].sum()], dtype=np.float64).tobytes())
    return h.hexdigest()[:16]

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
                "approval_mode": ([
                    "PAUSE for my approval",
                    "Approve All (Auto)",
                    "Approve Characters Only",
                    "Approve Environment Only",
                    "Approve Characters + Accessories",
                ], {"default": "PAUSE for my approval",
                    "tooltip": "PAUSE stops the run after the concept images so you can pick "
                               "what becomes 3D, what gets rigged, or regenerate."}),
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

        # Images pair 1:1 with manifest entries. Recycling one image across several
        # assets (the old `idx % len` behaviour) silently made every asset produce an
        # IDENTICAL mesh, so a mismatch is reported loudly and the extra assets are
        # dropped rather than duplicated.
        if len(saved_image_paths) != len(manifest):
            print(f"[GameAssetMake Gallery] WARNING: {len(saved_image_paths)} concept image(s) "
                  f"but {len(manifest)} manifest asset(s). Each asset needs its own image — "
                  f"only the first {min(len(saved_image_paths), len(manifest))} will be used. "
                  f"Use the Batch Concept Generator (one image per asset) to keep these in sync.")

        sig = batch_signature(images, manifest)
        approved_sig = manual_override.get("__batch__") if manual_override else None
        selections = {k: v for k, v in (manual_override or {}).items() if not k.startswith("__")}
        # An approval only counts for the batch it was made on: regenerate the
        # images and you review the NEW ones instead of being auto-passed.
        has_approval = bool(selections) and approved_sig == sig

        GROUP_MODES = {
            "Approve Characters Only": ("character",),
            "Approve Environment Only": ("environment",),
            "Approve Characters + Accessories": ("character", "accessory"),
        }
        ENV_CATEGORIES = ("wall", "floor", "ceiling", "stairs", "doorway",
                          "corner", "column", "archway", "structure")

        for idx, item in enumerate(manifest):
            if idx >= len(saved_image_paths):
                print(f"[GameAssetMake Gallery] Skipping '{item.get('name', idx)}' — no concept image for it.")
                continue
            img_path = saved_image_paths[idx]
            img_file = saved_image_files[idx]

            item_copy = dict(item)
            item_copy["image_path"] = img_path
            item_id = item.get("id", f"asset_{idx:02d}")
            group = item.get("asset_group") or (
                "character" if item.get("category") in ("hero", "enemy", "boss", "npc")
                else "environment" if item.get("category") in ENV_CATEGORIES
                else "accessory")
            item_copy["asset_group"] = group

            gallery_items.append({
                "id": item_id,
                "name": item.get("name", item_id),
                "category": item.get("category", ""),
                "asset_group": group,
                "can_rig": group == "character",
                "include_texture": item_copy.get("include_texture", True),
                "include_rigging": item_copy.get("include_rigging", False),
                "rig_type": item_copy.get("rig_type", "none"),
                "filename": img_file,
                "subfolder": GALLERY_SUBFOLDER,
                "type": "temp",
            })

            if has_approval:
                sel = selections.get(item_id)
                if not sel or not sel.get("approved", False):
                    continue
                item_copy["include_texture"] = sel.get("include_texture", item_copy.get("include_texture", True))
                item_copy["include_rigging"] = sel.get("include_rigging", item_copy.get("include_rigging", False))
                item_copy["rig_type"] = sel.get("rig_type", item_copy.get("rig_type", "none"))
                if group != "character":          # only characters can be rigged
                    item_copy["include_rigging"], item_copy["rig_type"] = False, "none"
                approved_manifest.append(item_copy)
                approved_paths.append(img_path)
            elif approval_mode == "Approve All (Auto)":
                approved_manifest.append(item_copy)
                approved_paths.append(img_path)
            elif approval_mode in GROUP_MODES and group in GROUP_MODES[approval_mode]:
                approved_manifest.append(item_copy)
                approved_paths.append(img_path)

        counts = {}
        for gi in gallery_items:
            counts[gi["asset_group"]] = counts.get(gi["asset_group"], 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "nothing"

        # ------------------------- the PAUSE -------------------------------
        if approval_mode == "PAUSE for my approval" and not has_approval:
            why = ("new concept images were generated"
                   if selections and approved_sig != sig else "waiting for your selection")
            print(f"[GameAssetMake Gallery] PAUSED — {why}. {len(gallery_items)} concept "
                  f"image(s) ready ({summary}). Review them on the node, tick what should "
                  f"become 3D (and what to rig), then press 'Approve & Continue'. "
                  f"Press 'Regenerate Images' to redo the concepts instead.")
            blocked = ExecutionBlocker(None) if ExecutionBlocker is not None else ""
            return {
                "ui": {"gallery_items": [gallery_items],
                       "batch_signature": [sig],
                       "awaiting_approval": [True]},
                "result": (blocked, blocked, 0),
            }

        print(f"[GameAssetMake Gallery] {len(approved_manifest)}/{len(gallery_items)} approved "
              f"({summary}) — continuing to 3D generation.")

        return {
            "ui": {"gallery_items": [gallery_items],
                   "batch_signature": [sig],
                   "awaiting_approval": [False]},
            "result": (
                json.dumps(approved_manifest, indent=2),
                json.dumps(approved_paths, indent=2),
                len(approved_manifest)
            ),
        }
