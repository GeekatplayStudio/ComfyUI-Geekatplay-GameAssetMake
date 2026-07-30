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

# Bump when the importer changes; printed on every payload so the log proves
# which version actually ran (Unreal caches Python for the editor session).
IMPORTER_VERSION = "2026.07.28b-unique-sky"


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


def _find_texture(folder, name_hint):
    """
    Finds the Texture produced by an image import. The asset may be renamed or
    suffixed by the importer, so try the direct path first, then scan the folder.
    """
    direct = f"{folder.rstrip('/')}/{name_hint}.{name_hint}"
    if unreal.EditorAssetLibrary.does_asset_exist(direct):
        loaded = unreal.EditorAssetLibrary.load_asset(direct)
        if isinstance(loaded, unreal.Texture):
            return loaded

    hint = name_hint.lower()
    fallback = None
    for asset_path in unreal.EditorAssetLibrary.list_assets(folder, recursive=True):
        base = asset_path.split("/")[-1].split(".")[0].lower()
        loaded = unreal.EditorAssetLibrary.load_asset(asset_path)
        if not isinstance(loaded, unreal.Texture):
            continue
        if base == hint:
            return loaded
        if hint in base or base in hint:
            fallback = fallback or loaded
    return fallback


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


def _reimport_clean(dest_folder, dest_name):
    """
    Deletes any existing asset at this path before re-importing.

    Re-importing over an asset that a material already references can leave the
    old content in place (or spill the new content into a '_1' duplicate), which
    is why a regenerated skydome/terrain map appeared to update once and then
    never again.
    """
    path = f"{dest_folder.rstrip('/')}/{dest_name}"
    try:
        if unreal.EditorAssetLibrary.does_asset_exist(path):
            ok = unreal.EditorAssetLibrary.delete_asset(path)
            if ok:
                unreal.log(f"[GameAssetMake] Removed stale asset {path} before re-import.")
            else:
                unreal.log_warning(f"[GameAssetMake] Stale asset {path} is still referenced "
                                   f"and could NOT be deleted; a re-import may not refresh it.")
            return bool(ok)
    except Exception as exc:
        unreal.log_warning(f"[GameAssetMake] Could not remove stale asset {path}: {exc}")
        return False
    return True


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
    # Pitch/roll come from the terrain normal under the asset, so props and trees
    # lie along the slope. Buildings are sent with 0/0 and stay upright.
    pitch = float(asset.get("rotation_pitch", 0.0))
    roll = float(asset.get("rotation_roll", 0.0))

    k = scale_factor / 100.0 if unit_scale else 1.0
    location = unreal.Vector(pos[0] * k, pos[1] * k, pos[2] * k)
    rotation = unreal.Rotator(roll, pitch, yaw)  # roll, pitch, yaw

    actor = editor_actor_subsystem.spawn_actor_from_object(obj, location, rotation)
    if actor:
        actor.set_actor_scale_3d(unreal.Vector(sc[0], sc[1], sc[2]))
        actor.set_actor_label(asset.get("name", "GeneratedAsset"))
        unreal.log(f"[GameAssetMake] Placed '{asset.get('name')}' at {location} "
                   f"yaw={yaw} pitch={pitch} roll={roll}")
    return actor


def _setup_sky_light(editor_actor_subsystem, name):
    """
    Adds/updates a SkyLight that captures the sky sphere we just built, so the
    HDRI actually LIGHTS the scene (image-based ambient), not merely look right.
    """
    try:
        sky_light = None
        for actor in editor_actor_subsystem.get_all_level_actors():
            if isinstance(actor, unreal.SkyLight):
                sky_light = actor
                break
        if sky_light is None:
            sky_light = editor_actor_subsystem.spawn_actor_from_class(
                unreal.SkyLight, unreal.Vector(0, 0, 300), unreal.Rotator(0, 0, 0))
            unreal.log("[GameAssetMake] Spawned a SkyLight for image-based lighting.")

        component = sky_light.light_component
        component.set_editor_property("source_type",
                                      unreal.SkyLightSourceType.SLS_CAPTURED_SCENE)
        component.set_editor_property("real_time_capture", True)
        component.set_editor_property("intensity", 1.0)
        sky_light.set_actor_label(f"{name}_SkyLight")
        try:
            component.recapture_sky()
        except Exception:
            pass  # real-time capture handles it on newer engine versions
        unreal.log("[GameAssetMake] SkyLight configured to capture the skydome "
                   "(the HDRI now lights the scene).")
        return True
    except Exception as exc:
        unreal.log_warning(f"[GameAssetMake] SkyLight setup failed ({exc}).")
        return False


