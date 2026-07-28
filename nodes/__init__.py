# =============================================================
# Geekatplay GameAssetMake — ComfyUI node registry
# (c) Geekatplay Studio / Vladimir Chopine — https://www.geekatplay.com
# =============================================================
from .game_planner_node import GameAssetPlannerNode
from .gallery_approval_node import GalleryApprovalNode
from .unified_3d_node import Unified3DGeneratorNode
from .unreal_bridge_node import UnrealEngineBridgeNode
from .unity_bridge_node import UnityEngineBridgeNode
from .engine_check_node import EngineConnectionCheckNode
from .asset_verify_node import SingleObjectGuardrailNode
from .batch_concept_node import BatchConceptGeneratorNode
from .local_hunyuan3d_node import LocalHunyuan3DGeneratorNode
from .terrain_mesh_node import TerrainMeshBuilderNode
from .scene_director_node import SceneDirectorNode
from .layout_map_node import LayoutMapPreviewNode
from .placement_manager_node import AssetPlacementManagerNode
from .sun_environment_node import SunEnvironmentNode
from .environment_export_node import EnvironmentAssetExportNode

NODE_CLASS_MAPPINGS = {
    "GameAssetPlannerNode": GameAssetPlannerNode,
    "GalleryApprovalNode": GalleryApprovalNode,
    "Unified3DGeneratorNode": Unified3DGeneratorNode,
    "UnrealEngineBridgeNode": UnrealEngineBridgeNode,
    "UnityEngineBridgeNode": UnityEngineBridgeNode,
    "EngineConnectionCheckNode": EngineConnectionCheckNode,
    "SingleObjectGuardrailNode": SingleObjectGuardrailNode,
    "BatchConceptGeneratorNode": BatchConceptGeneratorNode,
    "LocalHunyuan3DGeneratorNode": LocalHunyuan3DGeneratorNode,
    "TerrainMeshBuilderNode": TerrainMeshBuilderNode,
    "SceneDirectorNode": SceneDirectorNode,
    "LayoutMapPreviewNode": LayoutMapPreviewNode,
    "AssetPlacementManagerNode": AssetPlacementManagerNode,
    "SunEnvironmentNode": SunEnvironmentNode,
    "EnvironmentAssetExportNode": EnvironmentAssetExportNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "GameAssetPlannerNode": "🎮 GameAssetMake Asset Planner (AI Breakdown)",
    "GalleryApprovalNode": "🖼️ GameAssetMake Asset Gallery & Approval UI",
    "Unified3DGeneratorNode": "🧊 GameAssetMake 3D Generator (Tripo / Meshy / Hitem3D)",
    "UnrealEngineBridgeNode": "⚡ GameAssetMake Unreal Engine Bridge",
    "UnityEngineBridgeNode": "📦 GameAssetMake Unity Engine Bridge",
    "EngineConnectionCheckNode": "🔌 GameAssetMake Engine Connection Check",
    "SingleObjectGuardrailNode": "🛡️ GameAssetMake Single-Object Guardrail (VLM)",
    "BatchConceptGeneratorNode": "🔁 GameAssetMake Batch Concept Generator (one image per asset)",
    "LocalHunyuan3DGeneratorNode": "🖥️ GameAssetMake Local 3D Generator (Hunyuan3D 2.1 — no API)",
    "TerrainMeshBuilderNode": "⛰️ GameAssetMake Terrain Mesh Builder (heightmap → mesh)",
    "SceneDirectorNode": "🎬 GameAssetMake Scene Director (prompt → full scene plan)",
    "LayoutMapPreviewNode": "🗺️ GameAssetMake Layout Map (top-down placement preview)",
    "AssetPlacementManagerNode": "📐 GameAssetMake Placement Manager (order, scale, terrain snap)",
    "SunEnvironmentNode": "☀️ GameAssetMake Sun / Environment (season + time → light)",
    "EnvironmentAssetExportNode": "🌍 GameAssetMake Environment Export (Terrain/Sky)"
}
