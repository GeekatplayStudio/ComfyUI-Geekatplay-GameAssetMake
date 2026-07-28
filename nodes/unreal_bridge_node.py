import os
import json
import socket
import urllib.request
import urllib.error
from .engine_check_node import ping_engine_bridge, filter_deliverable_assets

class UnrealEngineBridgeNode:
    """
    Sends generated 3D assets to Unreal Engine Editor.
    Triggers automated asset importing, PBR material creation, collision setup, unit scale conversion,
    and automatic world level placement via Remote Control API, Python Socket, or File Sync.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "completed_3d_manifest_json": ("STRING", {"forceInput": True}),
                "unreal_host": ("STRING", {"default": "127.0.0.1"}),
                "unreal_port": ("INT", {"default": 30010, "min": 1024, "max": 65535}),
                "communication_mode": (["HTTP Bridge Plugin (Port 30010)", "Python Remote Socket", "JSON Manifest File Sync"], {"default": "HTTP Bridge Plugin (Port 30010)"}),
                "target_content_folder": ("STRING", {"default": "/Game/Assets/AI_Generated/"}),
                "unit_scale_factor": ("FLOAT", {"default": 100.0, "min": 0.01, "max": 10000.0, "step": 1.0}),
                # Master toggle: assets only land in the Content Browser, or are
                # also placed/set up in the level (meshes spawned, skydome applied,
                # terrain positioned, sun light configured).
                "auto_place_in_level": ("BOOLEAN", {"default": True,
                    "label_on": "Import + Set Up In Scene",
                    "label_off": "Import As Assets Only"}),
                "auto_generate_collisions": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "unreal_project_content_dir": ("STRING", {"default": ""}),
                # Scene Director environment_json: sun azimuth/elevation/color ->
                # a DirectionalLight is set up in the level automatically
                "environment_json": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING", "INT", "BOOLEAN")
    RETURN_NAMES = ("unreal_import_payload_json", "imported_count", "success")
    FUNCTION = "send_to_unreal"
    CATEGORY = "Geekatplay GameAssetMake/Engine-Bridge"
    OUTPUT_NODE = True

    def send_to_unreal(
        self,
        completed_3d_manifest_json,
        unreal_host="127.0.0.1",
        unreal_port=30010,
        communication_mode="HTTP Bridge Plugin (Port 30010)",
        target_content_folder="/Game/Assets/AI_Generated/",
        unit_scale_factor=100.0,
        auto_place_in_level=True,
        auto_generate_collisions=True,
        unreal_project_content_dir="",
        environment_json=""
    ):
        try:
            manifest = json.loads(completed_3d_manifest_json)
        except Exception:
            manifest = []

        import_payload = {
            "target_content_folder": target_content_folder,
            "unit_scale_factor": unit_scale_factor,
            "auto_place_in_level": auto_place_in_level,
            "auto_generate_collisions": auto_generate_collisions,
            "assets": []
        }

        if environment_json:
            try:
                import_payload["environment"] = json.loads(environment_json)
            except Exception:
                pass

        manifest, _skipped = filter_deliverable_assets(manifest, "Unreal Bridge")

        for item in manifest:
            asset_entry = {
                "id": item.get("id"),
                "name": item.get("name"),
                "category": item.get("category"),
                "source_file": item.get("model_path"),
                "format": item.get("model_format", "FBX"),
                "rig_type": item.get("rig_type", "none"),
                "scale": item.get("scale_override", [1.0, 1.0, 1.0]),
                "collision": item.get("collision_type", "box"),
                "location": item.get("world_placement_offset", [0.0, 0.0, 0.0]),
                "rotation_yaw": item.get("world_rotation_yaw", 0.0),
                # Placement Manager fields: intended real-world size + normalization
                "target_size_m": item.get("target_size_m"),
                "normalize_to_target": item.get("normalize_to_target", False),
                "ground_z_cm": item.get("ground_z_cm"),
                "verify_world_size": item.get("verify_world_size", False),
                "terrain_world_size_m": item.get("terrain_world_size_m"),
            }
            import_payload["assets"].append(asset_entry)

        payload_str = json.dumps(import_payload, indent=2)
        success = False

        if communication_mode == "HTTP Bridge Plugin (Port 30010)":
            # Verify the Unreal bridge is actually running before sending
            online, ping_msg = ping_engine_bridge(unreal_host, unreal_port, engine="unreal")
            print(f"[Unreal Bridge Check] {ping_msg}")
            if not online:
                return (payload_str, len(import_payload["assets"]), False)

            # POST the full import payload to the ComfyUnrealBridge plugin listener
            url = f"http://{unreal_host}:{unreal_port}/import_assets"
            try:
                req = urllib.request.Request(
                    url,
                    data=payload_str.encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        success = True
            except Exception as err:
                print(f"[Unreal Bridge HTTP] Connection status: {err}")

        elif communication_mode == "Python Remote Socket":
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                s.connect((unreal_host, unreal_port))
                s.sendall(payload_str.encode("utf-8"))
                s.close()
                success = True
            except Exception as err:
                print(f"[Unreal Python Socket] Connection status: {err}")

        # JSON File Sync mode (always saves a local sync manifest for Unreal python script plugin)
        sync_dir = unreal_project_content_dir or os.path.join(os.path.expanduser("~"), "Documents", "Unreal Projects", "ComfyUI_ImportSync")
        os.makedirs(sync_dir, exist_ok=True)
        sync_filepath = os.path.join(sync_dir, "unreal_import_manifest.json")
        with open(sync_filepath, "w") as f:
            f.write(payload_str)
        
        if communication_mode == "JSON Manifest File Sync":
            success = True

        return (payload_str, len(import_payload["assets"]), success)
