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
        public float[] target_size_m;
        public bool normalize_to_target;
        public float ground_z_cm;
    }

    [Serializable]
    public class GameForgePayload
    {
        public string target_assets_folder;
        public bool auto_instantiate;
        public List<GameForgeAsset> assets;
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
                    // Unreal yaw (about Z-up) maps to Unity rotation about Y-up
                    if (Mathf.Abs(meta.world_rotation_yaw) > 0.01f)
                    {
                        instance.transform.rotation = Quaternion.Euler(0f, meta.world_rotation_yaw, 0f);
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
            }

            Debug.Log($"[Geekatplay GameForge] Imported {importedAssetPaths.Count} asset(s) into {targetFolder}");
        }
    }
}
#endif
