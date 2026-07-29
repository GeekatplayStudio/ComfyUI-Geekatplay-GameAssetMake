# =============================================================
# Geekatplay GameAssetMake — Unreal Engine Bridge HTTP listener
# (c) Geekatplay Studio / Vladimir Chopine
# =============================================================
import importlib
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import unreal
except ImportError:
    unreal = None

# UE Content/Python is on sys.path, these modules run as top-level scripts (no package)
import comfy_importer

SERVER_PORT = 30010
# Printed at startup; comfy_importer is hot-reloaded per payload, but this
# server module itself only updates on an editor restart.
SERVER_VERSION = "2026.07.29a-import-results"
_server_instance = None
_pending_payloads = []
# batch_id -> {"done": bool, "results": [{id, name, imported, detail}, ...]}
_import_results = {}


class ComfyUnrealRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)

        try:
            payload = json.loads(post_data.decode("utf-8"))
            batch_id = payload.get("batch_id")
            if batch_id:
                _import_results[batch_id] = {"done": False, "results": []}
            _pending_payloads.append(payload)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = json.dumps({"status": "received", "items": len(payload.get("assets", []))})
            self.wfile.write(response.encode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode("utf-8"))

    def do_GET(self):
        if self.path.startswith("/import_results"):
            # /import_results?batch_id=... -> per-asset import outcome
            batch_id = ""
            if "?" in self.path:
                from urllib.parse import parse_qs, urlparse
                batch_id = parse_qs(urlparse(self.path).query).get("batch_id", [""])[0]
            data = _import_results.get(batch_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = {"batch_id": batch_id, "known": data is not None}
            body.update(data or {"done": False, "results": []})
            self.wfile.write(json.dumps(body).encode("utf-8"))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "Geekatplay GameAssetMake Unreal Bridge Active",
            "port": SERVER_PORT
        }).encode("utf-8"))

    def log_message(self, format, *args):
        # Silence default HTTP server console noise
        pass


def process_pending_imports_on_tick(delta_seconds):
    """
    Main-thread tick handler registered with the Slate post-tick callback in Unreal.
    Asset import must happen on the game thread, never on the HTTP thread.
    """
    while _pending_payloads:
        payload = _pending_payloads.pop(0)
        try:
            # Unreal caches Python modules for the whole editor session, so plugin
            # edits otherwise need a full editor restart to take effect. Reload
            # before every payload so the newest importer always runs.
            try:
                importlib.reload(comfy_importer)
            except Exception as reload_err:
                if unreal:
                    unreal.log_warning(f"[GameAssetMake] Importer reload failed "
                                       f"({reload_err}); using the already-loaded copy.")
            results = comfy_importer.import_and_place_manifest_payload(payload)
            batch_id = payload.get("batch_id")
            if batch_id:
                _import_results[batch_id] = {
                    "done": True,
                    "results": results if isinstance(results, list) else [],
                }
        except Exception as e:
            batch_id = payload.get("batch_id")
            if batch_id:
                _import_results[batch_id] = {"done": True, "results": [],
                                             "error": str(e)}
            if unreal:
                import traceback
                unreal.log_error(f"[GameAssetMake Bridge Import Error]: {e}")
                unreal.log_error(traceback.format_exc())


def start_bridge_server():
    global _server_instance
    if _server_instance is not None:
        return

    def run_server():
        HTTPServer.allow_reuse_address = True
        httpd = HTTPServer(("127.0.0.1", SERVER_PORT), ComfyUnrealRequestHandler)
        httpd.serve_forever()

    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    _server_instance = t

    if unreal:
        unreal.register_slate_post_tick_callback(process_pending_imports_on_tick)
        unreal.log(f"[GameAssetMake Bridge] server v{SERVER_VERSION} active on port {SERVER_PORT} "
                   f"(importer hot-reloads per payload)")
