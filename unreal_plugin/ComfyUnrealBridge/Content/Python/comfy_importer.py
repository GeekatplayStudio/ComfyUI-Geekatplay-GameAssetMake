# =============================================================
# Geekatplay GameAssetMake — Unreal Engine asset importer
# (c) Geekatplay Studio / Vladimir Chopine
# Runs on the game thread (invoked from comfy_server's tick handler).
# =============================================================
import os

try:
    import unreal
except ImportError:
    unreal = None


def _find_static_mesh(folder, name_hint):
    """
    Locates the StaticMesh produced by an import. FBX imports name the asset
    directly; glb imports go through Interchange, which may nest or rename,
    so fall back to scanning the folder for a StaticMesh matching the hint.
    """
    direct = f"{folder.rstrip('/')}/{name_hint}.{name_hint}"
    if unreal.EditorAssetLibrary.does_asset_exist(direct):
        return unreal.EditorAssetLibrary.load_asset(direct)

    for asset_path in unreal.EditorAssetLibrary.list_assets(folder, recursive=True):
        if name_hint.lower() not in asset_path.lower():
            continue
        loaded = unreal.EditorAssetLibrary.load_asset(asset_path)
        if isinstance(loaded, (unreal.StaticMesh, unreal.SkeletalMesh)):
            return loaded
    return None


def _import_file(asset_tools, source_path, dest_folder, dest_name, options=None):
    task = unreal.AssetImportTask()
    task.filename = source_path
    task.destination_path = dest_folder
    task.destination_name = dest_name
    task.replace_existing = True
    task.automated = True
    if options is not None:
        task.options = options
    asset_tools.import_asset_tasks([task])
    return task


def _spawn_placed(editor_actor_subsystem, obj, asset, scale_factor, unit_scale=True):
    pos = asset.get("location", [0.0, 0.0, 0.0])
    sc = asset.get("scale", [1.0, 1.0, 1.0])
    yaw = float(asset.get("rotation_yaw", 0.0))

    k = scale_factor / 100.0 if unit_scale else 1.0
    location = unreal.Vector(pos[0] * k, pos[1] * k, pos[2] * k)
    rotation = unreal.Rotator(0.0, 0.0, yaw)  # roll, pitch, yaw

    actor = editor_actor_subsystem.spawn_actor_from_object(obj, location, rotation)
    if actor:
        actor.set_actor_scale_3d(unreal.Vector(sc[0], sc[1], sc[2]))
        actor.set_actor_label(asset.get("name", "GeneratedAsset"))
        unreal.log(f"[GameAssetMake] Placed '{asset.get('name')}' at {location} yaw={yaw}")
    return actor


