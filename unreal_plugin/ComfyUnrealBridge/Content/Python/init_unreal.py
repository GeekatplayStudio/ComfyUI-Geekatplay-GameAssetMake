# ==============================================================================
# Geekatplay GameAssetMake — Unreal Engine Bridge initialization
# (c) Geekatplay Studio / Vladimir Chopine
# Automatically executed on Unreal Engine Editor startup
# (Content/Python/init_unreal.py convention of the Python Script Plugin).
# ==============================================================================

import unreal
from comfy_server import start_bridge_server


def init():
    unreal.log("[GameAssetMake Bridge] Initializing plugin background server...")
    start_bridge_server()
    unreal.log("[GameAssetMake Bridge] Plugin initialization complete!")


init()
