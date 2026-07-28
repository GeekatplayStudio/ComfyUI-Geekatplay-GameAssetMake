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
    Locates the mesh produced by an import. FBX imports name the asset
    directly; glb imports go through Interchange, which may nest or rename,
    so fall back to scanning the folder — preferring an EXACT name match so
    'House_1' never resolves to 'House_10'.
    """
    direct = f"{folder.rstrip('/')}/{name_hint}.{name_hint}"
    if unreal.EditorAssetLibrary.does_asset_exist(direct):
        return unreal.EditorAssetLibrary.load_asset(direct)

    exact, partial = None, None
    hint = name_hint.lower()
    for asset_path in unreal.EditorAssetLibrary.list_assets(folder, recursive=True):
        base = asset_path.split("/")[-1].split(".")[0].lower()
        if base != hint and hint not in base:
            continue
        loaded = unreal.EditorAssetLibrary.load_asset(asset_path)
        if isinstance(loaded, (unreal.StaticMesh, unreal.SkeletalMesh)):
            if base == hint:
                exact = loaded
                break
            partial = partial or loaded
    return exact or partial


def _actor_bounds(actor):
    """(origin, box_extent) world-space; extent is HALF-size in cm."""
    origin, extent = actor.get_actor_bounds(False)
    return origin, extent


def _normalize_and_ground(actor, asset):
    """
    Measures the spawned mesh and makes it the size the plan intended:
    uniform scale so the largest dimension matches target_size_m, then
    lifts/lowers so the mesh bottom sits exactly on ground_z_cm.
    AI-generated meshes arrive in random units — this makes them uniform.
    """
    target = asset.get("target_size_m")
    if asset.get("normalize_to_target") and target:
        _, extent = _actor_bounds(actor)
        actual_max_cm = 2.0 * max(extent.x, extent.y, extent.z)
        target_max_cm = max(float(t) for t in target) * 100.0
        if actual_max_cm > 1.0:
            s = target_max_cm / actual_max_cm
            actor.set_actor_scale_3d(unreal.Vector(s, s, s))
            unreal.log(f"[GameAssetMake] Normalized '{asset.get('name')}': mesh was "
                       f"{actual_max_cm / 100.0:.2f}m, target {target_max_cm / 100.0:.2f}m "
                       f"-> scale {s:.4f}")

    ground_z = asset.get("ground_z_cm")
    if ground_z is not None:
        origin, extent = _actor_bounds(actor)  # re-measure after scaling
        bottom = origin.z - extent.z
        shift = float(ground_z) - bottom
        loc = actor.get_actor_location()
        actor.set_actor_location(unreal.Vector(loc.x, loc.y, loc.z + shift), False, False)


def _verify_terrain_size(actor, asset):
    """glTF unit handling differs between importers — measure the placed
    terrain and rescale if it's off from its declared world size."""
    size_m = asset.get("terrain_world_size_m")
    if not asset.get("verify_world_size") or not size_m:
        return
    _, extent = _actor_bounds(actor)
    actual_m = 2.0 * max(extent.x, extent.y) / 100.0
    if actual_m < 0.01:
        return
    ratio = float(size_m) / actual_m
    if not (0.9 <= ratio <= 1.1):
        s = actor.get_actor_scale_3d()
        actor.set_actor_scale_3d(unreal.Vector(s.x * ratio, s.y * ratio, s.z * ratio))
        unreal.log(f"[GameAssetMake] Terrain measured {actual_m:.0f}m, expected {size_m:.0f}m "
                   f"-> corrected scale x{ratio:.3f}")
    # bottom of the terrain sits at z=0
    origin, extent = _actor_bounds(actor)
    loc = actor.get_actor_location()
    actor.set_actor_location(unreal.Vector(loc.x, loc.y, loc.z - (origin.z - extent.z)), False, False)


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


def _hex_to_linear_color(hex_str):
    h = hex_str.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return unreal.LinearColor(r, g, b, 1.0)