def _setup_skydome(editor_actor_subsystem, texture_asset, env_folder, name):
    """
    Builds an unlit emissive sky sphere from the imported HDRI texture:
    material with a TextureSample -> emissive, two-sided, applied to a huge
    engine sphere. Guarded — a failure logs but never aborts the batch.
    """
    try:
        mat_name = f"M_{name}_Sky"
        mat_path = f"{env_folder.rstrip('/')}/{mat_name}"
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()

        if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
            material = unreal.EditorAssetLibrary.load_asset(mat_path)
        else:
            material = asset_tools.create_asset(mat_name, env_folder.rstrip('/'),
                                                unreal.Material, unreal.MaterialFactoryNew())
            mel = unreal.MaterialEditingLibrary
            material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
            material.set_editor_property("two_sided", True)
            sample = mel.create_material_expression(
                material, unreal.MaterialExpressionTextureSample, -400, 0)
            sample.set_editor_property("texture", texture_asset)
            mel.connect_material_property(sample, "RGB", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
            mel.recompile_material(material)
            unreal.EditorAssetLibrary.save_asset(mat_path)

        sphere = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere.Sphere")
        actor = editor_actor_subsystem.spawn_actor_from_object(
            sphere, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
        if actor:
            actor.set_actor_label(f"{name}_SkyDome")
            # Engine sphere is 100uu across; scale to a 400m-radius dome,
            # negative Z flips normals inward so the inside is visible.
            actor.set_actor_scale_3d(unreal.Vector(800.0, 800.0, -800.0))
            mesh_component = actor.static_mesh_component
            mesh_component.set_material(0, material)
            mesh_component.set_editor_property("cast_shadow", False)
            unreal.log(f"[GameAssetMake] Skydome '{name}' created and applied.")
            return True
    except Exception as exc:
        unreal.log_warning(f"[GameAssetMake] Skydome auto-setup failed ({exc}). "
                           f"The HDRI texture is imported — build the sky material manually.")
    return False


def import_and_place_manifest_payload(payload_dict):
    """
    Imports every asset in the payload into the Content Browser and places it
    in the active level: meshes (FBX/GLB, static or skeletal), terrain meshes,
    skydomes (with automatic sky-sphere setup), heightmaps, and textures.
    """
    if unreal is None:
        print("[GameAssetMake] Error: must run inside the Unreal Editor.")
        return False

    target_folder = payload_dict.get("target_content_folder", "/Game/Assets/AI_Generated/")
    env_folder = f"{target_folder.rstrip('/')}/Environment"
    scale_factor = float(payload_dict.get("unit_scale_factor", 100.0))
    auto_place = payload_dict.get("auto_place_in_level", True)
    auto_collision = payload_dict.get("auto_generate_collisions", True)
    assets = payload_dict.get("assets", [])

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    editor_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    unreal.log(f"[GameAssetMake] Batch import: {len(assets)} asset(s) -> '{target_folder}'")
    imported_count = 0

    for asset in assets:
        source_path = asset.get("source_file")
        if not source_path or not os.path.exists(source_path):
            unreal.log_warning(f"[GameAssetMake Skip] Source file missing: {source_path}")
            continue

        asset_name = asset.get("name", "GeneratedAsset").replace(" ", "_")
        rig_type = asset.get("rig_type", "none")
        category = asset.get("category", "")
        ext = source_path.rsplit(".", 1)[-1].lower()
        is_image = ext in ("png", "jpg", "jpeg", "exr", "hdr", "tga")
        is_gltf = ext in ("glb", "gltf")

        # ---- image-based environment assets --------------------------------
        if is_image:
            _import_file(asset_tools, source_path, env_folder, asset_name)
            unreal.log(f"[GameAssetMake] Imported {category or 'texture'} '{asset_name}' -> {env_folder}")
            imported_count += 1

            if category == "skydome" and auto_place:
                tex = _find_static_mesh(env_folder, asset_name) or \
                    unreal.EditorAssetLibrary.load_asset(f"{env_folder}/{asset_name}.{asset_name}")
                if isinstance(tex, unreal.Texture):
                    _setup_skydome(editor_actor_subsystem, tex, env_folder, asset_name)
            elif category == "terrain":
                unreal.log(f"[GameAssetMake] Heightmap imported. For a native Landscape use "
                           f"Mode > Landscape > Import from File with: {source_path}")
            continue

        # ---- mesh assets ---------------------------------------------------
        if is_gltf:
            # glTF goes through Interchange — passing FbxImportUI would break it
            dest = env_folder if category in ("terrain_mesh",) else target_folder
            _import_file(asset_tools, source_path, dest, asset_name)
        else:
            options = unreal.FbxImportUI()
            options.import_mesh = True
            options.import_textures = True
            options.import_materials = True
            if rig_type != "none":
                options.mesh_type_to_import = unreal.FBXImportType.FBXIT_SKELETAL_MESH
                options.skeletal_mesh_import_data.set_editor_property("import_uniform_scale", scale_factor)
            else:
                options.mesh_type_to_import = unreal.FBXImportType.FBXIT_STATIC_MESH
                options.static_mesh_import_data.set_editor_property("import_uniform_scale", scale_factor)
                if auto_collision:
                    options.static_mesh_import_data.set_editor_property("auto_generate_collision", True)
            dest = target_folder
            _import_file(asset_tools, source_path, dest, asset_name, options)

        unreal.log(f"[GameAssetMake] Imported mesh '{asset_name}' -> {dest}")
        imported_count += 1

        if auto_place:
            mesh = _find_static_mesh(dest, asset_name)
            if mesh is None:
                unreal.log_warning(f"[GameAssetMake] Imported '{asset_name}' but found no "
                                   f"mesh asset to place (check {dest}).")
                continue
            if category == "terrain_mesh":
                # Terrain glb is authored in meters at world size; Interchange
                # imports meters->cm (x100) already, so place at origin, scale 1.
                terrain_asset = dict(asset)
                terrain_asset["location"] = [0.0, 0.0, 0.0]
                terrain_asset["scale"] = [1.0, 1.0, 1.0]
                terrain_asset["rotation_yaw"] = 0.0
                _spawn_placed(editor_actor_subsystem, mesh, terrain_asset, scale_factor)
            else:
                _spawn_placed(editor_actor_subsystem, mesh, asset, scale_factor)

    unreal.log(f"[GameAssetMake] Batch complete: {imported_count}/{len(assets)} imported.")
    return True
