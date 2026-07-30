// =============================================================
// Geekatplay GameForge — Unity Editor asset importer bridge
// (c) Geekatplay Studio / Vladimir Chopine
// Drop this file anywhere inside your Unity project's Assets/ folder
// (e.g. Assets/Editor/ComfyUnityImporter.cs). Listens on port 8080.
// =============================================================
#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace GeekatplayGameForge
{
    [Serializable]
    public class GameForgeAsset
    {
        public string id;
        public string name;
        public string category;
        public string model_path;
        public string model_format;
        public float[] scale_override;
        public float[] world_placement_offset;
        public float world_rotation_yaw;
        public float world_rotation_pitch;
        public float world_rotation_roll;
        public float[] target_size_m;
        public bool normalize_to_target;
        public float ground_z_cm;
        public string placement_role;
    }

    [Serializable]
    public class GameForgeSun
    {
        public float azimuth_deg;
        public float elevation_deg;
        public float intensity = 1f;
        public string color_hex;
    }

    [Serializable]
    public class GameForgeEnvironment
    {
        public string season;
        public string time_of_day;
        public GameForgeSun sun;
    }

    [Serializable]
    public class GameForgePayload
    {
        public string target_assets_folder;
        public bool auto_instantiate;
        public List<GameForgeAsset> assets;
        public GameForgeEnvironment environment;
    }

    [InitializeOnLoad]
    public static class ComfyUnityImporter
    {
        private static HttpListener listener;
        private const int Port = 8080;
        private static readonly Queue<GameForgePayload> pending = new Queue<GameForgePayload>();
        private static readonly object queueLock = new object();

        static ComfyUnityImporter()
        {
            EditorApplication.update += ProcessPendingOnMainThread;
            StartServer();
        }

        private static async void StartServer()
        {
            try
            {
                listener = new HttpListener();
                listener.Prefixes.Add($"http://127.0.0.1:{Port}/import_assets/");
                listener.Start();
                Debug.Log($"[Geekatplay GameForge] Unity bridge listening on port {Port}...");

                while (listener.IsListening)
                {
                    var context = await listener.GetContextAsync();
                    ProcessRequest(context);
                }
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[Geekatplay GameForge] Bridge server stopped: {ex.Message}");
            }
        }

        private static void ProcessRequest(HttpListenerContext context)
        {
            string status = "ok";
            try
            {
                string body;
                using (var reader = new StreamReader(context.Request.InputStream, Encoding.UTF8))
                    body = reader.ReadToEnd();

                var payload = JsonUtility.FromJson<GameForgePayload>(body);
                if (payload != null && payload.assets != null)
                {
                    lock (queueLock) { pending.Enqueue(payload); }
                }
                else
                {
                    status = "empty_payload";
                }
            }
            catch (Exception ex)
            {
                status = "error: " + ex.Message;
            }

            var response = context.Response;
            byte[] buffer = Encoding.UTF8.GetBytes("{\"status\":\"" + status.Replace("\"", "'") + "\"}");
            response.ContentType = "application/json";
            response.ContentLength64 = buffer.Length;
            response.OutputStream.Write(buffer, 0, buffer.Length);
            response.OutputStream.Close();
        }

        // AssetDatabase / scene APIs must run on the Unity main thread.
        private static void ProcessPendingOnMainThread()
        {
            GameForgePayload payload = null;
            lock (queueLock)
            {
                if (pending.Count > 0) payload = pending.Dequeue();
            }
            if (payload == null) return;

            string targetFolder = string.IsNullOrEmpty(payload.target_assets_folder)
                ? "Assets/AI_Generated/"
                : payload.target_assets_folder;
            if (!targetFolder.StartsWith("Assets")) targetFolder = "Assets/" + targetFolder.TrimStart('/');
            targetFolder = targetFolder.TrimEnd('/');

            string absTarget = Path.Combine(
                Directory.GetParent(Application.dataPath).FullName,
                targetFolder.Replace('/', Path.DirectorySeparatorChar));
            Directory.CreateDirectory(absTarget);

            var importedAssetPaths = new List<string>();
            var assetMeta = new List<GameForgeAsset>();
            var environmentAssets = new List<KeyValuePair<string, GameForgeAsset>>();

            foreach (var asset in payload.assets)
            {
                if (string.IsNullOrEmpty(asset.model_path) || !File.Exists(asset.model_path))
                {
                    Debug.LogWarning($"[Geekatplay GameForge] Skipping '{asset.name}': source file missing ({asset.model_path})");
                    continue;
                }

                string safeName = string.IsNullOrEmpty(asset.name) ? "GeneratedAsset" : asset.name.Replace(' ', '_');
                // Prefix with the asset id: two assets sharing a name would otherwise
                // overwrite each other and only the last one would survive.
                string idPrefix = string.IsNullOrEmpty(asset.id) ? "" : asset.id.Replace(' ', '_') + "_";
                string ext = Path.GetExtension(asset.model_path);
                string destFile = idPrefix + safeName + ext;
                string destAbs = Path.Combine(absTarget, destFile);
                File.Copy(asset.model_path, destAbs, true);

                bool isImage = ext.Equals(".png", StringComparison.OrdinalIgnoreCase) ||
                               ext.Equals(".jpg", StringComparison.OrdinalIgnoreCase) ||
                               ext.Equals(".jpeg", StringComparison.OrdinalIgnoreCase) ||
                               ext.Equals(".exr", StringComparison.OrdinalIgnoreCase) ||
                               ext.Equals(".hdr", StringComparison.OrdinalIgnoreCase) ||
                               ext.Equals(".tga", StringComparison.OrdinalIgnoreCase);

                if (isImage)
                {
                    // Environment images (skydome HDRI / heightmap / textures) are
                    // handled after the AssetDatabase refresh below, not instantiated.
                    environmentAssets.Add(new KeyValuePair<string, GameForgeAsset>(
                        targetFolder + "/" + destFile, asset));
                    continue;
                }

                if (ext.Equals(".glb", StringComparison.OrdinalIgnoreCase) ||
                    ext.Equals(".gltf", StringComparison.OrdinalIgnoreCase))
                {
                    Debug.LogWarning($"[Geekatplay GameAssetMake] '{destFile}' is glTF. Unity has no " +
                        "built-in glTF importer — install glTFast (com.unity.cloud.gltfast) or UnityGLTF, " +
                        "or generate FBX instead, otherwise no prefab will be created for it.");
                }

                importedAssetPaths.Add(targetFolder + "/" + destFile);
                assetMeta.Add(asset);
            }

            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            if (payload.auto_instantiate)
            {
                for (int i = 0; i < importedAssetPaths.Count; i++)
                {
                    var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(importedAssetPaths[i]);
                    if (prefab == null)
                    {
                        Debug.LogWarning($"[Geekatplay GameAssetMake] Imported '{importedAssetPaths[i]}' " +
                            "but Unity produced no GameObject for it, so nothing was placed in the scene. " +
                            "For .glb/.gltf this means no glTF importer package is installed.");
                        continue;
                    }

                    var instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
                    var meta = assetMeta[i];

                    // Manifest coordinates are Unreal-style centimeters (Z-up); Unity uses meters (Y-up).
                    if (meta.world_placement_offset != null && meta.world_placement_offset.Length >= 3)
                    {
                        instance.transform.position = new Vector3(
                            meta.world_placement_offset[0] / 100f,
                            meta.world_placement_offset[2] / 100f,
                            meta.world_placement_offset[1] / 100f);
                    }
                    if (meta.scale_override != null && meta.scale_override.Length >= 3)
                    {
                        instance.transform.localScale = new Vector3(
                            meta.scale_override[0], meta.scale_override[2], meta.scale_override[1]);
                    }
                    // Unreal is Z-up, Unity is Y-up: yaw (about Unreal Z) becomes a
                    // rotation about Unity Y, pitch (about Unreal Y) about Unity X, and
                    // roll (about Unreal X) about Unity Z. Pitch/roll are the terrain
                    // normal under the asset, so props follow the slope; buildings are
                    // sent as 0/0 and stay upright.
                    if (Mathf.Abs(meta.world_rotation_yaw) > 0.01f
                        || Mathf.Abs(meta.world_rotation_pitch) > 0.01f
                        || Mathf.Abs(meta.world_rotation_roll) > 0.01f)
                    {
                        instance.transform.rotation = Quaternion.Euler(
                            meta.world_rotation_pitch,
                            meta.world_rotation_yaw,
                            meta.world_rotation_roll);
                    }

                    // Placement Manager normalization: measure the mesh and scale it
                    // to its intended real-world size (AI meshes arrive in random units),
                    // then sit its bottom on the terrain ground height.
                    if (meta.normalize_to_target && meta.target_size_m != null && meta.target_size_m.Length >= 3)
                    {
                        var renderers = instance.GetComponentsInChildren<Renderer>();
                        if (renderers.Length > 0)
                        {
                            Bounds bounds = renderers[0].bounds;
                            for (int rb = 1; rb < renderers.Length; rb++) bounds.Encapsulate(renderers[rb].bounds);
                            float actualMax = Mathf.Max(bounds.size.x, bounds.size.y, bounds.size.z);
                            float targetMax = Mathf.Max(meta.target_size_m[0], meta.target_size_m[1], meta.target_size_m[2]);
                            if (actualMax > 0.001f)
                            {
                                float s = targetMax / actualMax;
                                instance.transform.localScale = instance.transform.localScale * s;
                                // re-measure and ground the mesh bottom
                                bounds = renderers[0].bounds;
                                for (int rb = 1; rb < renderers.Length; rb++) bounds.Encapsulate(renderers[rb].bounds);
                                float groundY = meta.ground_z_cm / 100f;
                                float shift = groundY - bounds.min.y;
                                instance.transform.position += new Vector3(0f, shift, 0f);
                                Debug.Log($"[Geekatplay GameAssetMake] Normalized '{meta.name}': " +
                                          $"{actualMax:F2}m -> {targetMax:F2}m (scale {s:F3}), grounded at {groundY:F2}m");
                            }
                        }
                    }
                    Undo.RegisterCreatedObjectUndo(instance, "GameForge Import");
                }

                // Environment: skydome -> skybox, sun -> directional light
                foreach (var kv in environmentAssets)
                {
                    SetupEnvironmentAsset(kv.Key, kv.Value, targetFolder);
                }
                if (payload.environment != null && payload.environment.sun != null)
                {
                    SetupSun(payload.environment);
                }
            }
            else if (environmentAssets.Count > 0)
            {
                Debug.Log($"[Geekatplay GameAssetMake] Imported {environmentAssets.Count} environment " +
                          "asset(s) as project assets only (scene setup toggle is off).");
            }

            Debug.Log($"[Geekatplay GameAssetMake] Imported {importedAssetPaths.Count} mesh(es) and " +
                      $"{environmentAssets.Count} environment asset(s) into {targetFolder}");
        }

        /// <summary>
        /// Sets up an imported environment image in the scene:
        /// skydome HDRI -> panoramic skybox material assigned to RenderSettings,
        /// terrain heightmap -> logged with instructions (Unity Terrain needs RAW),
        /// texture -> left as a project asset.
        /// </summary>
        private static void SetupEnvironmentAsset(string assetPath, GameForgeAsset asset, string targetFolder)
        {
            string category = asset.category ?? "";

            if (category == "skydome" || category == "skydome_hdri")
            {
                var tex = AssetDatabase.LoadAssetAtPath<Texture>(assetPath);
                if (tex == null)
                {
                    Debug.LogWarning($"[Geekatplay GameAssetMake] Could not load skydome texture at {assetPath}");
                    return;
                }

                var shader = Shader.Find("Skybox/Panoramic");
                if (shader == null)
                {
                    Debug.LogWarning("[Geekatplay GameAssetMake] Shader 'Skybox/Panoramic' not found — " +
                                     "cannot build the skybox material automatically.");
                    return;
                }

                string matPath = $"{targetFolder}/M_{Path.GetFileNameWithoutExtension(assetPath)}_Skybox.mat";
                var mat = AssetDatabase.LoadAssetAtPath<Material>(matPath);
                if (mat == null)
                {
                    mat = new Material(shader);
                    AssetDatabase.CreateAsset(mat, matPath);
                }
                mat.shader = shader;
                mat.SetTexture("_MainTex", tex);
                mat.SetFloat("_Mapping", 1f);        // Latitude-Longitude layout
                mat.SetFloat("_ImageType", 0f);      // 360 degrees
                EditorUtility.SetDirty(mat);
                AssetDatabase.SaveAssets();

                RenderSettings.skybox = mat;
                DynamicGI.UpdateEnvironment();
                Debug.Log($"[Geekatplay GameAssetMake] Skydome applied to the scene skybox ({matPath}).");
            }
            else if (category == "terrain")
            {
                Debug.Log($"[Geekatplay GameAssetMake] Heightmap imported at {assetPath}. " +
                          "For a native Unity Terrain use Terrain > Import Raw with the 16-bit source, " +
                          "or use the terrain MESH workflow which places walkable geometry automatically.");
            }
        }

        /// <summary>
        /// Positions/configures a directional light from the Scene Director's
        /// season and time-of-day sun data (azimuth 0 = North, clockwise).
        /// </summary>
        private static void SetupSun(GameForgeEnvironment env)
        {
            try
            {
                var sun = env.sun;
                Light light = null;
                foreach (var l in UnityEngine.Object.FindObjectsByType<Light>(FindObjectsSortMode.None))
                {
                    if (l.type == LightType.Directional) { light = l; break; }
                }
                if (light == null)
                {
                    var go = new GameObject("Sun");
                    light = go.AddComponent<Light>();
                    light.type = LightType.Directional;
                    Undo.RegisterCreatedObjectUndo(go, "GameAssetMake Sun");
                }

                // Unity: rotate so the light shines FROM the sun azimuth/elevation.
                light.transform.rotation = Quaternion.Euler(sun.elevation_deg, sun.azimuth_deg + 180f, 0f);
                light.intensity = Mathf.Max(0.1f, sun.intensity / 5f);   // Unreal lux -> Unity relative
                if (!string.IsNullOrEmpty(sun.color_hex) &&
                    ColorUtility.TryParseHtmlString(sun.color_hex, out Color c))
                {
                    light.color = c;
                }
                light.gameObject.name = $"Sun_{env.season}_{env.time_of_day}";
                Debug.Log($"[Geekatplay GameAssetMake] Sun set: {env.season} {env.time_of_day} " +
                          $"az={sun.azimuth_deg} el={sun.elevation_deg} {sun.color_hex}");
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[Geekatplay GameAssetMake] Sun setup failed: {ex.Message}");
            }
        }
    }
}
#endif
