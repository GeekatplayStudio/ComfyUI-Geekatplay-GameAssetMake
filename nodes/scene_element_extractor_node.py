# =============================================================
# Geekatplay GameAssetMake — Scene Element Extractor
# (c) Geekatplay Studio / Vladimir Chopine — https://www.geekatplay.com
#
# ONE image in -> a whole asset pack out. Detects the distinct
# objects in a single environment/concept image (buildings, benches,
# lamps, props...), cuts each one out onto a clean white square, and
# emits an asset manifest in the exact shape the Gallery Approval and
# 3D Generator nodes already consume. This is the "extract elements
# from a scene" pipeline: instead of planning assets from text, you
# start from a picture you already have.
#
# Two detection layers (same philosophy as the Single-Object Guardrail):
#   - vlm: a local Ollama vision model lists the objects it sees with
#     approximate bounding boxes and a category for each (best quality)
#   - heuristic: background-color estimation from the image border +
#     scipy connected-component analysis (free, instant, no services)
# When Ollama is unavailable the heuristic decides alone, so the node
# always produces something without any external dependency.
#
# Cutout matting: when OpenCV is importable each crop is matted with
# GrabCut (seeded by the detection box) so the object lands on pure
# white; otherwise the raw crop is used. cv2 is optional on purpose —
# this pack must load without it.
# =============================================================
import io
import json
import base64
import numpy as np
import torch

try:
    import requests
except ImportError:
    requests = None

try:
    from scipy import ndimage
    _HAVE_SCIPY = True
except ImportError:
    _HAVE_SCIPY = False

try:
    import cv2
    _HAVE_CV2 = True
except ImportError:
    _HAVE_CV2 = False

from .game_planner_node import classify_subject, title_case, CATEGORY_SPECS


def _tensor_to_png_b64(img_np_u8):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(img_np_u8).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def vlm_detect_objects(img_np_u8, max_assets, ollama_url, ollama_model,
                       timeout=(10, 300)):
    """
    Asks a local Ollama vision model to list the distinct extractable objects
    with normalized bounding boxes. Returns a list of
    {name, category, box(x0,y0,x1,y1 in 0..1)} or None on any failure.
    """
    if requests is None:
        return None
    prompt = (
        "You are preparing a game-asset extraction pass over this image. "
        f"List up to {max_assets} distinct, separate PHYSICAL OBJECTS that could each "
        "become an individual 3D game asset (buildings, furniture, lamps, vehicles, "
        "crates, plants, characters...). Skip the ground, sky, walls of the overall "
        "scene, and anything only partially visible at the frame edge. "
        "Reply ONLY with a JSON array; each item: "
        '{"name": "short object name", '
        '"type": "object|structure|vehicle|weapon|character|creature|vegetation", '
        '"box": [x_min, y_min, x_max, y_max]} '
        "where box coordinates are integers 0-1000 relative to image width/height."
    )
    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={
                "model": ollama_model,
                "prompt": prompt,
                "images": [_tensor_to_png_b64(img_np_u8)],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "")
        start, end = text.find("["), text.rfind("]") + 1
        if start < 0:  # some models wrap the array in an object
            obj = json.loads(text[text.find("{"):text.rfind("}") + 1])
            data = next((v for v in obj.values() if isinstance(v, list)), [])
        else:
            data = json.loads(text[start:end])

        out = []
        for item in data[:max_assets]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            box = item.get("box", None)
            if not name or not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            try:
                x0, y0, x1, y1 = (max(0.0, min(1.0, float(v) / 1000.0)) for v in box)
            except (TypeError, ValueError):
                continue
            if x1 - x0 < 0.01 or y1 - y0 < 0.01:
                continue
            out.append({"name": name, "type": str(item.get("type", "object")).lower(),
                        "box": (x0, y0, x1, y1)})
        return out or None
    except Exception as exc:
        print(f"[Element Extractor] Ollama detection failed ({exc}); "
              f"using the heuristic segmenter.")
        return None


