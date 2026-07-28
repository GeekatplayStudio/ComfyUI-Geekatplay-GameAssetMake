"""
Workflow connection validator for GameAssetMake.
Checks every workflow JSON against the LIVE ComfyUI /object_info:
  - links reference existing nodes and valid slots
  - every required input is satisfied (link or widget value)
  - link types are compatible
  - widgets_values count matches the node definition (incl. seed control_after_generate)
"""
import json
import glob
import os
import sys
import urllib.request

SRV = "http://127.0.0.1:8188"
WF_DIR = r"O:\ComfyUI\custom_nodes\ComfyUI-Geekatplay-GameAssetMake\workflows"

CONNECTION_TYPES = {"MODEL", "CLIP", "VAE", "CLIP_VISION", "CLIP_VISION_OUTPUT",
                    "CONDITIONING", "LATENT", "IMAGE", "MASK", "VOXEL", "MESH",
                    "AUDIO", "NOISE", "SAMPLER", "SIGMAS", "GUIDER", "CONTROL_NET"}

info = json.loads(urllib.request.urlopen(f"{SRV}/object_info", timeout=60).read())


def node_spec(ntype):
    return info.get(ntype)


def expected_widgets(ntype):
    """Widget slots in order, with +1 marker after any seed-like INT."""
    spec = node_spec(ntype)
    if spec is None:
        return None
    out = []
    inp = spec.get("input", {})
    for section in ("required", "optional"):
        for name, defn in inp.get(section, {}).items():
            t = defn[0]
            opts = defn[1] if len(defn) > 1 else {}
            if isinstance(opts, dict) and opts.get("forceInput"):
                continue  # link-only
            if isinstance(t, list):
                out.append((name, "COMBO"))
            elif t in CONNECTION_TYPES:
                continue  # connection input, no widget
            else:
                out.append((name, t))
                if t == "INT" and (name in ("seed", "noise_seed") or
                                   (isinstance(opts, dict) and opts.get("control_after_generate"))):
                    out.append((name + ".control_after_generate", "COMBO"))
    return out


def input_defn(ntype, name):
    spec = node_spec(ntype)
    if not spec:
        return None
    inp = spec.get("input", {})
    for section in ("required", "optional"):
        if name in inp.get(section, {}):
            return section, inp[section][name]
    return None


total_problems = 0
for path in sorted(glob.glob(os.path.join(WF_DIR, "*.json"))):
    wf = json.load(open(path, encoding="utf-8"))
    fname = os.path.basename(path)
    problems = []
    nodes = {n["id"]: n for n in wf["nodes"]}

    # --- link table integrity ---
    links_by_id = {}
    for l in wf["links"]:
        lid, src, s_slot, dst, d_slot, ltype = l
        links_by_id[lid] = l
        if src not in nodes:
            problems.append(f"link {lid}: source node {src} does not exist")
            continue
        if dst not in nodes:
            problems.append(f"link {lid}: dest node {dst} does not exist")
            continue
        souts = nodes[src].get("outputs", [])
        if s_slot >= len(souts):
            problems.append(f"link {lid}: {nodes[src]['type']}#{src} has no output slot {s_slot}")
        else:
            otype = souts[s_slot].get("type", "*")
            if otype not in ("*", ltype) and ltype not in ("*",):
                problems.append(f"link {lid}: type mismatch {nodes[src]['type']}#{src}.{otype} -> {ltype}")
        dins = nodes[dst].get("inputs", [])
        if d_slot >= len(dins):
            problems.append(f"link {lid}: {nodes[dst]['type']}#{dst} has no input slot {d_slot}")
        elif dins[d_slot].get("link") != lid:
            problems.append(f"link {lid}: {nodes[dst]['type']}#{dst} input slot {d_slot} "
                            f"records link {dins[d_slot].get('link')} instead")

    for n in wf["nodes"]:
        ntype, nid = n["type"], n["id"]
        if ntype in ("Note", "MarkdownNote", "Reroute", "PrimitiveNode"):
            continue  # frontend-only virtual nodes, never in /object_info
        spec = node_spec(ntype)
        if spec is None:
            problems.append(f"{ntype}#{nid}: node type NOT REGISTERED in ComfyUI")
            continue

        # every declared input's link must exist in the link table
        linked_names = set()
        for i, d in enumerate(n.get("inputs", [])):
            if d.get("link") is not None:
                if d["link"] not in links_by_id:
                    problems.append(f"{ntype}#{nid}: input '{d['name']}' references "
                                    f"missing link {d['link']}")
                else:
                    linked_names.add(d["name"])

        # output slots must match the definition count
        defn_outs = spec.get("output", [])
        if len(n.get("outputs", [])) > len(defn_outs):
            problems.append(f"{ntype}#{nid}: declares {len(n['outputs'])} outputs, "
                            f"definition has {len(defn_outs)}")

        # required inputs: link or widget value must cover them
        widgets = expected_widgets(ntype)
        widget_names = {w[0] for w in widgets}
        for name, defn in spec.get("input", {}).get("required", {}).items():
            t = defn[0]
            opts = defn[1] if len(defn) > 1 else {}
            is_conn = (not isinstance(t, list)) and (t in CONNECTION_TYPES or
                      (isinstance(opts, dict) and opts.get("forceInput")))
            if is_conn and name not in linked_names:
                problems.append(f"{ntype}#{nid}: REQUIRED input '{name}' ({t}) is NOT CONNECTED")
            if not is_conn and name not in widget_names and name not in linked_names:
                problems.append(f"{ntype}#{nid}: required '{name}' has neither widget nor link")

        # widget count: converted-to-input widgets still occupy a widgets_values slot
        wv = n.get("widgets_values", [])
        if isinstance(wv, list) and len(wv) != len(widgets):
            problems.append(f"{ntype}#{nid}: widgets_values has {len(wv)} entries, "
                            f"definition expects {len(widgets)} "
                            f"({[w[0] for w in widgets]})")

    status = "OK " if not problems else "FAIL"
    print(f"[{status}] {fname} ({len(wf['nodes'])} nodes, {len(wf['links'])} links)")
    for p in problems:
        print(f"       - {p}")
    total_problems += len(problems)

print()
print(f"TOTAL PROBLEMS: {total_problems}")
sys.exit(1 if total_problems else 0)
