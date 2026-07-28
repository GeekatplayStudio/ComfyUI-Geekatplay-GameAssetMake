# =============================================================
# Geekatplay GameAssetMake — ComfyUI node registry
# (c) Geekatplay Studio / Vladimir Chopine — https://www.geekatplay.com
# =============================================================
from .game_planner_node import GameAssetPlannerNode
from .gallery_approval_node import GalleryApprovalNode
from .unified_3d_node import Unified3DGeneratorNode
from .unreal_bridge_node import UnrealEngineBridgeNode
from .unity_bridge_node import UnityEngineBridgeNode

NODE_CLASS_MAPPINGS = {
    "GameAssetPlannerNode": GameAssetPlannerNode,
    "GalleryApprovalNode": GalleryApprovalNode,
    "Unified3DGeneratorNode": Unified3DGeneratorNode,
    "UnrealEngineBridgeNode": UnrealEngineBridgeNode,
    "UnityEngineBridgeNode": UnityEngineBridgeNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "GameAssetPlannerNode": "🎮 GameAssetMake Asset Planner (AI Breakdown)",
    "GalleryApprovalNode": "🖼️ GameAssetMake Asset Gallery & Approval UI",
    "Unified3DGeneratorNode": "🧊 GameAssetMake 3D Generator (Tripo / Meshy)",
    "UnrealEngineBridgeNode": "⚡ GameAssetMake Unreal Engine Bridge",
    "UnityEngineBridgeNode": "📦 GameAssetMake Unity Engine Bridge"
}
