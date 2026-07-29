# =============================================================
# Geekatplay GameAssetMake — ComfyUI web API routes
# (c) Geekatplay Studio / Vladimir Chopine
#
# /gameassetmake/unreal_import_status — proxies the Unreal bridge's
#   /import_results endpoint so the browser widget can poll import
#   confirmations without hitting CORS.
# /gameassetmake/model — serves generated 3D model files (restricted to
#   the ComfyUI output directory) to the in-browser 3D preview.
# =============================================================
import json
import os
import urllib.request

import folder_paths
from aiohttp import web
from server import PromptServer

routes = PromptServer.instance.routes

MODEL_MIME = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".fbx": "application/octet-stream",
    ".obj": "text/plain",
}


@routes.get("/gameassetmake/unreal_import_status")
async def unreal_import_status(request):
    host = request.rel_url.query.get("host", "127.0.0.1")
    try:
        port = int(request.rel_url.query.get("port", "30010"))
    except ValueError:
        port = 30010
    batch_id = request.rel_url.query.get("batch_id", "")

    url = f"http://{host}:{port}/import_results?batch_id={batch_id}"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return web.json_response(data)
    except Exception as err:
        return web.json_response(
            {"batch_id": batch_id, "known": False, "done": False,
             "results": [], "error": f"bridge unreachable: {err}"})


@routes.get("/gameassetmake/model")
async def serve_model(request):
    path = request.rel_url.query.get("path", "")
    if not path:
        return web.Response(status=400, text="missing path")

    real = os.path.realpath(path)
    output_root = os.path.realpath(folder_paths.get_output_directory())
    if not real.startswith(output_root + os.sep):
        return web.Response(status=403, text="path outside the output directory")
    if not os.path.isfile(real):
        return web.Response(status=404, text="file not found")

    ext = os.path.splitext(real)[1].lower()
    return web.FileResponse(
        real, headers={"Content-Type": MODEL_MIME.get(ext, "application/octet-stream")})
