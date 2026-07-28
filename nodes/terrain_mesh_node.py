# =============================================================
# Geekatplay GameAssetMake — Terrain Mesh Builder
# (c) Geekatplay Studio / Vladimir Chopine
#
# Turns a heightmap IMAGE into a real displaced terrain MESH
# (.glb with UVs and an optional baked ground texture) so the
# existing Unreal/Unity bridges import it as a static mesh at the
# correct world size — actual walkable terrain, not just a texture.
#
# (Unreal's native Landscape can't be reliably created headless
# across UE versions, so we build the mesh ourselves; the 16-bit
# heightmap PNG is still exported alongside for a manual
# Mode > Landscape > Import if preferred.)
# =============================================================
import os
import json
import numpy as np
import torch
import folder_paths


def build_terrain_mesh(height_field, world_size_m, height_m):
    """
    height_field: 2D numpy array (H, W) in 0..1.
    Returns (vertices [N,3] float32 torch, faces [M,3] int64 torch, uvs [N,2] float32 torch).
    Y-up (glTF convention); the Unreal importer converts axes on import.
    Terrain is centered on the origin.
    """
    h, w = height_field.shape
    xs = np.linspace(-world_size_m / 2.0, world_size_m / 2.0, w, dtype=np.float32)
    zs = np.linspace(-world_size_m / 2.0, world_size_m / 2.0, h, dtype=np.float32)
    xv, zv = np.meshgrid(xs, zs)
    yv = height_field.astype(np.float32) * float(height_m)

    vertices = np.stack([xv, yv, zv], axis=-1).reshape(-1, 3)

    uu, vv = np.meshgrid(np.linspace(0, 1, w, dtype=np.float32),
                         np.linspace(0, 1, h, dtype=np.float32))
    uvs = np.stack([uu, vv], axis=-1).reshape(-1, 2)

    idx = np.arange(h * w).reshape(h, w)
    a = idx[:-1, :-1].ravel()
    b = idx[:-1, 1:].ravel()
    c = idx[1:, :-1].ravel()
    d = idx[1:, 1:].ravel()
    # two triangles per quad, wound upward
    faces = np.concatenate([
        np.stack([a, c, b], axis=-1),
        np.stack([b, c, d], axis=-1),
    ], axis=0).astype(np.int64)

    return (torch.from_numpy(vertices), torch.from_numpy(faces), torch.from_numpy(uvs))


class TerrainMeshBuilderNode:
    """
    Heightmap IMAGE -> displaced terrain mesh (.glb, textured) + 16-bit PNG,
    packaged as a bridge-compatible manifest for automatic engine import.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "heightmap": ("IMAGE",),
                "terrain_name": ("STRING", {"default": "Terrain_01"}),
                "grid_resolution": ("INT", {"default": 256, "min": 32, "max": 1024, "step": 32,
                                            "tooltip": "Vertices per side. 256 = ~65k verts / 130k tris."}),
                "world_size_m": ("FLOAT", {"default": 500.0, "min": 10.0, "max": 20000.0, "step": 10.0}),
                "height_m": ("FLOAT", {"default": 80.0, "min": 0.5, "max": 5000.0, "step": 1.0}),
                "smooth_passes": ("INT", {"default": 1, "min": 0, "max": 10,
                                          "tooltip": "Box-blur passes on the height field to remove stair-stepping."}),
            },
            "optional": {
                "ground_texture": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("completed_3d_manifest_json", "mesh_path", "heightmap16_path")
    FUNCTION = "build_terrain"
    CATEGORY = "Geekatplay GameAssetMake/Environment"
    OUTPUT_NODE = True

    def build_terrain(self, heightmap, terrain_name="Terrain_01", grid_resolution=256,
                      world_size_m=500.0, height_m=80.0, smooth_passes=1, ground_texture=None):
        from PIL import Image as PILImage
        from comfy_extras.nodes_save_3d import save_glb

        out_dir = os.path.join(folder_paths.get_output_directory(), "game_environment")
        os.makedirs(out_dir, exist_ok=True)
        safe = str(terrain_name).strip().replace(" ", "_") or "Terrain"

        # --- height field: grayscale, resampled to the grid resolution ---
        arr = heightmap[0].cpu().numpy()
        gray = arr.mean(axis=2) if arr.ndim == 3 else arr
        img = PILImage.fromarray(np.clip(gray * 255.0, 0, 255).astype(np.uint8))
        img = img.resize((grid_resolution, grid_resolution), PILImage.LANCZOS)
        field = np.asarray(img).astype(np.float32) / 255.0

        for _ in range(max(0, smooth_passes)):
            padded = np.pad(field, 1, mode="edge")
            field = (padded[:-2, :-2] + padded[:-2, 1:-1] + padded[:-2, 2:] +
                     padded[1:-1, :-2] + padded[1:-1, 1:-1] + padded[1:-1, 2:] +
                     padded[2:, :-2] + padded[2:, 1:-1] + padded[2:, 2:]) / 9.0

        # normalize so the full height range is used
        lo, hi = float(field.min()), float(field.max())
        if hi > lo:
            field = (field - lo) / (hi - lo)

        # --- 16-bit PNG export (for manual UE Landscape import if preferred) ---
        png16_path = os.path.join(out_dir, f"{safe}_heightmap16.png")
        gray16 = np.clip(field * 65535.0, 0, 65535).astype(np.uint16)
        PILImage.fromarray(gray16, mode="I;16").save(png16_path)

        # --- displaced mesh ---
        vertices, faces, uvs = build_terrain_mesh(field, world_size_m, height_m)

        texture_image = None
        if ground_texture is not None:
            tex = np.clip(ground_texture[0].cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
            texture_image = PILImage.fromarray(tex[..., :3], mode="RGB")

        mesh_path = os.path.join(out_dir, f"{safe}.glb")
        save_glb(vertices, faces, mesh_path, {}, uvs=uvs,
                 vertex_colors=None, texture_image=texture_image, unlit=False)

        size_mb = os.path.getsize(mesh_path) / 1e6
        print(f"[GameAssetMake Terrain] '{safe}': {vertices.shape[0]} verts / "
              f"{faces.shape[0]} tris, {world_size_m:.0f}m x {world_size_m:.0f}m x {height_m:.0f}m "
              f"-> {os.path.basename(mesh_path)} ({size_mb:.1f} MB)")

        manifest = [{
            "id": f"terrain_{safe.lower()}",
            "name": safe,
            "category": "terrain_mesh",
            "model_path": mesh_path,
            "model_format": "GLB",
            "rig_type": "none",
            "include_rigging": False,
            # glb is authored in meters at world scale; don't rescale on placement
            "scale_override": [1.0, 1.0, 1.0],
            "collision_type": "complex",
            "world_placement_offset": [0.0, 0.0, 0.0],
            "world_rotation_yaw": 0.0,
            "generation_status": "TERRAIN_MESH_OK",
            "heightmap16_path": png16_path,
            "terrain_world_size_m": world_size_m,
            "terrain_height_m": height_m,
        }]

        return (json.dumps(manifest, indent=2), mesh_path, png16_path)
