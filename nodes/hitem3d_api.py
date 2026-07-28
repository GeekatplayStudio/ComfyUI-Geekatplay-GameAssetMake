# =============================================================
# Geekatplay GameAssetMake — HiTem3D API client
# (c) Geekatplay Studio / Vladimir Chopine
#
# Ported from ComfyUI-Blender-Toolbox (same author). HiTem3D uses
# TWO keys — an Access Key and a Secret Key — exchanged via Basic
# auth for a 24h bearer token. Stored key format: "AccessKey:SecretKey".
# =============================================================
import os
import time
import json
import base64
import requests

HITEM3D_API_BASE = "https://api.hitem3d.ai"

# format codes per HiTem3D API
_FMT_MAP = {"obj": 1, "glb": 2, "stl": 3, "fbx": 4, "usdz": 5}

_token_cache = {"token": None, "expires_at": 0, "keypair": None}


def _get_token(access_key, secret_key):
    now = time.time()
    keypair = (access_key, secret_key)
    if (_token_cache["token"] and _token_cache["keypair"] == keypair
            and now < _token_cache["expires_at"] - 3600):
        return _token_cache["token"]

    credentials = base64.b64encode(f"{access_key}:{secret_key}".encode()).decode()
    resp = requests.post(
        f"{HITEM3D_API_BASE}/open-api/v1/auth/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "Accept": "*/*",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"HiTem3D token request failed: {data.get('msg', 'Unknown error')}")

    _token_cache.update({
        "token": data["data"]["accessToken"],
        "expires_at": now + 24 * 3600,
        "keypair": keypair,
    })
    return _token_cache["token"]


def split_keypair(api_key):
    """'AccessKey:SecretKey' -> (access, secret). Raises with a clear message otherwise."""
    if not api_key or ":" not in api_key:
        raise RuntimeError(
            "HiTem3D needs TWO keys in the form 'AccessKey:SecretKey' "
            "(ak_xxx:sk_xxx). Save them once with remember_keys enabled."
        )
    access_key, secret_key = api_key.split(":", 1)
    return access_key.strip(), secret_key.strip()


def submit_hitem3d_image_to_3d(api_key, image_path, target_poly_count=1000000, enable_pbr=True,
                               model="hitem3dv2.1", resolution="1536fast", output_format="glb"):
    """
    Submits an Image-to-3D task to HiTem3D. Returns the task id.
    """
    access_key, secret_key = split_keypair(api_key)
    token = _get_token(access_key, secret_key)

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    data = {
        "request_type": "3",  # all-in-one: geometry + texture
        "model": model,
        "resolution": str(resolution),
        "face": str(max(100000, int(target_poly_count))),
        "format": str(_FMT_MAP.get(output_format.lower(), 2)),
    }
    if model in {"hitem3dv2.0", "hitem3dv2.1", "scene-portraitv2.0", "scene-portraitv2.1"}:
        data["pbr"] = "1" if enable_pbr else "0"

    resp = requests.post(
        f"{HITEM3D_API_BASE}/open-api/v1/submit-task",
        headers={"Authorization": f"Bearer {token}", "Accept": "*/*"},
        files=[("images", ("front.png", image_bytes, "image/png"))],
        data=data,
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HiTem3D submit failed: {resp.text[:400]}")

    result = resp.json()
    if result.get("code") == 200:
        return result["data"]["task_id"]

    msg = result.get("msg", "Unknown error")
    if "balance is not enough" in msg.lower():
        msg = "Insufficient balance in HiTem3D account."
    raise RuntimeError(f"HiTem3D error ({result.get('code')}): {msg}")


def poll_hitem3d_task(api_key, task_id, timeout_sec=900, poll_interval=5):
    """
    Polls a HiTem3D task until success/failed/timeout. Returns the task data dict.
    """
    access_key, secret_key = split_keypair(api_key)
    start = time.time()

    while time.time() - start < timeout_sec:
        token = _get_token(access_key, secret_key)
        try:
            resp = requests.get(
                f"{HITEM3D_API_BASE}/open-api/v1/query-task",
                headers={"Authorization": f"Bearer {token}", "Accept": "*/*"},
                params={"task_id": task_id},
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") != 200:
                raise RuntimeError(f"HiTem3D query error: {result.get('msg')}")

            task = result["data"]
            state = str(task.get("state", "")).lower()
            if state == "success":
                return task
            if state == "failed":
                raise RuntimeError(f"HiTem3D task {task_id} failed")
        except requests.RequestException as exc:
            print(f"[HiTem3D] transient poll error: {exc}")

        time.sleep(poll_interval)

    raise TimeoutError(f"HiTem3D task {task_id} timed out after {timeout_sec}s")


def extract_model_url(task_data):
    """Finds the model download URL in a completed task response."""
    for key in ("url", "model_url", "mesh_url", "file_url"):
        if task_data.get(key):
            return task_data[key]
    return None


def download_file(url, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    partial = output_path + ".part"
    try:
        with requests.get(url, stream=True, timeout=(15, 300)) as resp:
            resp.raise_for_status()
            with open(partial, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        if not os.path.getsize(partial):
            raise RuntimeError("HiTem3D downloaded an empty model file")
        os.replace(partial, output_path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    return output_path
