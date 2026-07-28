# 📂 Example Workflows — Geekatplay GameAssetMake

Ready-to-load ComfyUI workflows by **Geekatplay Studio — Vladimir Chopine**.
Load any of these via **Workflow → Open** (or drag-and-drop the `.json` onto the ComfyUI canvas).

| Workflow | Target Engine | 3D Provider |
|---|---|---|
| `gameassetmake_unreal_tripo.json` | Unreal Engine 5 | Tripo3D |
| `gameassetmake_unreal_meshy.json` | Unreal Engine 5 | Meshy |
| `gameassetmake_unreal_hitem3d.json` | Unreal Engine 5 | Hitem3D *(experimental)* |
| `gameassetmake_unity_tripo.json` | Unity | Tripo3D |
| `gameassetmake_unity_meshy.json` | Unity | Meshy |

Each workflow contains the full pipeline:

**🎮 Asset Planner → SDXL concept generation → 🖼️ Gallery Approval → 🧊 3D Generator → ⚡/📦 Engine Bridge**

## Before running

1. **Checkpoint** — the `CheckpointLoaderSimple` node defaults to `sd_xl_base_1.0.safetensors`; switch it to any SDXL/FLUX/SD3 checkpoint you have installed.
2. **Batch size** — the `EmptyLatentImage` batch (default 12) should match the Planner's `target_asset_count` so every asset gets its own concept image.
3. **API key** — enter your Tripo3D / Meshy / Hitem3D key on the 3D Generator node (or set the `TRIPO_API_KEY` / `MESHY_API_KEY` / `HITEM3D_API_KEY` environment variables). `dry_run_mock` is **ON** by default — flip it off to spend credits.
4. **Engine bridge** — have the ComfyUnrealBridge plugin (port `30010`) or the Unity `ComfyUnityImporter.cs` script (port `8080`) running in your engine editor.

The 3D provider per workflow is preset with the 3D Generator's `engine_override` widget — set it back to *"use manifest (per-asset)"* to instead honor the per-asset choices you make in the approval gallery.