def heuristic_detect_objects(arr01, max_assets, min_area_frac):
    """
    No-ML fallback: estimates the background color from the image border,
    thresholds everything sufficiently different from it, and labels the
    connected components. Works best on renders/concepts with a distinct
    ground/sky; on busy photos the VLM path is strongly recommended.
    Returns a list of {name, type, box} like vlm_detect_objects.
    """
    h, w = arr01.shape[:2]
    m = max(2, int(min(h, w) * 0.04))
    border = np.concatenate([
        arr01[:m, :].reshape(-1, 3), arr01[-m:, :].reshape(-1, 3),
        arr01[:, :m].reshape(-1, 3), arr01[:, -m:].reshape(-1, 3),
    ])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(arr01 - bg[None, None, :], axis=2)
    mask = dist > 0.18

    if not _HAVE_SCIPY:
        # Without scipy we cannot separate blobs; treat the biggest difference
        # region as one object so the pipeline still yields something.
        ys, xs = np.where(mask)
        if len(xs) < h * w * min_area_frac:
            return []
        return [{"name": "Extracted Object", "type": "object",
                 "box": (xs.min() / w, ys.min() / h, xs.max() / w, ys.max() / h)}]

    # close small gaps so one object does not shatter into fragments
    mask = ndimage.binary_closing(mask, structure=np.ones((5, 5)))
    labeled, n = ndimage.label(mask)
    if n == 0:
        return []
    objects = []
    slices = ndimage.find_objects(labeled)
    for i, sl in enumerate(slices, start=1):
        if sl is None:
            continue
        area = float((labeled[sl] == i).sum()) / float(h * w)
        if area < min_area_frac:
            continue
        y0, y1 = sl[0].start / h, sl[0].stop / h
        x0, x1 = sl[1].start / w, sl[1].stop / w
        objects.append({"name": f"Object {len(objects) + 1}", "type": "object",
                        "box": (x0, y0, x1, y1), "_area": area})
    objects.sort(key=lambda o: -o.get("_area", 0.0))
    return objects[:max_assets]


def grabcut_matte(crop_u8):
    """
    Mattes the crop's subject onto pure white with GrabCut seeded by an inset
    rectangle. Returns the matted crop, or None when cv2 is unavailable or
    GrabCut degenerates (keeps the raw crop in that case).
    """
    if not _HAVE_CV2:
        return None
    h, w = crop_u8.shape[:2]
    if h < 24 or w < 24:
        return None
    try:
        mask = np.zeros((h, w), np.uint8)
        inset_x, inset_y = max(2, int(w * 0.04)), max(2, int(h * 0.04))
        rect = (inset_x, inset_y, w - 2 * inset_x, h - 2 * inset_y)
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        cv2.grabCut(cv2.cvtColor(crop_u8, cv2.COLOR_RGB2BGR), mask, rect,
                    bgd, fgd, 4, cv2.GC_INIT_WITH_RECT)
        fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1, 0).astype(np.uint8)
        frac = float(fg.mean())
        if frac < 0.05 or frac > 0.98:
            return None  # matte collapsed or grabbed everything — keep raw crop
        fg3 = np.repeat(fg[:, :, None], 3, axis=2)
        return (crop_u8 * fg3 + 255 * (1 - fg3)).astype(np.uint8)
    except Exception:
        return None


# VLM object types -> planner categories (sizes/rig/collision follow from
# CATEGORY_SPECS exactly like planner-created assets)
_TYPE_TO_CATEGORY = {
    "character": "hero",
    "creature": "enemy",
    "vehicle": "vehicle",
    "weapon": "weapon",
    "structure": "structure",
    "vegetation": "environment_prop",
    "object": "environment_prop",
}