def _setup_sun(editor_actor_subsystem, environment):
    """
    Positions a DirectionalLight from the Scene Director's sun data
    (season/time-of-day aware: azimuth 0=N clockwise, elevation above horizon).
    Reuses an existing DirectionalLight if the level has one.
    """
    try:
        sun = environment.get("sun") or {}
        if "azimuth_deg" not in sun:
            return False
        azimuth = float(sun["azimuth_deg"])
        elevation = float(sun["elevation_deg"])
        intensity = float(sun.get("intensity", 10.0))
        color = _hex_to_linear_color(sun.get("color_hex", "#FFF4E0"))

        light_actor = None
        for actor in editor_actor_subsystem.get_all_level_actors():
            if isinstance(actor, unreal.DirectionalLight):
                light_actor = actor
                break
        if light_actor is None:
            light_actor = editor_actor_subsystem.spawn_actor_from_class(
                unreal.DirectionalLight, unreal.Vector(0, 0, 500), unreal.Rotator(0, 0, 0))

        # Light points along +X at rotator zero; pitch down by elevation,
        # yaw so the light comes FROM the sun azimuth (0=N which is +X here).
        rotation = unreal.Rotator(0.0, -elevation, (azimuth + 180.0) % 360.0)
        light_actor.set_actor_rotation(rotation, teleport_physics=False)
        component = light_actor.light_component
        component.set_intensity(intensity)
        component.set_light_color(color)
        light_actor.set_actor_label(
            f"Sun_{environment.get('season', '')}_{environment.get('time_of_day', '')}")
        unreal.log(f"[GameAssetMake] Sun set: {environment.get('season')} "
                   f"{environment.get('time_of_day')} az={azimuth} el={elevation} "
                   f"intensity={intensity} {sun.get('color_hex')}")
        return True
    except Exception as exc:
        unreal.log_warning(f"[GameAssetMake] Sun setup failed ({exc}).")
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

    # Season/time-of-day sun from the Scene Director (if provided)
    environment = payload_dict.get("environment") or {}
    if environment and auto_place:
        _setup_sun(editor_actor_subsystem, environment)

    failed = []
    for asset in assets:
        # One bad asset must NEVER abort the rest of the batch
        try:
            imported_count += _process_one_asset(
                asset, asset_tools, editor_actor_subsystem,
                target_folder, env_folder, auto_place, auto_collision)
        except Exception as exc:
            failed.append(asset.get("name", "?"))
            unreal.log_error(f"[GameAssetMake] Asset '{asset.get('name')}' failed: {exc} "
                             f"— continuing with the next asset.")

    unreal.log(f"[GameAssetMake] Batch complete: {imported_count}/{len(assets)} imported"
               + (f", failed: {failed}" if failed else "."))
    return True


def _process_one_asset(asset, asset_tools, editor_actor_subsystem,
                       target_folder, env_folder, auto_place, auto_collision):
    """Imports + places a single asset. Returns 1 on success, 0 on skip."""
    source_path = asset.get("source_file")
    if not source_path or not os.path.exists(source_path):
        unreal.log_warning(f"[GameAssetMake Skip] Source file missing: {source_path}")
        return 0

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
        if category == "skydome" and auto_place:
            tex = unreal.EditorAssetLibrary.load_asset(f"{env_folder}/{asset_name}.{asset_name}")
            if isinstance(tex, unreal.Texture):
                _setup_skydome(editor_actor_subsystem, tex, env_folder, asset_name)
        elif category == "terrain":
            unreal.log(f"[GameAssetMake] Heightmap imported. For a native Landscape use "
                       f"Mode > Landscape > Import from File with: {source_path}")
        return 1

    # ---- mesh assets ----------------------------------------------------
    if is_gltf:
        # glTF goes through Interchange — passing FbxImportUI would break it
        dest = env_folder if category in ("terrain_mesh",) else target_folder
        _import_file(asset_tools, source_path, dest, asset_name)
    else:
        options = unreal.FbxImportUI()
        options.import_mesh = True
        options.import_textures = True
        options.import_materials = True
        # NOTE: no blanket import_uniform_scale — AI meshes arrive in random
        # units; size is normalized after spawn against target_size_m instead.
        if rig_type != "none":
            options.mesh_type_to_import = unreal.FBXImportType.FBXIT_SKELETAL_MESH
        else:
            options.mesh_type_to_import = unreal.FBXImportType.FBXIT_STATIC_MESH
            if auto_collision:
                options.static_mesh_import_data.set_editor_property("auto_generate_collision", True)
        dest = target_folder
        _import_file(asset_tools, source_path, dest, asset_name, options)

    unreal.log(f"[GameAssetMake] Imported mesh '{asset_name}' -> {dest}")

    if auto_place:
        mesh = _find_static_mesh(dest, asset_name)
        if mesh is None:
            unreal.log_warning(f"[GameAssetMake] Imported '{asset_name}' but found no "
                               f"mesh asset to place (check {dest}).")
            return 1

        if category == "terrain_mesh":
            terrain_asset = dict(asset)
            terrain_asset["location"] = [0.0, 0.0, 0.0]
            terrain_asset["scale"] = [1.0, 1.0, 1.0]
            terrain_asset["rotation_yaw"] = 0.0
            actor = _spawn_placed(editor_actor_subsystem, mesh, terrain_asset, 100.0)
            if actor:
                _verify_terrain_size(actor, asset)
        else:
            actor = _spawn_placed(editor_actor_subsystem, mesh, asset, 100.0)
            if actor:
                _normalize_and_ground(actor, asset)
    return 1
