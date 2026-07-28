# =============================================================
# Geekatplay GameAssetMake — Hitem3D API client (EXPERIMENTAL)
# (c) Geekatplay Studio / Vladimir Chopine
#
# NOTE: Hitem3D's public API is newer and less standardized than
# Tripo3D/Meshy. Verify HITEM3D_API_BASE and the payload fields
# against your account's API documentation at https://hitem3d.ai
# before running live generations.
# =============================================================
import os
import time
import json
import base64
import urllib.request
import urllib.error

HITEM3D_API_BASE = "https://api.hitem3d.ai/v1"


def encode_image_to_data_uri(image_path):
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    if ext == "jpg":
        ext = "jpeg"
    base64_str = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/{ext};base64,{base64_str}"


def submit_hitem3d_image_to_3d(api_key, image_path, target_poly_count=30000, enable_pbr=True):
    """
    Submits an Image-to-3D task to the Hitem3D API. Returns the task id.
    """
    data_uri = encode_image_to_data_uri(image_path)

    payload = {
        "image_url": data_uri,
        "enable_pbr": enable_pbr,
        "target_polycount": target_poly_count,
    }

    req = urllib.request.Request(
        f"{HITEM3D_API_BASE}/image-to-3d",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Hitem3D HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")

    task_id = data.get("result") or data.get("id") or data.get("task_id")
    if task_id:
        return task_id
    raise RuntimeError(f"Hitem3D API Response Invalid: {data}")


def poll_hitem3d_task(api_key, task_id, timeout_sec=600, poll_interval=5):
    """
    Polls Hitem3D task status until finished, failed, or timeout.
    Returns the final task data dict (model URLs under 'model_urls' or 'output').
    """
    start_time = time.time()

    while time.time() - start_time < timeout_sec:
        req = urllib.request.Request(
            f"{HITEM3D_API_BASE}/image-to-3d/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                status = str(data.get("status", "")).upper()

                if status in ("SUCCEEDED", "SUCCESS", "COMPLETED"):
                    return data
                elif status in ("FAILED", "EXPIRED", "CANCELED", "ERROR"):
                    raise RuntimeError(f"Hitem3D Task {task_id} {status}: {data.get('error', 'Unknown')}")
        except urllib.error.HTTPError:
            pass

        time.sleep(poll_interval)

    raise TimeoutError(f"Hitem3D Task {task_id} timed out after {timeout_sec}s")
