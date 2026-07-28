# 📂 Workflows — Geekatplay GameAssetMake

Ready-to-load ComfyUI workflows by **Geekatplay Studio — Vladimir Chopine**.
Load via **Workflow → Open** or drag the `.json` onto the canvas.

**Full game assets from a single prompt** — models, terrain, skydome, and materials:

| Workflow | What it makes | Engine delivery |
|---|---|---|
| `gameassetmake_unreal.json` | **Universal 3D asset pipeline** — planner → concepts → guardrail → gallery → 3D models | Unreal Engine 5 (:30010) |
| `gameassetmake_unity.json` | Same universal pipeline | Unity (:8080) |
| `gameassetmake_terrain.json` / `_unity.json` | **Walkable terrain mesh** from a description — heightmap + matching ground texture, imported and placed | UE5 / Unity |
| `gameassetmake_skydome.json` / `_unity.json` | **360° skydome HDRI** — set up as a sky sphere (UE) or scene skybox (Unity) | UE5 / Unity |
| `gameassetmake_textures.json` | **Seamless PBR material** (albedo + normal + roughness + metallic) | Unreal Engine 5 |
| `gameassetmake_local_hunyuan3d_unreal.json` | **Fully local 3D** — Hunyuan3D 2.1 on your GPU, no API/keys/credits | Unreal Engine 5 |
| `gameassetmake_local_hunyuan3d_unity.json` | Same, fully local | Unity |
| `gameassetmake_full_scene_unreal.json` | 🎬 **FULL SCENE from one prompt** — terrain mesh + skydome + positioned assets | Unreal Engine 5 |

## 🎬 Full scene from one prompt

Type *"medieval village in a green valley"* into the **Scene Director** node and queue. It plans the whole scene — using a local Ollama LLM (`qwen2.5:7b` by default) for deep layout analysis, with a deterministic seeded layout engine as fallback — and drives three parallel branches:

1. **Terrain** — the director's terrain brief → heightmap generation → **Terrain Mesh Builder** turns it into a real displaced, UV-mapped mesh (.glb) at true world size, imported into Unreal as a walkable static mesh placed at the origin. A 16-bit heightmap PNG is exported alongside for a native `Mode → Landscape → Import` if you prefer a real Landscape.
2. **Skydome** — the director's sky brief → 2:1 panorama → seam heal → HDRI (.exr) → in Unreal, an **unlit emissive sky sphere is created automatically** (material built from the imported texture, applied to an inverted 800m dome).
3. **Assets** — the director emits a positioned asset list (world x/y in meters, rotation yaw, per-asset scale — houses ring the well facing center, church at the edge, props scattered logically) that feeds the existing planner → per-asset concepts → **Gallery approval stop** (reject and re-queue until you like the set) → 3D generation → placed in Unreal at the exact layout coordinates and rotations.

The gallery is the human-in-the-loop stop: nothing is spent on 3D generation until you approve. Re-queue with a different seed to redo any branch.

### 🎚️ Import-only vs. import + scene setup

Every exporter — Unreal **and** Unity — has one master toggle:

| Toggle | Unreal (`auto_place_in_level`) | Unity (`auto_instantiate_in_scene`) |
|---|---|---|
| **Import + Set Up In Scene** | meshes spawned at their coordinates, terrain placed and size-verified, **skydome built as an emissive sky sphere**, sun DirectionalLight configured | prefabs instantiated, terrain placed, **skydome assigned as the scene skybox** (`RenderSettings.skybox`, panoramic material), sun directional light configured |
| **Import As Assets Only** | everything lands in `/Game/Assets/AI_Generated/` — nothing touches your level | everything lands in `Assets/AI_Generated/` — nothing touches your scene |

This applies to **all** asset kinds: objects, terrain, and skydome. The terrain and skydome workflows now route through the Placement Manager and a real bridge, so they import *and* set up exactly like the asset workflows do.

### 📐 Placement Manager — sizes and positions that actually match

Every delivery now flows through the **Placement Manager** node, the single authority for what enters the engine:

- **Order** — terrain imports first, then the skydome, then assets, in one payload through one bridge node.
- **True scale** — AI-generated meshes arrive in random units. Each asset carries `target_size_m` (its intended real-world size); the engine importer *measures the imported mesh's bounds* and scales it to match exactly, then verifies the terrain's world size the same way. No more 100× surprises.
- **Terrain snapping** — asset Z is sampled from the terrain's 16-bit heightfield, so buildings sit ON hills instead of floating or sinking; the mesh *bottom* is grounded, not its pivot.
- **Overlap resolution** — assets planned too close are nudged apart automatically.
- One failed asset no longer aborts the batch — the Unreal importer isolates errors per asset and reports which ones failed at the end.

### 🗺️ Layout map & 🌦️ season/time intelligence

- The **Layout Map** node renders the director's plan as a visible top-down map: meter grid, one marker per asset with name + (x, y) coordinates, facing arrows, and a sun compass — verify the placement *before* generating anything.
- The prompt drives the **environment**: say *"medieval village at sunset in winter"* and the director detects season + time of day, then: the terrain's ground texture prompt becomes seasonal (snow on peaks, frozen paths — baked onto the terrain mesh), the skydome prompt becomes a winter sunset sky, and the **sun position is computed for that season and hour** (winter sunset ≈ azimuth 265°, elevation 2°, warm orange). The Unreal bridge carries this as `environment_json`, and the plugin **sets a DirectionalLight in the level** to match — imported terrain, sky, and lighting all agree.
- Ground-texture-to-terrain pairing follows the two-step approach from the author's [ai-terrain](https://github.com/GeekatplayStudio/ai-terrain) project: texture generated to match the terrain description and elevation zones, then baked into the terrain `.glb` with matching UVs.

