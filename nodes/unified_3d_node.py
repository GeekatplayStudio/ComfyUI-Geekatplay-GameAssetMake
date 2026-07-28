import os
import json
import folder_paths
from .tripo_api import (
    submit_tripo_image_to_3d,
    submit_tripo_rigging,
    poll_tripo_task,
    download_file
)
from .meshy_api import (
    submit_meshy_image_to_3d,
    poll_meshy_task
)
from .hitem3d_api import (
    submit_hitem3d_image_to_3d,
    poll_hitem3d_task,
    extract_model_url
)
from . import keystore

class Unified3DGeneratorNode:
    """
    Executes 3D model generation, texturing, and auto-rigging tasks via Tripo3D or Meshy APIs
    for each approved concept asset in the manifest batch.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "approved_assets_json": ("STRING", {"forceInput": True}),
                "engine": (["tripo", "meshy", "hitem3d"], {"default": "tripo"}),
                "output_format": (["FBX", "GLB", "OBJ"], {"default": "FBX"}),
                "default_topology": (["quad", "triangle"], {"default": "quad"}),
                "target_face_count": ("INT", {"default": 15000, "min": 1000, "max": 100000, "step": 1000}),
                "dry_run_mock": ("BOOLEAN", {"default": True, "label_on": "Enable Mocking (No API Usage)", "label_off": "Live Cloud APIs"}),
            },
            "optional": {
                # Keys are OPTIONAL: stored keys (OS credential vault / env vars) are
                # used automatically. Type a key once with remember_keys ON to save it
                # locally — keys are never written into workflow JSON.
                "tripo_api_key": ("STRING", {"default": "", "multiline": False, "password": True}),
                "meshy_api_key": ("STRING", {"default": "", "multiline": False, "password": True}),
                # HiTem3D issues an AK/SK pair — two separate fields
                "hitem3d_access_key": ("STRING", {"default": "", "multiline": False, "password": True, "placeholder": "Access Key (ak_...)"}),
                "hitem3d_secret_key": ("STRING", {"default": "", "multiline": False, "password": True, "placeholder": "Secret Key (sk_...)"}),
                "remember_keys": ("BOOLEAN", {"default": True, "label_on": "Save typed keys to OS vault", "label_off": "Use typed keys once"}),
            }
        }

    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("completed_3d_manifest_json", "asset_count")
    FUNCTION = "generate_3d_assets"
    CATEGORY = "Geekatplay GameAssetMake/3D-Generator"
    OUTPUT_NODE = True

    def generate_3d_assets(self, approved_assets_json, engine="tripo", output_format="FBX", default_topology="quad", target_face_count=15000, dry_run_mock=True, tripo_api_key="", meshy_api_key="", hitem3d_access_key="", hitem3d_secret_key="", remember_keys=True):
        try:
            assets = json.loads(approved_assets_json)
        except Exception:
            assets = []

        # Typed keys win (and are persisted when remember_keys is on);
        # otherwise keys come from the OS credential vault / env vars.
        tripo_key = keystore.resolve_key("tripo", tripo_api_key, remember_keys)
        meshy_key = keystore.resolve_key("meshy", meshy_api_key, remember_keys)
        hitem3d_access, hitem3d_secret = keystore.resolve_hitem3d_keys(
            hitem3d_access_key, hitem3d_secret_key, remember_keys)

        output_dir = os.path.join(folder_paths.get_output_directory(), "3d_game_assets")
        os.makedirs(output_dir, exist_ok=True)

        completed_manifest = []

        for idx, item in enumerate(assets):
            asset_id = item.get("id", f"asset_{idx:02d}")
            asset_name = item.get("name", f"GameAsset_{idx}")
            img_path = item.get("image_path", "")
            # Engine is a global choice on this node; per-asset engine_target in the manifest is ignored
            inc_texture = item.get("include_texture", True)
            inc_rigging = item.get("include_rigging", False)
            rig_type = item.get("rig_type", "biped")

            model_filename = f"{asset_id}_{asset_name.replace(' ', '_')}.{output_format.lower()}"
            model_dest_path = os.path.join(output_dir, model_filename)

            if dry_run_mock:
                # Mock generation mode creates valid placeholder metadata file
                with open(model_dest_path, "w") as f:
                    f.write(f"# MOCK 3D MESH FILE FOR {asset_name}\n# Engine: {engine}\n# Rigged: {inc_rigging}\n")
                
                status_msg = "MOCK_SUCCESS"
            elif not img_path or not os.path.exists(img_path):
                status_msg = "SKIPPED_NO_SOURCE_IMAGE"
            else:
                try:
                    if engine == "tripo" and tripo_key:
                        task_id = submit_tripo_image_to_3d(
                            tripo_key,
                            img_path,
                            quad=(default_topology == "quad"),
                            face_limit=target_face_count,
                            pbr=inc_texture
                        )
                        res = poll_tripo_task(tripo_key, task_id)
                        
                        # Extract model URL
                        model_urls = res.get("output", {})
                        model_url = model_urls.get("pbr_model") or model_urls.get("model") or model_urls.get("base_model")

                        if inc_rigging and rig_type != "none":
                            try:
                                rig_task_id = submit_tripo_rigging(tripo_key, task_id, rig_type=rig_type)
                                rig_res = poll_tripo_task(tripo_key, rig_task_id)
                                rig_url = rig_res.get("output", {}).get("model")
                                if rig_url:
                                    model_url = rig_url
                            except Exception as rig_err:
                                print(f"[Tripo Rigging Warning] Asset {asset_id}: {rig_err}")

                        if model_url:
                            download_file(model_url, model_dest_path)
                            status_msg = "LIVE_SUCCESS"
                        else:
                            status_msg = "FAILED_NO_URL"

                    elif engine == "meshy" and meshy_key:
                        task_id = submit_meshy_image_to_3d(
                            meshy_key,
                            img_path,
                            topology=default_topology,
                            target_poly_count=target_face_count,
                            enable_pbr=inc_texture
                        )
                        res = poll_meshy_task(meshy_key, task_id)
                        
                        model_urls = res.get("model_urls", {})
                        model_url = model_urls.get(output_format.lower()) or model_urls.get("glb")
                        
                        if model_url:
                            download_file(model_url, model_dest_path)
                            status_msg = "LIVE_SUCCESS"
                        else:
                            status_msg = "FAILED_NO_URL"

                    elif engine == "hitem3d" and hitem3d_access and hitem3d_secret:
                        task_id = submit_hitem3d_image_to_3d(
                            hitem3d_access,
                            hitem3d_secret,
                            img_path,
                            target_poly_count=max(100000, target_face_count),
                            enable_pbr=inc_texture,
                            output_format=output_format.lower() if output_format.lower() in ("obj", "glb", "fbx") else "glb"
                        )
                        res = poll_hitem3d_task(hitem3d_access, hitem3d_secret, task_id)
                        model_url = extract_model_url(res)

                        if model_url:
                            download_file(model_url, model_dest_path)
                            status_msg = "LIVE_SUCCESS"
                        else:
                            status_msg = "FAILED_NO_URL"
                    else:
                        status_msg = "SKIPPED_NO_API_KEY"
                except Exception as e:
                    status_msg = f"ERROR: {str(e)}"
                    # Fallback file creation to allow pipeline continuity
                    with open(model_dest_path, "w") as f:
                        f.write(f"# FALLBACK FILE: {str(e)}")

            completed_item = dict(item)
            completed_item["model_path"] = model_dest_path
            completed_item["model_format"] = output_format
            completed_item["engine_used"] = engine
            completed_item["generation_status"] = status_msg
            completed_manifest.append(completed_item)

        # Results list rendered by the node's web widget (all 3D models returned from the API)
        model_results = [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "engine": engine,
                "status": c.get("generation_status"),
                "model_path": c.get("model_path"),
                "format": c.get("model_format"),
            }
            for c in completed_manifest
        ]

        return {
            "ui": {"generated_models": [model_results]},
            "result": (
                json.dumps(completed_manifest, indent=2),
                len(completed_manifest)
            ),
        }
