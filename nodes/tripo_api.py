# =============================================================
# Geekatplay GameAssetMake — Tripo3D API v2 client
# (c) Geekatplay Studio / Vladimir Chopine
# =============================================================
import os
import time
import json
import uuid
import urllib.request
import urllib.error

TRIPO_API_BASE = "https://api.tripo3d.ai/v2/openapi"


def upload_image_to_tripo(api_key, image_path):
    """
    Uploads a local image to the Tripo3D STS file server via multipart/form-data.
    Returns the image file token to reference in task submissions.
    """
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    if ext == "jpg":
        ext = "jpeg"

    boundary = uuid.uuid4().hex
    filename = os.path.basename(image_path)
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: image/{ext}\r\n\r\n".encode(),
        image_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ])

    req = urllib.request.Request(
        f"{TRIPO_API_BASE}/upload",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Tripo Upload HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")

    if data.get("code") == 0 and "data" in data:
        return data["data"]["image_token"], ext
    raise RuntimeError(f"Tripo Upload Error: {data.get('message', data)}")


def submit_tripo_image_to_3d(api_key, image_path, quad=True, face_limit=10000, pbr=True, texture_quality="standard"):
    """
    Submits an image_to_model task to Tripo3D API v2.
    """
    file_token, ext = upload_image_to_tripo(api_key, image_path)

    payload = {
        "type": "image_to_model",
        "file": {
            "type": ext,
            "file_token": file_token,
        },
        "model_version": "v2.5-20250123",
        "quad": quad,
        "face_limit": face_limit,
        "texture": True,
        "pbr": pbr,
        "texture_quality": texture_quality,
    }

    return _submit_tripo_task(api_key, payload)


def submit_tripo_rigging(api_key, original_task_id, rig_type="biped"):
    """
    Submits an animate_rig task to Tripo3D API v2 to auto-rig a character model.
    """
    payload = {
        "type": "animate_rig",
        "original_model_task_id": original_task_id,
        "out_format": "fbx",
    }
    return _submit_tripo_task(api_key, payload)


def _submit_tripo_task(api_key, payload):
    req = urllib.request.Request(
        f"{TRIPO_API_BASE}/task",
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
        raise RuntimeError(f"Tripo HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")

    if data.get("code") == 0 and "data" in data:
        return data["data"]["task_id"]
    raise RuntimeError(f"Tripo API Error: {data.get('message', data)}")


def poll_tripo_task(api_key, task_id, timeout_sec=600, poll_interval=5):
    """
    Polls Tripo3D task status until success, failure, or timeout.
    """
    start_time = time.time()

    while time.time() - start_time < timeout_sec:
        req = urllib.request.Request(
            f"{TRIPO_API_BASE}/task/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0 and "data" in data:
                    task_data = data["data"]
                    status = task_data.get("status")

                    if status == "success":
                        return task_data
                    elif status in ("failed", "cancelled", "banned", "expired"):
                        raise RuntimeError(f"Tripo Task {task_id} {status}: {task_data.get('error', 'Unknown')}")
        except urllib.error.HTTPError:
            pass

        time.sleep(poll_interval)

    raise TimeoutError(f"Tripo Task {task_id} timed out after {timeout_sec}s")


def download_file(url, output_path):
    """
    Downloads a file from a URL to a local path.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    urllib.request.urlretrieve(url, output_path)
    return output_path