The 3D provider (**Tripo3D / Meshy / HiTem3D**) is one global `engine` choice on the 3D Generator node — no more per-provider workflow duplicates. Unity delivery for terrain/skydome/textures: swap the bridge node for the Unity one.

## 🖥️ Local vs cloud 3D generation

Two interchangeable ways to turn approved concepts into meshes — both feed the same engine bridges:

| | Cloud (`gameassetmake_unreal/unity.json`) | Local (`gameassetmake_local_hunyuan3d_*.json`) |
|---|---|---|
| Provider | Tripo3D / Meshy / HiTem3D | **Hunyuan3D 2.1** on your own GPU |
| API key | required | **none** |
| Cost | consumes credits | free |
| Output | `.FBX`/`.GLB`, PBR textures, auto-rigging | `.glb` geometry + vertex colors |
| Privacy | images uploaded | nothing leaves your machine |

The local workflow needs **`hunyuan_3d_v2.1.safetensors`** in `ComfyUI/models/checkpoints/` ([download](https://huggingface.co/Comfy-Org/hunyuan3D_2.1_repackaged/resolve/main/hunyuan_3d_v2.1.safetensors), ~7.4 GB). It loads through an `ImageOnlyCheckpointLoader` (MODEL / CLIP_VISION / VAE) into the **🖥️ Local 3D Generator**, which loops over every approved asset — CLIP-Vision encode → Hunyuan3D conditioning → KSampler (30 steps, cfg 5) → voxel decode → mesh → `.glb`. Rigging and PBR texture maps are cloud-only features; use the API workflow when you need those.

## 🔑 API keys — enter once, stored securely

Keys are **never saved in workflow JSON** — share workflows freely, they leak nothing.
Type a key into the 3D Generator once with `remember_keys` ON and it's stored in the **OS credential vault** (Windows Credential Manager / macOS Keychain). After that the fields stay empty and keys load automatically. Keys saved by the ComfyUI-Blender-Toolbox Credential Manager are also picked up.

- **Tripo3D / Meshy**: one field each — `tripo_api_key`, `meshy_api_key`
- **HiTem3D**: **two separate fields** — `hitem3d_access_key` (`ak_...`) and `hitem3d_secret_key` (`sk_...`). Per the [HiTem3D docs](https://docs.hi3d.ai/en/api/api-reference/list/get-token), these are your Client ID and Client Secret; the node sends them as `Basic base64(access:secret)` to `/open-api/v1/auth/token` and uses the returned bearer token. **Both are required** — filling only one gives an error naming the missing half.

## 🔁 One image per asset (not one image of everything)

The universal workflows use the **Batch Concept Generator**, which **loops over the planner's prompt list and renders one image per asset**, each with its own seed. This is essential: feeding the whole prompt list into a single `CLIPTextEncode` produces one combined prompt, so every image ends up containing every asset at once. The loop node encodes and samples each prompt separately, and reports progress per item in the console.

## 🛡️ Single-object guardrail

The Batch Concept Generator verifies each asset **as it is generated** (`verify_single_object`) and retries that item immediately if it fails — one object, isolated on white; for rigged characters, exactly one human/animal. A standalone **Single-Object Guardrail** node is also available if you generate concepts some other way. Checks performed:

- **heuristic** — local blob analysis of the white background (free, instant)
- **vlm** — cross-check by a local [Ollama](https://ollama.com) vision model (default `gemma3` at `http://127.0.0.1:11434`; if Ollama isn't running, the heuristic alone decides)
- Failed images are **automatically regenerated** with a stricter prompt (up to `max_retries`), because a concept with two objects or a busy background ruins the 3D mesh.

## ⚙️ Before running

1. **Models** — Z-Image Turbo (benchmarked best for isolated game-asset concepts, ~7.5 s/image on an RTX 3090): `UNETLoader` → `z_image_turbo_bf16.safetensors`, `CLIPLoader` (type `lumina2`) → `qwen_3_4b.safetensors`, `VAELoader` → `ae.safetensors`. Sampling preset: 8 steps @ cfg 1.0, 16-channel `EmptySD3LatentImage`.
2. **Companion pack** — terrain, skydome, and texture workflows use nodes from [ComfyUI-Blender-Toolbox](https://github.com/GeekatplayStudio/ComfyUI_Blender_toolbox) (same author) — install it alongside this pack.
3. **Batch size** — nothing to match: the Batch Concept Generator renders exactly as many images as the planner produced prompts (`target_asset_count`). Set the image size on the generator node itself.
4. **Engine bridge** — ComfyUnrealBridge plugin (:30010) or ComfyUnityImporter.cs (:8080) running; the 🔌 Connection Check node shows ONLINE/OFFLINE.
5. **dry_run_mock** is ON by default on the 3D Generator — flip it off to spend API credits.

## 🌍 Environment assets in the engine

Terrain heightmaps arrive in Unreal under `/Game/Assets/AI_Generated/Environment` as textures; the source 16-bit PNG path is printed in the Output Log for **Mode → Landscape → Import from File**. Skydome EXRs import as textures ready for a sky material; PBR material sets are saved to `output/PBR_Materials/`.