class SceneElementExtractorNode:
    """
    Splits one scene image into per-object concept crops + an asset manifest,
    feeding the same gallery -> 3D generator -> engine bridge chain the
    text-planned workflows use.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "max_assets": ("INT", {"default": 12, "min": 1, "max": 40, "step": 1,
                    "tooltip": "Upper limit on how many objects are extracted from the scene."}),
                "min_object_area_pct": ("FLOAT", {"default": 0.5, "min": 0.05, "max": 25.0,
                    "step": 0.05,
                    "tooltip": "Detections covering less than this % of the image are noise "
                               "and get dropped (heuristic detector and VLM boxes alike)."}),
                "crop_padding_pct": ("FLOAT", {"default": 8.0, "min": 0.0, "max": 30.0,
                    "step": 1.0,
                    "tooltip": "Extra margin added around each detection box before cropping, "
                               "so nothing gets clipped by a tight box."}),
                "output_size": ([512, 768, 1024], {"default": 1024,
                    "tooltip": "Each extracted object is centered on a white square canvas of "
                               "this size — the framing image-to-3D APIs expect."}),
                "matte_on_white": ("BOOLEAN", {"default": True,
                    "label_on": "GrabCut matte onto white (needs OpenCV)",
                    "label_off": "Raw crop, no matting"}),
                "detection": (["vlm+heuristic", "vlm", "heuristic"], {"default": "vlm+heuristic",
                    "tooltip": "vlm = Ollama vision model finds and NAMES the objects (best). "
                               "heuristic = local blob analysis, no services needed. "
                               "vlm+heuristic tries the VLM first and falls back."}),
                "art_style": ("STRING", {"default": "Stylized Low Poly",
                    "tooltip": "Written into each asset's prompt so guardrail retries and "
                               "regenerated concepts keep the pack's style."}),
                "scene_span_m": ("FLOAT", {"default": 60.0, "min": 1.0, "max": 2000.0,
                    "step": 1.0,
                    "tooltip": "Assumed real-world width of the pictured scene in meters. "
                               "Each object's position in the image becomes its world "
                               "placement, so the extracted pack re-assembles like the picture."}),
            },
            "optional": {
                "ollama_url": ("STRING", {"default": "http://127.0.0.1:11434"}),
                "ollama_model": ("STRING", {"default": "gemma3:12b"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "INT")
    RETURN_NAMES = ("element_images", "asset_manifest_json", "extraction_report_json", "element_count")
    FUNCTION = "extract_elements"
    CATEGORY = "Geekatplay GameAssetMake/Extractor"

    def extract_elements(self, image, max_assets=12, min_object_area_pct=0.5,
                         crop_padding_pct=8.0, output_size=1024, matte_on_white=True,
                         detection="vlm+heuristic", art_style="Stylized Low Poly",
                         scene_span_m=60.0,
                         ollama_url="http://127.0.0.1:11434", ollama_model="gemma3:12b"):
        arr01 = image[0].cpu().numpy()  # [H, W, C] in 0..1 — first image of the batch
        h, w = arr01.shape[:2]
        img_u8 = np.clip(arr01 * 255.0, 0, 255).astype(np.uint8)
        min_area_frac = float(min_object_area_pct) / 100.0

        detections, detector_used = None, "heuristic"
        if detection in ("vlm", "vlm+heuristic"):
            detections = vlm_detect_objects(img_u8, max_assets, ollama_url, ollama_model)
            if detections:
                detector_used = "vlm"
                # apply the same minimum-area floor the heuristic uses
                detections = [d for d in detections
                              if (d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1])
                              >= min_area_frac]
        if not detections and detection in ("heuristic", "vlm+heuristic"):
            detections = heuristic_detect_objects(arr01, max_assets, min_area_frac)
            detector_used = "heuristic"
        if not detections:
            raise RuntimeError(
                "Scene Element Extractor found no objects. Lower min_object_area_pct, "
                "or use 'vlm' detection with Ollama running (a vision model like "
                "gemma3:12b) — busy photographs need the VLM to separate objects."
            )

        detections = detections[:max_assets]
        print(f"[Element Extractor] {len(detections)} object(s) found via {detector_used}.")

        pad = float(crop_padding_pct) / 100.0
        size = int(output_size)
        canvas_frames, manifest, report = [], [], []

        for idx, det in enumerate(detections, start=1):
            x0, y0, x1, y1 = det["box"]
            bw, bh = x1 - x0, y1 - y0
            px0 = max(0, int((x0 - bw * pad) * w))
            py0 = max(0, int((y0 - bh * pad) * h))
            px1 = min(w, int((x1 + bw * pad) * w))
            py1 = min(h, int((y1 + bh * pad) * h))
            crop = img_u8[py0:py1, px0:px1]
            if crop.size == 0:
                continue

            matted = grabcut_matte(crop) if matte_on_white else None
            final_crop = matted if matted is not None else crop

            # fit the crop into a white square canvas, centered
            from PIL import Image
            pil = Image.fromarray(final_crop)
            scale = min(size * 0.9 / pil.width, size * 0.9 / pil.height)
            pil = pil.resize((max(1, int(pil.width * scale)),
                              max(1, int(pil.height * scale))), Image.LANCZOS)
            canvas = Image.new("RGB", (size, size), (255, 255, 255))
            canvas.paste(pil, ((size - pil.width) // 2, (size - pil.height) // 2))
            canvas_frames.append(torch.from_numpy(
                np.asarray(canvas).astype(np.float32) / 255.0))

            name = title_case(det["name"])
            category = _TYPE_TO_CATEGORY.get(det.get("type", "object"))
            if category is None or category not in CATEGORY_SPECS:
                category = classify_subject(name)
            spec = CATEGORY_SPECS.get(category, CATEGORY_SPECS["environment_prop"])
            group = spec.get("group", "accessory")

            # Estimate real size from the object's share of the pictured scene.
            # Rough by design — the Placement Manager and the engine importer
            # rescale from target_size_m against measured mesh bounds anyway.
            est_w = max(0.2, round(bw * scene_span_m, 1))
            est_h = max(0.2, round(bh * scene_span_m * 0.75, 1))
            target_size = [est_w, est_w, est_h]

            # World placement mirrors the picture: image X spreads across the
            # span, image Y (depth in a typical eye-level shot) becomes world Y.
            cx = ((x0 + x1) / 2.0 - 0.5) * scene_span_m
            cy = (0.5 - (y0 + y1) / 2.0) * scene_span_m * 0.5

            manifest.append({
                "id": f"asset_{idx:02d}",
                "name": name,
                "category": category,
                "asset_group": group,
                "prompt": (f"single {name}, one object only, 3/4 view facing the camera, "
                           f"isolated on pure white background, {art_style} style 3D game "
                           f"asset concept art, production ready, no text, no reference sheet"),
                "engine_target": "tripo",
                "rig_type": "none",
                "include_texture": True,
                "include_rigging": False,
                "target_size_m": target_size,
                "scale_override": [1.0, 1.0, 1.0],
                "collision_type": spec["collision"],
                "placement_role": ("building" if category == "structure" else
                                   "character" if group == "character" else "prop"),
                "world_placement_offset": [round(cx * 100.0, 1), round(cy * 100.0, 1),
                                           round(est_h * 100.0 / 2.0, 1)],
                "world_rotation_yaw": 0.0,
                "source": "scene_extraction",
            })
            report.append({
                "index": idx, "name": name, "category": category,
                "detector": detector_used,
                "box_px": [px0, py0, px1, py1],
                "matted": matted is not None,
                "estimated_size_m": target_size,
            })
            print(f"[Element Extractor]   [{idx}/{len(detections)}] {name} "
                  f"({category}, ~{est_w}x{est_h} m"
                  f"{', matted' if matted is not None else ''})")

        if not canvas_frames:
            raise RuntimeError("Scene Element Extractor: every detection produced an "
                               "empty crop — check min_object_area_pct.")

        result = torch.stack(canvas_frames, dim=0)
        if not _HAVE_CV2 and matte_on_white:
            print("[Element Extractor] OpenCV not installed — crops are NOT matted "
                  "onto white (pip install opencv-python to enable GrabCut matting).")

        return {
            "ui": {"extraction_report": [report]},
            "result": (result, json.dumps(manifest, indent=2),
                       json.dumps(report, indent=2), len(manifest)),
        }
