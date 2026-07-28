# =============================================================
# Geekatplay GameAssetMake — Meshy API client (Image-to-3D, openapi v1)
# (c) Geekatplay Studio / Vladimir Chopine
# =============================================================
import os
import time
import json
import base64
import urllib.request
import urllib.error

MESHY_API_BASE = "https://api.meshy.ai/openapi/v1"


def encode_image_to_data_uri(image_path):
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    if ext == "jpg":
        ext = "jpeg"
    base64_str = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/{ext};base64,{base64_str}"


def submit_meshy_image_to_3d(api_key, image_path, topology="quad", target_poly_count=30000, enable_pbr=True):
    """
    Submits an Image-to-3D task to the Meshy API.
    Returns the task id.
    """
    data_uri = encode_image_to_data_uri(image_path)

    payload = {
        "image_url": data_uri,
        "enable_pbr": enable_pbr,
        "should_texture": True,
        "topology": topology,
        "target_polycount": target_poly_count,
        "should_remesh": True,
    }

    req = urllib.request.Request(
        f"{MESHY_API_BASE}/image-to-3d",
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
        raise RuntimeError(f"Meshy HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")

    task_id = data.get("result") or data.get("id")
    if task_id:
        return task_id
    raise RuntimeError(f"Meshy API Response Invalid: {data}")


def poll_meshy_task(api_key, task_id, timeout_sec=600, poll_interval=5):
    """
    Polls Meshy task status until SUCCEEDED, FAILED, or timeout.
    """
    start_time = time.time()

    while time.time() - start_time < timeout_sec:
        req = urllib.request.Request(
            f"{MESHY_API_BASE}/image-to-3d/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                status = data.get("status")

                if status == "SUCCEEDED":
                    return data
                elif status in ("FAILED", "EXPIRED", "CANCELED"):
                    raise RuntimeError(
                        f"Meshy Task {task_id} {status}: "
                        f"{(data.get('task_error') or {}).get('message', 'Unknown')}"
                    )
        except urllib.error.HTTPError:
            pass

        time.sleep(poll_interval)

    raise TimeoutError(f"Meshy Task {task_id} timed out after {timeout_sec}s")
