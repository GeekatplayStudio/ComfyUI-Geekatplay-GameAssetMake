# ==============================================================================
# ComfyUI-Unreal Automated Asset Importer & World Placement Script
# Run inside Unreal Engine Editor via Python Console, Remote Execution, or Startup Script.
# ==============================================================================

import os
import json

try:
    import unreal
except ImportError:
    unreal = None

def import_and_place_assets(manifest_file_path=None):
    if unreal is None:
        print("[ComfyUI-Unreal Error] Script must be run inside Unreal Engine Python Editor.")
        return

    if manifest_file_path is None:
        default_dir = os.path.join(os.path.expanduser("~"), "Documents", "Unreal Projects", "ComfyUI_ImportSync")
        manifest_file_path = os.path.join(default_dir, "unreal_import_manifest.json")

    if not os.path.exists(manifest_file_path):
        print(f"[ComfyUI-Unreal Warning] Manifest file not found: {manifest_file_path}")
        return

    with open(manifest_file_path, "r") as f:
        data = json.load(f)

    target_folder = data.get("target_content_folder", "/Game/Assets/AI_Generated/")
    scale_factor = float(data.get("unit_scale_factor", 100.0))
    auto_place = data.get("auto_place_in_level", True)
    auto_collision = data.get("auto_generate_collisions", True)
    assets = data.get("assets", [])

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    editor_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    print(f"[ComfyUI-Unreal] Processing batch import of {len(assets)} assets to '{target_folder}'...")

    for asset in assets:
        source_path = asset.get("source_file")
        if not source_path or not os.path.exists(source_path):
            print(f"[ComfyUI-Unreal Skip] Source file missing: {source_path}")
            continue

        asset_name = asset.get("name", "GeneratedAsset").replace(" ", "_")
        rig_type = asset.get("rig_type", "none")
        is_skeletal = (rig_type != "none")

        # Setup Import Task
        task = unreal.AssetImportTask()
        task.filename = source_path
        task.destination_path = target_folder
        task.destination_name = asset_name
        task.replace_existing = True
        task.automated = True

        # Setup FBX Import UI
        options = unreal.FbxImportUI()
        options.import_mesh = True
        options.import_textures = True
        options.import_materials = True

        if is_skeletal:
            options.mesh_type_to_import = unreal.FBXImportType.FBXIT_SKELETAL_MESH
            options.skeletal_mesh_import_data.set_editor_property("import_translation", unreal.Vector(0, 0, 0))
            options.skeletal_mesh_import_data.set_editor_property("import_uniform_scale", scale_factor)
        else:
            options.mesh_type_to_import = unreal.FBXImportType.FBXIT_STATIC_MESH
            options.static_mesh_import_data.set_editor_property("import_uniform_scale", scale_factor)
            if auto_collision:
                options.static_mesh_import_data.set_editor_property("auto_generate_collision", True)

        task.options = options

        # Execute Import
        asset_tools.import_asset_tasks([task])
        print(f"[ComfyUI-Unreal Imported] {asset_name} -> {target_folder}/{asset_name}")

        # Auto World Level Placement
        if auto_place:
            asset_path = f"{target_folder}/{asset_name}.{asset_name}"
            loaded_mesh = unreal.EditorAssetLibrary.load_asset(asset_path)
            
            if loaded_mesh:
                pos = asset.get("location", [0.0, 0.0, 0.0])
                sc = asset.get("scale", [1.0, 1.0, 1.0])
                
                location = unreal.Vector(pos[0] * scale_factor / 100.0, pos[1] * scale_factor / 100.0, pos[2] * scale_factor / 100.0)
                rotation = unreal.Rotator(0, 0, 0)
                scale = unreal.Vector(sc[0], sc[1], sc[2])

                spawned_actor = editor_actor_subsystem.spawn_actor_from_object(loaded_mesh, location, rotation)
                if spawned_actor:
                    spawned_actor.set_actor_scale_3d(scale)
                    print(f"[ComfyUI-Unreal Placed] Spawned actor in level at {location}")

if __name__ == "__main__":
    import_and_place_assets()
