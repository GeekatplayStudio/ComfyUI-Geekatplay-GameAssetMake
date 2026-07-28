import os
import json
import urllib.request
import urllib.error
from .engine_check_node import ping_engine_bridge, filter_deliverable_assets

class UnityEngineBridgeNode:
    """
    Sends generated 3D assets to Unity Engine Editor.
    Sends HTTP import request to Unity Editor server listener or exports Unity-compatible package/manifest.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "completed_3d_manifest_json": ("STRING", {"forceInput": True}),
                "unity_host": ("STRING", {"default": "127.0.0.1"}),
                "unity_port": ("INT", {"default": 8080, "min": 1024, "max": 65535}),
                "target_assets_folder": ("STRING", {"default": "Assets/AI_Generated/"}),
                # Master toggle: assets only land in the project, or are also
                # set up in the open scene (prefabs instantiated, skybox applied,
                # terrain positioned, sun light configured).
                "auto_instantiate_in_scene": ("BOOLEAN", {"default": True,
                    "label_on": "Import + Set Up In Scene",
                    "label_off": "Import As Assets Only"}),
            },
            "optional": {
                # Scene Director environment_json: sun az/el/color -> directional light
                "environment_json": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING", "INT", "BOOLEAN")
    RETURN_NAMES = ("unity_import_payload_json", "imported_count", "success")
    FUNCTION = "send_to_unity"
    CATEGORY = "Geekatplay GameAssetMake/Engine-Bridge"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Side effect (network send to the editor) — must always re-run.
        return float("NaN")

    def send_to_unity(
        self,
        completed_3d_manifest_json,
        unity_host="127.0.0.1",
        unity_port=8080,
        target_assets_folder="Assets/AI_Generated/",
        auto_instantiate_in_scene=True,
        environment_json=""
    ):
        try:
            manifest = json.loads(completed_3d_manifest_json)
        except Exception:
            manifest = []

        manifest, _skipped = filter_deliverable_assets(manifest, "Unity Bridge")

        payload = {
            "target_assets_folder": target_assets_folder,
            "auto_instantiate": auto_instantiate_in_scene,
            "assets": manifest
        }

        if environment_json:
            try:
                payload["environment"] = json.loads(environment_json)
            except Exception:
                pass

        payload_str = json.dumps(payload, indent=2)
        success = False

        # Verify the Unity bridge is actually running before sending
        online, ping_msg = ping_engine_bridge(unity_host, unity_port, engine="unity")
        print(f"[Unity Bridge Check] {ping_msg}")
        if not online:
            return (payload_str, len(manifest), False)

        # Trailing slash required: Unity's HttpListener prefix is registered as /import_assets/
        url = f"http://{unity_host}:{unity_port}/import_assets/"
        try:
            req = urllib.request.Request(
                url,
                data=payload_str.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    success = True
        except Exception as err:
            print(f"[Unity Bridge] HTTP status: {err}")

        return (payload_str, len(manifest), success)
