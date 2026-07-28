import os
import json

try:
    import unreal
except ImportError:
    unreal = None

def import_and_place_manifest_payload(payload_dict):
    """
    Imports assets listed in payload_dict into Unreal Engine Content Browser and places them in the active level.
    """
    if unreal is None:
        print("[ComfyUI Bridge] Error: Must be executed inside Unreal Engine Editor.")
        return False

    target_folder = payload_dict.get("target_content_folder", "/Game/Assets/AI_Generated/")
    scale_factor = float(payload_dict.get("unit_scale_factor", 100.0))
    auto_place = payload_dict.get("auto_place_in_level", True)
    auto_collision = payload_dict.get("auto_generate_collisions", True)
    assets = payload_dict.get("assets", [])

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    editor_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    unreal.log(f"[ComfyUI Bridge] Processing batch import of {len(assets)} assets to '{target_folder}'...")

    imported_count = 0

    for asset in assets:
        source_path = asset.get("source_file")
        if not source_path or not os.path.exists(source_path):
            unreal.log_warning(f"[ComfyUI Bridge Skip] Source file missing: {source_path}")
            continue

        asset_name = asset.get("name", "GeneratedAsset").replace(" ", "_")
        rig_type = asset.get("rig_type", "none")
        is_skeletal = (rig_type != "none")
        category = asset.get("category", "")
        ext = source_path.rsplit(".", 1)[-1].lower()

        # Environment assets (terrain heightmaps, skydome HDRIs, textures)
        # import as texture assets — no FBX options, no actor spawn
        if category in ("terrain", "skydome", "texture") or ext in ("png", "jpg", "jpeg", "exr", "hdr", "tga"):
            env_task = unreal.AssetImportTask()
            env_task.filename = source_path
            env_task.destination_path = f"{target_folder.rstrip('/')}/Environment"
            env_task.destination_name = asset_name
            env_task.replace_existing = True
            env_task.automated = True
            asset_tools.import_asset_tasks([env_task])
            unreal.log(f"[ComfyUI Bridge] Imported {category or 'texture'} '{asset_name}' into {env_task.destination_path}")
            if category == "terrain":
                unreal.log(f"[ComfyUI Bridge] Heightmap ready — create a Landscape via "
                           f"Mode > Landscape > Import from File using: {source_path}")
            imported_count += 1
            continue

        # Configure Unreal Asset Import Task
        task = unreal.AssetImportTask()
        task.filename = source_path
        task.destination_path = target_folder
        task.destination_name = asset_name
        task.replace_existing = True
        task.automated = True

        # Configure FBX / GLB options
        options = unreal.FbxImportUI()
        options.import_mesh = True
        options.import_textures = True
        options.import_materials = True

        if is_skeletal:
            options.mesh_type_to_import = unreal.FBXImportType.FBXIT_SKELETAL_MESH
            options.skeletal_mesh_import_data.set_editor_property("import_uniform_scale", scale_factor)
        else:
            options.mesh_type_to_import = unreal.FBXImportType.FBXIT_STATIC_MESH
            options.static_mesh_import_data.set_editor_property("import_uniform_scale", scale_factor)
            if auto_collision:
                options.static_mesh_import_data.set_editor_property("auto_generate_collision", True)

        task.options = options

        # Execute Asset Import Task
        asset_tools.import_asset_tasks([task])
        unreal.log(f"[ComfyUI Bridge] Successfully imported asset '{asset_name}' into {target_folder}")
        imported_count += 1

        # Spawn Actor into Level if auto_place is requested
        if auto_place:
            asset_path = f"{target_folder.rstrip('/')}/{asset_name}.{asset_name}"
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
                    unreal.log(f"[ComfyUI Bridge Placed] Spawned actor in level at {location}")

    return True
