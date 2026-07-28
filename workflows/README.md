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

Every workflow also includes a **🔌 Engine Connection Check** node that verifies your Unreal/Unity editor bridge is installed and reachable each time you queue — it shows a green **ONLINE** / red **OFFLINE** badge right on the node.

## Before running

1. **Checkpoint** — the loader defaults to `flux1-dev-fp8.safetensors` (**Flux is recommended over SDXL** for clean isolated-object concepts; Z-Image or any modern checkpoint also works — adjust `cfg`/`steps` to suit: Flux fp8 uses cfg `1.0`, SDXL-style models want cfg ~`7`).
2. **Batch size** — the `EmptyLatentImage` batch (default 12) should match the Planner's `target_asset_count` so every asset gets its own concept image.
3. **API key** — enter your Tripo3D / Meshy / Hitem3D key on the 3D Generator node (or set the `TRIPO_API_KEY` / `MESHY_API_KEY` / `HITEM3D_API_KEY` environment variables). `dry_run_mock` is **ON** by default — flip it off to spend credits.
4. **Engine bridge** — have the ComfyUnrealBridge plugin (port `30010`) or the Unity `ComfyUnityImporter.cs` script (port `8080`) running in your engine editor. The Connection Check node (and the bridge nodes themselves) will tell you if it isn't.

The 3D provider is a **global choice** set with the 3D Generator's `engine` widget (preset per workflow). Concept images are generated **one object per image, 3/4 view to the camera, isolated on white** — the framing 3D generation APIs work best with. After generation, the 3D Generator node displays a results panel listing **every model returned from the API** with its status and local file path.
