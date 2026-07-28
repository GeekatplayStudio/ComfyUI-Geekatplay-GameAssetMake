# =============================================================
# Geekatplay GameAssetMake — Engine Connection Check node
# (c) Geekatplay Studio / Vladimir Chopine
# =============================================================
import os
import urllib.request

# A real mesh is never this small; mock/fallback stubs are ~80-byte text files.
MIN_REAL_MESH_BYTES = 2048


def filter_deliverable_assets(manifest, label="Bridge"):
    """
    Drops placeholder assets so mock stubs are never imported into a game engine.

    dry_run_mock writes ~80-byte text files named .fbx; importing those produces
    nothing (or garbage) in Unreal/Unity, which looks like "no models arrived".
    Returns (deliverable, skipped) where skipped items carry a 'reason'.
    """
    deliverable, skipped = [], []
    for item in manifest:
        path = item.get("model_path") or item.get("source_file") or ""
        status = str(item.get("generation_status", ""))
        name = item.get("name", item.get("id", "?"))

        if status.startswith("MOCK"):
            skipped.append({"name": name, "reason": "mock placeholder (turn OFF dry_run_mock)"})
            continue
        if not path or not os.path.exists(path):
            skipped.append({"name": name, "reason": f"file missing: {path or '(none)'}"})
            continue
        if os.path.getsize(path) < MIN_REAL_MESH_BYTES:
            skipped.append({"name": name,
                            "reason": f"placeholder stub, only {os.path.getsize(path)} bytes"})
            continue
        deliverable.append(item)

    if skipped:
        print(f"[{label}] Skipping {len(skipped)} asset(s) that are not real meshes:")
        for s in skipped[:10]:
            print(f"    - {s['name']}: {s['reason']}")
        if not deliverable:
            print(f"[{label}] NOTHING TO SEND — every asset was a placeholder. "
                  f"Turn OFF 'dry_run_mock' on the 3D Generator (or use the local "
                  f"Hunyuan3D workflow) to produce real meshes.")
    return deliverable, skipped


def ping_engine_bridge(host, port, engine="unreal", timeout=3):
    """
    Returns (online: bool, message: str). Unreal's bridge answers GET on any path;
    Unity's HttpListener only answers under its /import_assets/ prefix.
    """
    path = "/import_assets/" if engine == "unity" else "/"
    url = f"http://{host}:{port}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return True, f"{engine.capitalize()} bridge ONLINE at {host}:{port}"
            return False, f"{engine.capitalize()} bridge answered HTTP {resp.status} at {host}:{port}"
    except Exception as e:
        hint = (
            "Is Unreal Engine running with the ComfyUnrealBridge plugin enabled?"
            if engine == "unreal"
            else "Is the Unity Editor open with ComfyUnityImporter.cs installed in Assets/Editor/?"
        )
        return False, f"{engine.capitalize()} bridge OFFLINE at {host}:{port} ({e}). {hint}"


class EngineConnectionCheckNode:
    """
    Verifies that the Unreal or Unity editor bridge is installed, running,
    and reachable before you spend API credits on 3D generation.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "engine": (["unreal", "unity"], {"default": "unreal"}),
                "host": ("STRING", {"default": "127.0.0.1"}),
                "port": ("INT", {"default": 30010, "min": 1024, "max": 65535}),
            }
        }

    RETURN_TYPES = ("BOOLEAN", "STRING")
    RETURN_NAMES = ("engine_online", "status_message")
    FUNCTION = "check_connection"
    CATEGORY = "Geekatplay GameAssetMake/Engine-Bridge"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, engine, host, port):
        # Always re-run: connection state can change between queue runs
        return float("NaN")

    def check_connection(self, engine="unreal", host="127.0.0.1", port=30010):
        online, message = ping_engine_bridge(host, port, engine=engine)
        print(f"[GameAssetMake Connection Check] {message}")
        return {
            "ui": {"connection_status": [[{"online": online, "message": message}]]},
            "result": (online, message),
        }
