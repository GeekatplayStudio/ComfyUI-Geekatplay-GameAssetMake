# 📂 Workflows — Geekatplay GameAssetMake

Ready-to-load ComfyUI workflows by **Geekatplay Studio — Vladimir Chopine**.
Load via **Workflow → Open** or drag the `.json` onto the canvas.

**Full game assets from a single prompt** — models, terrain, skydome, and materials:

| Workflow | What it makes | Engine delivery |
|---|---|---|
| `gameassetmake_unreal.json` | **Universal 3D asset pipeline** — planner → concepts → guardrail → gallery → 3D models | Unreal Engine 5 (:30010) |
| `gameassetmake_unity.json` | Same universal pipeline | Unity (:8080) |
| `gameassetmake_terrain.json` | **Terrain heightmap** from a text description (16-bit PNG for Landscape/Terrain import) | Unreal Engine 5 |
| `gameassetmake_skydome.json` | **360° skydome HDRI** (.exr, seam-healed equirectangular panorama) | Unreal Engine 5 |
| `gameassetmake_textures.json` | **Seamless PBR material** (albedo + normal + roughness + metallic) | Unreal Engine 5 |

The 3D provider (**Tripo3D / Meshy / HiTem3D**) is one global `engine` choice on the 3D Generator node — no more per-provider workflow duplicates. Unity delivery for terrain/skydome/textures: swap the bridge node for the Unity one.

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