def _setup_skydome(editor_actor_subsystem, texture_asset, env_folder, name):
    """
    Builds an unlit emissive sky sphere from the imported HDRI plus a SkyLight
    that captures it. Every step logs, so a failure states exactly where it stopped.
    """
    mat_name = f"M_{name}_Sky"
    mat_path = f"{env_folder.rstrip('/')}/{mat_name}"
    material = None

    # --- 1. sky material ---
    try:
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
            material = unreal.EditorAssetLibrary.load_asset(mat_path)
            unreal.log(f"[GameAssetMake] Reusing sky material {mat_path}")
        if material is None:
            material = asset_tools.create_asset(mat_name, env_folder.rstrip('/'),
                                                unreal.Material, unreal.MaterialFactoryNew())
        if material is None:
            unreal.log_error(f"[GameAssetMake] Could not create sky material at {mat_path}")
            return False

        mel = unreal.MaterialEditingLibrary
        material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
        material.set_editor_property("two_sided", True)
        # Rebuild the texture node each run so a re-import refreshes the sky.
        try:
            mel.delete_all_material_expressions(material)
        except Exception:
            pass
        sample = mel.create_material_expression(
            material, unreal.MaterialExpressionTextureSample, -400, 0)
        sample.set_editor_property("texture", texture_asset)
        mel.connect_material_property(sample, "RGB", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        mel.recompile_material(material)
        unreal.EditorAssetLibrary.save_asset(mat_path)
        unreal.log(f"[GameAssetMake] Sky material built: {mat_path}")
    except Exception as exc:
        unreal.log_error(f"[GameAssetMake] Sky material step failed: {exc}")
        return False

    # --- 2. sky sphere actor ---
    try:
        sphere = None
        for candidate in ("/Engine/BasicShapes/Sphere.Sphere",
                          "/Engine/EngineMeshes/Sphere.Sphere"):
            if unreal.EditorAssetLibrary.does_asset_exist(candidate):
                sphere = unreal.EditorAssetLibrary.load_asset(candidate)
                if sphere:
                    break
        if sphere is None:
            unreal.log_error("[GameAssetMake] Engine sphere mesh not found "
                             "(/Engine/BasicShapes/Sphere). Enable 'Show Engine Content' "
                             "in the Content Browser, or apply the sky material to your "
                             "own large sphere manually.")
            return False

        # Replace a previous dome so repeated runs do not stack spheres.
        label = f"{name}_SkyDome"
        for actor in editor_actor_subsystem.get_all_level_actors():
            try:
                if actor.get_actor_label() == label:
                    editor_actor_subsystem.destroy_actor(actor)
            except Exception:
                pass

        actor = editor_actor_subsystem.spawn_actor_from_object(
            sphere, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
        if actor is None:
            unreal.log_error("[GameAssetMake] Failed to spawn the sky sphere actor.")
            return False

        actor.set_actor_label(label)
        # Engine sphere is 100uu across; negative scale flips the normals inward
        # so the textured surface faces the camera from inside the dome.
        actor.set_actor_scale_3d(unreal.Vector(800.0, 800.0, -800.0))
        mesh_component = actor.static_mesh_component
        mesh_component.set_material(0, material)
        mesh_component.set_editor_property("cast_shadow", False)
        try:
            mesh_component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        except Exception:
            pass
        unreal.log(f"[GameAssetMake] Skydome '{label}' created "
                   f"(800x sphere, unlit emissive, inward-facing).")
    except Exception as exc:
        unreal.log_error(f"[GameAssetMake] Sky sphere step failed: {exc}")
        return False

    # --- 3. image-based lighting ---
    _setup_sky_light(editor_actor_subsystem, name)
    return True


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


def _import_map(asset_tools, path, env_folder, name, kind):
    """
    Imports a PBR map and applies the correct texture settings. Getting these
    wrong is the classic PBR mistake: normal/roughness/AO are DATA, not colour,
    so they must not be sRGB-decoded, and normals need normal-map compression.
    """
    if not path or not os.path.exists(path):
        return None
    _reimport_clean(env_folder, name)
    _import_file(asset_tools, path, env_folder, name)
    tex = _find_texture(env_folder, name)
    if tex is None:
        unreal.log_warning(f"[GameAssetMake] Could not locate imported {kind} map '{name}'.")
        return None
    try:
        if kind == "normal":
            tex.set_editor_property("srgb", False)
            tex.set_editor_property("compression_settings",
                                    unreal.TextureCompressionSettings.TC_NORMALMAP)
            tex.set_editor_property("flip_green_channel", False)
        elif kind in ("roughness", "ao"):
            tex.set_editor_property("srgb", False)
            tex.set_editor_property("compression_settings",
                                    unreal.TextureCompressionSettings.TC_MASKS)
        unreal.EditorAssetLibrary.save_loaded_asset(tex)
    except Exception as exc:
        unreal.log_warning(f"[GameAssetMake] Could not set {kind} texture settings: {exc}")
    return tex


def _apply_terrain_material(asset_tools, editor_actor_subsystem, actor, asset, env_folder):
    """
    Builds a real PBR material for the terrain from the maps generated off the
    heightfield (base colour, normal, roughness, AO) and applies it to the actor
    - the same explicit approach used for the skydome, because glTF imports do
    not reliably surface embedded textures.
    """
    texture_path = asset.get("texture_path")
    if not texture_path:
        unreal.log("[GameAssetMake] Terrain has no separate colour map; relying on "
                   "whatever the glTF import produced.")
        return False
    if not os.path.exists(texture_path):
        unreal.log_warning(f"[GameAssetMake] Terrain colour map not found on disk: {texture_path}")
        return False

    name = asset.get("name", "Terrain").replace(" ", "_")
    try:
        base_tex = _import_map(asset_tools, texture_path, env_folder, f"T_{name}_Color", "color")
        if base_tex is None:
            unreal.log_error("[GameAssetMake] Terrain colour map imported but not found as a texture.")
            return False
        normal_tex = _import_map(asset_tools, asset.get("normal_path"),
                                 env_folder, f"T_{name}_Normal", "normal")
        rough_tex = _import_map(asset_tools, asset.get("roughness_path"),
                                env_folder, f"T_{name}_Rough", "roughness")
        ao_tex = _import_map(asset_tools, asset.get("ao_path"),
                             env_folder, f"T_{name}_AO", "ao")

        mat_name = f"M_{name}_Terrain"
        mat_path = f"{env_folder.rstrip('/')}/{mat_name}"
        material = None
        if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
            material = unreal.EditorAssetLibrary.load_asset(mat_path)
        if material is None:
            material = asset_tools.create_asset(mat_name, env_folder.rstrip('/'),
                                                unreal.Material, unreal.MaterialFactoryNew())
        if material is None:
            unreal.log_error(f"[GameAssetMake] Could not create terrain material {mat_path}")
            return False

        mel = unreal.MaterialEditingLibrary
        try:
            mel.delete_all_material_expressions(material)
        except Exception:
            pass

        base = mel.create_material_expression(
            material, unreal.MaterialExpressionTextureSample, -400, 0)
        base.set_editor_property("texture", base_tex)
        mel.connect_material_property(base, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
        wired = ["base colour"]

        if normal_tex is not None:
            n = mel.create_material_expression(
                material, unreal.MaterialExpressionTextureSample, -400, 300)
            n.set_editor_property("texture", normal_tex)
            n.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
            mel.connect_material_property(n, "RGB", unreal.MaterialProperty.MP_NORMAL)
            wired.append("normal")

        if rough_tex is not None:
            r = mel.create_material_expression(
                material, unreal.MaterialExpressionTextureSample, -400, 600)
            r.set_editor_property("texture", rough_tex)
            r.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_GRAYSCALE)
            mel.connect_material_property(r, "R", unreal.MaterialProperty.MP_ROUGHNESS)
            wired.append("roughness")
        else:
            rough_const = mel.create_material_expression(
                material, unreal.MaterialExpressionConstant, -400, 600)
            rough_const.set_editor_property("r", 0.92)
            mel.connect_material_property(rough_const, "", unreal.MaterialProperty.MP_ROUGHNESS)

        if ao_tex is not None:
            a = mel.create_material_expression(
                material, unreal.MaterialExpressionTextureSample, -400, 900)
            a.set_editor_property("texture", ao_tex)
            a.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_GRAYSCALE)
            mel.connect_material_property(a, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
            wired.append("AO")

        # terrain is never metallic
        metal = mel.create_material_expression(
            material, unreal.MaterialExpressionConstant, -400, 1200)
        metal.set_editor_property("r", 0.0)
        mel.connect_material_property(metal, "", unreal.MaterialProperty.MP_METALLIC)

        mel.recompile_material(material)
        unreal.EditorAssetLibrary.save_asset(mat_path)

        actor.static_mesh_component.set_material(0, material)
        unreal.log(f"[GameAssetMake] Terrain PBR material applied: {mat_path} "
                   f"({', '.join(wired)})")
        return True
    except Exception as exc:
        unreal.log_error(f"[GameAssetMake] Terrain material step failed: {exc}")
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

    unreal.log(f"[GameAssetMake] importer v{IMPORTER_VERSION} | batch of {len(assets)} "
               f"asset(s) -> '{target_folder}' | scene setup: {auto_place}")
    for a in assets:
        unreal.log(f"[GameAssetMake]   received: name='{a.get('name')}' "
                   f"category='{a.get('category')}' file='{os.path.basename(str(a.get('source_file')))}'")
    imported_count = 0

    # Season/time-of-day sun from the Scene Director (if provided)
    environment = payload_dict.get("environment") or {}
    if environment and auto_place:
        _setup_sun(editor_actor_subsystem, environment)

    failed = []
    results = []
    for asset in assets:
        # One bad asset must NEVER abort the rest of the batch
        entry = {"id": asset.get("id"), "name": asset.get("name"),
                 "imported": False, "detail": ""}
        try:
            ok = _process_one_asset(
                asset, asset_tools, editor_actor_subsystem,
                target_folder, env_folder, auto_place, auto_collision)
            imported_count += ok
            entry["imported"] = bool(ok)
            entry["detail"] = ("imported" if ok else
                               "skipped (source file missing or unusable)")
        except Exception as exc:
            failed.append(asset.get("name", "?"))
            entry["detail"] = str(exc)
            unreal.log_error(f"[GameAssetMake] Asset '{asset.get('name')}' failed: {exc} "
                             f"— continuing with the next asset.")
        results.append(entry)

    unreal.log(f"[GameAssetMake] Batch complete: {imported_count}/{len(assets)} imported"
               + (f", failed: {failed}" if failed else "."))
    return results


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
    # CATEGORY is authoritative: an environment asset must never fall through to
    # the mesh/FBX path just because its file carries an unexpected extension.
    ENV_CATEGORIES = ("skydome", "skydome_hdri", "terrain", "texture", "tileable_texture")
    is_image = ext in ("png", "jpg", "jpeg", "exr", "hdr", "tga") or category in ENV_CATEGORIES
    is_gltf = ext in ("glb", "gltf")

    # ---- image-based environment assets --------------------------------
    if is_image:
        # For skydomes, the ComfyUI render counter makes the SOURCE FILE unique
        # every run — mirror that in the asset name so the material always binds
        # a brand-new texture, even if a previous one is locked by references.
        import_name = asset_name
        if category in ("skydome", "skydome_hdri"):
            stem = os.path.splitext(os.path.basename(source_path))[0]
            import_name = "".join(c if (c.isalnum() or c == "_") else "_" for c in stem).strip("_") or asset_name
        _reimport_clean(env_folder, import_name)
        _import_file(asset_tools, source_path, env_folder, import_name)
        unreal.log(f"[GameAssetMake] Imported {category or 'texture'} '{import_name}' -> {env_folder}")
        if category in ("skydome", "skydome_hdri"):
            if not auto_place:
                unreal.log(f"[GameAssetMake] Skydome '{asset_name}' imported as an asset only "
                           f"(scene setup toggle is off).")
                return 1
            tex = _find_texture(env_folder, import_name)
            if tex is None:
                unreal.log_error(
                    f"[GameAssetMake] Skydome texture '{import_name}' was NOT found in "
                    f"{env_folder} after import, so no sky was built. The source file was "
                    f"{source_path}. Check the Content Browser for the imported texture.")
            else:
                unreal.log(f"[GameAssetMake] Building skydome from texture '{tex.get_name()}'...")
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
                _apply_terrain_material(asset_tools, editor_actor_subsystem,
                                        actor, asset, env_folder)
        else:
            actor = _spawn_placed(editor_actor_subsystem, mesh, asset, 100.0)
            if actor:
                _normalize_and_ground(actor, asset)
    return 1
