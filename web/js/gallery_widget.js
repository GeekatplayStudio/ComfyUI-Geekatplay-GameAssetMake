// =============================================================
// Geekatplay GameAssetMake — Interactive Batch Asset Gallery widget
// (c) Geekatplay Studio / Vladimir Chopine
// =============================================================
import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

// Load CSS dynamically
const link = document.createElement("link");
link.rel = "stylesheet";
link.type = "text/css";
link.href = new URL("../css/gallery.css", import.meta.url).href;
document.head.appendChild(link);

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

// =============================================================
// Node-definition cache + widget-value repair
//
// `widgets_values` in a saved workflow is a POSITIONAL array. Anything that
// changes a node's widget count or order between save and load shifts every
// value one slot along: combos land on a value that isn't in their option list
// (serialized as ""), an INT receives a boolean, a BOOLEAN receives a number.
// That is exactly the "Value not in list: engine: ''" /
// "Value 1 smaller than min of 1000: target_face_count" family of errors.
//
// Two things are done about it:
//   1. the presentation-only DOM widgets below all pass `serialize: false`, so
//      they never take a slot in that array in the first place;
//   2. values are validated against the live node definition after a workflow
//      loads and reset to their declared defaults when they don't fit, so a
//      workflow that was already saved in the corrupted state repairs itself
//      instead of failing validation on every queue.
// =============================================================
const NODE_DEFS = {};      // comfyClass -> nodeData (our nodes only)

function inputSpec(nodeData, name) {
    const inp = nodeData?.input || {};
    for (const section of ["required", "optional"]) {
        const spec = inp[section]?.[name];
        if (spec) return spec;
    }
    return null;
}

/**
 * Add a presentation-only DOM widget that stays OUT of `widgets_values`.
 *
 * `addDOMWidget(..., { serialize: false })` is not enough: LGraphNode.serialize
 * tests `widget.serialize === false` as an OWN PROPERTY on the widget, while
 * addDOMWidget only stores the flag under `widget.options`. So the widget was
 * serialized anyway, `widgets_values` came back one entry longer than the node
 * has real widgets, and the frontend then dropped the saved values entirely —
 * every field silently reverted, and combos that received a neighbour's value
 * ended up as "" ("Value not in list: engine: ''").
 *
 * The flag is set on both the widget and its options so this keeps working if a
 * later frontend switches to reading the option instead.
 */
function addPresentationWidget(node, name, type, element) {
    const widget = node.addDOMWidget(name, type, element, {
        serialize: false,
        getValue() { return ""; },
        setValue() {},
    });
    if (widget) {
        widget.serialize = false;
        if (widget.options) widget.options.serialize = false;
    }
    return widget;
}

// Only these produce a widget; anything else on an input is a link socket.
const SCALAR_WIDGET_TYPES = new Set(["INT", "FLOAT", "STRING", "BOOLEAN"]);

/**
 * The widget slots `widgets_values` is expected to hold, in order — the same
 * rule the Python-side workflow validator uses. A seed-like INT is followed by
 * its `control_after_generate` combo, which also occupies a slot.
 */
function expectedWidgetSlots(nodeData) {
    const inp = nodeData?.input || {};
    const slots = [];
    for (const section of ["required", "optional"]) {
        for (const [name, spec] of Object.entries(inp[section] || {})) {
            const type = spec[0];
            const opts = (spec.length > 1 && spec[1]) || {};
            if (opts.forceInput) continue;                     // link-only
            if (Array.isArray(type)) { slots.push(name); continue; }
            if (!SCALAR_WIDGET_TYPES.has(type)) continue;      // IMAGE, MESH, ...
            slots.push(name);
            if (type === "INT" && (name === "seed" || name === "noise_seed" || opts.control_after_generate)) {
                slots.push(`${name}.control_after_generate`);
            }
        }
    }
    return slots;
}

/**
 * Repair `widgets_values` arrays saved by an earlier build of this pack.
 *
 * Those builds serialized the presentation-only DOM widget, so the array came
 * out one entry longer than the node really has. The frontend responds to that
 * length mismatch by throwing the WHOLE array away — every field silently
 * reverts to its default, which is the "fields prefilled with wrong data"
 * symptom. Trimming the surplus trailing entries makes those workflows load
 * with the values they were saved with. Our DOM widgets are always appended
 * last, so the surplus is always at the end.
 */
function sanitizeGraphWidgetValues(graphData) {
    let repairedNodes = 0;
    for (const node of graphData?.nodes || []) {
        const def = NODE_DEFS[node.type];
        if (!def || !Array.isArray(node.widgets_values)) continue;
        const expected = expectedWidgetSlots(def).length;
        if (node.widgets_values.length <= expected) continue;
        const dropped = node.widgets_values.slice(expected);
        // Only ever drop padding, never real settings.
        if (!dropped.every(v => v === "" || v === null || v === undefined)) {
            console.warn(`[GameAssetMake] ${node.type}#${node.id}: widgets_values has `
                + `${node.widgets_values.length} entries, expected ${expected}, and the surplus `
                + `${JSON.stringify(dropped)} is not padding — leaving it alone.`);
            continue;
        }
        node.widgets_values = node.widgets_values.slice(0, expected);
        repairedNodes++;
    }
    if (repairedNodes) {
        console.log(`[GameAssetMake] Trimmed stale DOM-widget padding from ${repairedNodes} node(s) `
            + `so their saved field values load instead of reverting to defaults. `
            + `Save the workflow to make the fix permanent.`);
    }
}

function setWidgetValue(node, widget, value, runCallback = false) {
    widget.value = value;
    if (runCallback) {
        try { widget.callback?.(value, app.canvas, node, undefined, undefined); }
        catch (err) { console.warn("[GameAssetMake] widget callback failed", widget.name, err); }
    }
    node.setDirtyCanvas?.(true, true);
}

/**
 * Reset every widget whose value cannot possibly be valid back to the default
 * declared by the Python node. Returns the names that were repaired.
 * Only ever touches this pack's own nodes, whose combo lists are static.
 */
function repairWidgetValues(node) {
    const def = NODE_DEFS[node.comfyClass];
    if (!def || !node.widgets) return [];

    const repaired = [];
    for (const w of node.widgets) {
        const spec = inputSpec(def, w.name);
        if (!spec) continue;              // DOM widget, or seed's control_after_generate
        const type = spec[0];
        const opts = (spec.length > 1 && spec[1]) || {};
        const dflt = opts.default;

        if (Array.isArray(type)) {
            if (!type.includes(w.value)) {
                setWidgetValue(node, w, type.includes(dflt) ? dflt : type[0]);
                repaired.push(w.name);
            }
        } else if (type === "INT" || type === "FLOAT") {
            // `typeof true === "boolean"` — Number(true) would silently become 1,
            // which is how target_face_count ended up below its own minimum.
            let v = typeof w.value === "boolean" ? NaN : Number(w.value);
            if (!Number.isFinite(v)) v = dflt ?? opts.min ?? 0;
            if (opts.min !== undefined && v < opts.min) v = (dflt !== undefined && dflt >= opts.min) ? dflt : opts.min;
            if (opts.max !== undefined && v > opts.max) v = (dflt !== undefined && dflt <= opts.max) ? dflt : opts.max;
            if (type === "INT") v = Math.round(v);
            if (v !== w.value) { setWidgetValue(node, w, v); repaired.push(w.name); }
        } else if (type === "BOOLEAN") {
            if (typeof w.value !== "boolean") {
                setWidgetValue(node, w, typeof dflt === "boolean" ? dflt : !!w.value);
                repaired.push(w.name);
            }
        } else if (type === "STRING") {
            if (typeof w.value !== "string") {
                setWidgetValue(node, w, dflt != null ? String(dflt) : "");
                repaired.push(w.name);
            }
        }
    }
    return repaired;
}

function resetNodeToDefaults(node) {
    const def = NODE_DEFS[node.comfyClass];
    if (!def || !node.widgets) return 0;
    let n = 0;
    for (const w of node.widgets) {
        const spec = inputSpec(def, w.name);
        if (!spec) continue;
        const type = spec[0];
        const opts = (spec.length > 1 && spec[1]) || {};
        const dflt = Array.isArray(type) ? (opts.default ?? type[0]) : opts.default;
        if (dflt === undefined) continue;
        if (w.value !== dflt) { setWidgetValue(node, w, dflt, true); n++; }
    }
    return n;
}

app.registerExtension({
    name: "Geekatplay.GameAssetMake.WidgetIntegrity",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!String(nodeData?.category || "").startsWith("Geekatplay GameAssetMake")) return;
        NODE_DEFS[nodeData.name] = nodeData;

        // "Reset fields to defaults" / cache clearing, straight from the node menu
        const origMenu = nodeType.prototype.getExtraMenuOptions;
        nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
            origMenu?.apply(this, arguments);
            const node = this;
            options.push({
                content: "🧹 Reset fields to default values",
                callback: () => {
                    const n = resetNodeToDefaults(node);
                    console.log(`[GameAssetMake] ${node.comfyClass}: reset ${n} field(s) to defaults.`);
                },
            });
            options.push({
                content: "🧹 Clear this node's cached preview",
                callback: () => node.__gamClearCache?.(),
            });
        };
    },

    // Runs on the raw workflow JSON, before litegraph applies any of it.
    beforeConfigureGraph(graphData) {
        try { sanitizeGraphWidgetValues(graphData); }
        catch (err) { console.error("[GameAssetMake] widgets_values sanitize failed", err); }
    },

    // Runs once per node right after its saved widgets_values were applied.
    loadedGraphNode(node) {
        const repaired = repairWidgetValues(node);
        if (repaired.length) {
            console.warn(`[GameAssetMake] ${node.comfyClass}#${node.id}: invalid saved values for `
                + `${repaired.join(", ")} — reset to the node defaults. Save the workflow to keep the fix.`);
        }
    },
});

// Per-node UI state. A module-level singleton here meant the LAST gallery node
// created won — so with two workflow tabs open (or two Gallery nodes in one
// graph) the 3D-preview modal wrote its approval onto the wrong node.
const GALLERY_STATE = new WeakMap();   // GalleryApprovalNode -> { container }

/** Find the Gallery node that actually feeds this 3D Generator node. */
function galleryForGenerator(genNode) {
    const graph = genNode.graph || app.graph;
    const slot = (genNode.inputs || []).find(i => i.name === "approved_assets_json");
    if (slot?.link != null) {
        const link = graph.links?.[slot.link];
        const origin = link ? graph.getNodeById(link.origin_id) : null;
        if (origin?.comfyClass === "GalleryApprovalNode") return origin;
    }
    // Not wired (or wired through a reroute) — only guess when it's unambiguous.
    const candidates = (graph._nodes || []).filter(n => n.comfyClass === "GalleryApprovalNode");
    return candidates.length === 1 ? candidates[0] : null;
}

const GROUP_LABELS = {
    character: "👤 Characters (can be auto-rigged)",
    accessory: "🎒 Accessories & props",
    environment: "🧱 Environment kit (walls, floors, stairs…)",
};
const GROUP_ORDER = ["character", "accessory", "environment"];

function renderCards(container, items, awaiting) {
    const list = container.querySelector(".gameassetmake-asset-list-container");
    if (!list) return;
    if (!items || !items.length) {
        list.innerHTML = `<div style="font-size:12px; color:#888; text-align:center; padding:12px;">
            No assets received. Queue the workflow to generate concept images.</div>`;
        return;
    }

    const grouped = {};
    for (const it of items) (grouped[it.asset_group || "accessory"] ||= []).push(it);

    let html = "";
    if (awaiting) {
        html += `<div class="gameassetmake-banner">PAUSED — pick what becomes 3D, then press
            <b>Approve &amp; Continue</b>. Nothing is generated or spent until you do.</div>`;
    }

    for (const g of GROUP_ORDER) {
        const rows = grouped[g];
        if (!rows || !rows.length) continue;
        html += `<div class="gameassetmake-group" data-group="${g}">
            <span>${GROUP_LABELS[g] || g} — ${rows.length}</span>
            <span class="gameassetmake-group-actions">
              <button class="comfy-unreal-btn" data-act="group-all" data-group="${g}">all</button>
              <button class="comfy-unreal-btn" data-act="group-none" data-group="${g}">none</button>
            </span></div>`;

        html += rows.map(item => {
            // `v` is the batch signature: it changes whenever the concepts change,
            // so the browser is never allowed to reuse a previously rendered image.
            const imgUrl = item.filename
                ? api.apiURL(`/view?filename=${encodeURIComponent(item.filename)}`
                    + `&subfolder=${encodeURIComponent(item.subfolder || "")}`
                    + `&type=${encodeURIComponent(item.type || "temp")}`
                    + `&v=${encodeURIComponent(item.cache_key || "")}`)
                : "";
            const rigBlock = item.can_rig ? `
                <label><input type="checkbox" class="opt-rigging" ${item.include_rigging ? "checked" : ""}> Auto-Rig</label>
                <label>Rig:
                    <select class="opt-rigtype">
                        <option value="biped" ${item.rig_type === "biped" ? "selected" : ""}>Biped</option>
                        <option value="quadruped" ${item.rig_type === "quadruped" ? "selected" : ""}>Quadruped</option>
                        <option value="none" ${(!item.rig_type || item.rig_type === "none") ? "selected" : ""}>None</option>
                    </select>
                </label>` : `<span class="comfy-unreal-cat">no rigging for this type</span>`;
            return `
            <div class="comfy-unreal-asset-card" data-id="${esc(item.id)}" data-group="${g}">
                ${imgUrl ? `<img class="comfy-unreal-thumb" src="${imgUrl}" alt="${esc(item.name)}">` : ""}
                <div class="comfy-unreal-card-body">
                    <label class="comfy-unreal-card-title">
                        <input type="checkbox" class="comfy-unreal-checkbox" checked>
                        ${esc(item.name)} <span class="comfy-unreal-cat">(${esc(item.category)})</span>
                    </label>
                    <label><input type="checkbox" class="opt-texture" ${item.include_texture ? "checked" : ""}> PBR Texture</label>
                    ${rigBlock}
                </div>
            </div>`;
        }).join("");
    }
    list.innerHTML = html;
}

function updatePayload(node, container) {
    const cards = container.querySelectorAll(".comfy-unreal-asset-card");
    const selectionMap = { __batch__: container.dataset.batchSignature || "" };

    cards.forEach(card => {
        const rig = card.querySelector(".opt-rigging");
        const rigType = card.querySelector(".opt-rigtype");
        selectionMap[card.dataset.id] = {
            approved: card.querySelector(".comfy-unreal-checkbox").checked,
            include_texture: card.querySelector(".opt-texture").checked,
            include_rigging: rig ? rig.checked : false,
            rig_type: rigType ? rigType.value : "none",
        };
    });

    const payloadStr = JSON.stringify(selectionMap);
    container.dataset.payload = payloadStr;
    const overrideWidget = node.widgets?.find(w => w.name === "user_selection_override");
    if (overrideWidget) overrideWidget.value = payloadStr;
    return payloadStr;
}

function countChecked(container) {
    return container.querySelectorAll(".comfy-unreal-checkbox:checked").length;
}

app.registerExtension({
    name: "Geekatplay.GameAssetMake.Gallery",

    async nodeCreated(node) {
        if (node.comfyClass !== "GalleryApprovalNode") return;

        const widgetContainer = document.createElement("div");
        widgetContainer.className = "comfy-unreal-gallery-container";
        GALLERY_STATE.set(node, { container: widgetContainer });

        widgetContainer.innerHTML = `
            <div class="comfy-unreal-header">
                <div class="comfy-unreal-title">🖼️ Geekatplay GameAssetMake — Asset Gallery & 3D Options</div>
                <div class="comfy-unreal-actions">
                    <button class="comfy-unreal-btn" data-act="all">Select All</button>
                    <button class="comfy-unreal-btn" data-act="none">Deselect All</button>
                    <button class="comfy-unreal-btn comfy-unreal-btn-regen" data-act="regenerate">Regenerate Images</button>
                    <button class="comfy-unreal-btn comfy-unreal-btn-submit" data-act="approve">Approve &amp; Continue</button>
                </div>
            </div>
            <div class="comfy-unreal-asset-list gameassetmake-asset-list-container">
                <div style="font-size:12px; color:#888; text-align:center; padding:12px;">
                    Queue workflow to load generated 2D concept images for 3D selection.
                </div>
            </div>
        `;

        // Presentation-only state — never serialized (see addPresentationWidget).
        // The approval payload it holds lives in the real `user_selection_override`
        // widget, so nothing is lost by keeping this out of widgets_values.
        addPresentationWidget(node, "interactive_gallery", "gallery_ui", widgetContainer);

        widgetContainer.addEventListener("click", (ev) => {
            const act = ev.target?.dataset?.act;
            if (!act) return;

            if (act === "all" || act === "none") {
                widgetContainer.querySelectorAll(".comfy-unreal-checkbox")
                    .forEach(cb => cb.checked = (act === "all"));
                updatePayload(node, widgetContainer);

            } else if (act === "group-all" || act === "group-none") {
                const g = ev.target.dataset.group;
                widgetContainer.querySelectorAll(`.comfy-unreal-asset-card[data-group="${g}"] .comfy-unreal-checkbox`)
                    .forEach(cb => cb.checked = (act === "group-all"));
                updatePayload(node, widgetContainer);

            } else if (act === "approve") {
                if (!countChecked(widgetContainer)) {
                    alert("Nothing is selected. Tick at least one asset, or press "
                          + "'Regenerate Images' to create new concepts.");
                    return;
                }
                updatePayload(node, widgetContainer);
                app.queuePrompt(0);

            } else if (act === "regenerate") {
                // Start the concepts over: clear the approval so the run pauses
                // again on the NEW images, and roll fresh seeds upstream.
                widgetContainer.dataset.payload = "";
                widgetContainer.dataset.batchSignature = "";
                const ow = node.widgets?.find(w => w.name === "user_selection_override");
                if (ow) setWidgetValue(node, ow, "");
                // node.graph, not app.graph — with several workflow tabs open the
                // active graph may not be the one this Gallery node lives in.
                for (const n of ((node.graph || app.graph)._nodes || [])) {
                    if (n.comfyClass === "BatchConceptGeneratorNode" ||
                        n.comfyClass === "GameAssetPlannerNode") {
                        const seedW = n.widgets?.find(w => w.name === "seed");
                        if (seedW) setWidgetValue(n, seedW, Math.floor(Math.random() * 0xffffffff));
                    }
                }
                app.queuePrompt(0);
            }
        });

        widgetContainer.addEventListener("change", () => updatePayload(node, widgetContainer));

        // Drops the rendered cards, the stored approval and the batch signature.
        // Used by the node menu and whenever a fresh batch arrives, so a stale
        // approval from a previous run can never be re-submitted.
        node.__gamClearCache = () => {
            widgetContainer.dataset.payload = "";
            widgetContainer.dataset.batchSignature = "";
            const ow = node.widgets?.find(w => w.name === "user_selection_override");
            if (ow) setWidgetValue(node, ow, "");
            renderCards(widgetContainer, null, false);
        };

        // Render gallery cards when the node executes and reports its items
        const origOnExecuted = node.onExecuted;
        node.onExecuted = function (message) {
            origOnExecuted?.apply(this, arguments);
            const items = message?.gallery_items?.[0];
            if (items) {
                widgetContainer.dataset.batchSignature = message?.batch_signature?.[0] || "";
                const awaiting = !!(message?.awaiting_approval?.[0]);
                renderCards(widgetContainer, items, awaiting);
                updatePayload(node, widgetContainer);
            }
        };
    },
});

// ---------------------------------------------------------------
// 3D Generator results panel: lists every model returned by the API
// ---------------------------------------------------------------
app.registerExtension({
    name: "Geekatplay.GameAssetMake.ModelResults",

    async nodeCreated(node) {
        if (node.comfyClass !== "Unified3DGeneratorNode") return;

        const panel = document.createElement("div");
        panel.className = "comfy-unreal-gallery-container";
        panel.innerHTML = `
            <div class="comfy-unreal-header">
                <div class="comfy-unreal-title">🧊 Generated 3D Models</div>
            </div>
            <div class="comfy-unreal-asset-list gameassetmake-model-list">
                <div style="font-size:12px; color:#888; text-align:center; padding:12px;">
                    Run the workflow to see generated 3D models here.
                </div>
            </div>
        `;

        // Presentation only: keeping this out of widgets_values is what stops
        // engine / output_format / default_topology / target_face_count from
        // losing their saved values on reload.
        addPresentationWidget(node, "model_results", "results_ui", panel);

        const emptyMessage = `<div style="font-size:12px; color:#888; text-align:center; padding:12px;">
            Run the workflow to see generated 3D models here.</div>`;
        node.__gamClearCache = () => {
            const list = panel.querySelector(".gameassetmake-model-list");
            if (list) { list.innerHTML = emptyMessage; list.onclick = null; }
        };

        const origOnExecuted = node.onExecuted;
        node.onExecuted = function (message) {
            origOnExecuted?.apply(this, arguments);
            const models = message?.generated_models?.[0];
            if (!models) return;

            const list = panel.querySelector(".gameassetmake-model-list");
            if (!models.length) {
                list.innerHTML = `<div style="font-size:12px; color:#888; text-align:center; padding:12px;">No models were generated.</div>`;
                return;
            }

            list.innerHTML = models.map((m, i) => {
                const ok = String(m.status || "").includes("SUCCESS");
                return `
                <div class="comfy-unreal-asset-card gameassetmake-model-card" data-idx="${i}" title="Click to preview in 3D">
                    <div class="gameassetmake-status-dot ${ok ? "ok" : "fail"}"></div>
                    <div class="comfy-unreal-card-body">
                        <div class="comfy-unreal-card-title">${esc(m.name)}
                            <span class="comfy-unreal-cat">${esc(m.engine)} · ${esc(m.format)}</span>
                        </div>
                        <div class="gameassetmake-model-status ${ok ? "ok" : "fail"}">${esc(m.status)}</div>
                        <div class="gameassetmake-model-path" title="${esc(m.model_path)}">${esc(m.model_path)}</div>
                    </div>
                    <button class="comfy-unreal-btn" data-idx="${i}" data-act="preview">🧊 Preview 3D</button>
                </div>`;
            }).join("");

            list.onclick = (ev) => {
                const card = ev.target.closest(".gameassetmake-model-card");
                if (!card) return;
                const m = models[Number(card.dataset.idx)];
                if (m) openModelPreview(m, node);
            };
        };
    },
});

// ---------------------------------------------------------------
// Interactive 3D model preview modal (three.js) with
// "adjust parameters & regenerate this asset"
// ---------------------------------------------------------------
const THREE_CDN = "https://cdn.jsdelivr.net/npm/three@0.170.0";
let _threeModules = null;

async function loadThree() {
    if (_threeModules) return _threeModules;
    const [THREE, orbit, gltf, obj, fbx] = await Promise.all([
        import(`${THREE_CDN}/build/three.module.js/+esm`),
        import(`${THREE_CDN}/examples/jsm/controls/OrbitControls.js/+esm`),
        import(`${THREE_CDN}/examples/jsm/loaders/GLTFLoader.js/+esm`),
        import(`${THREE_CDN}/examples/jsm/loaders/OBJLoader.js/+esm`),
        import(`${THREE_CDN}/examples/jsm/loaders/FBXLoader.js/+esm`),
    ]);
    _threeModules = {
        THREE,
        OrbitControls: orbit.OrbitControls,
        GLTFLoader: gltf.GLTFLoader,
        OBJLoader: obj.OBJLoader,
        FBXLoader: fbx.FBXLoader,
    };
    return _threeModules;
}

function modelFileURL(path) {
    return api.apiURL(`/gameassetmake/model?path=${encodeURIComponent(path)}`)
        // api.apiURL prefixes /api which custom routes are also reachable under
        ;
}

function openModelPreview(model, generatorNode) {
    const isChar = model.asset_group === "character";
    const modal = document.createElement("div");
    modal.className = "comfy-unreal-modal";
    modal.innerHTML = `
      <div class="gameassetmake-preview-window">
        <div class="gameassetmake-preview-head">
            <div class="comfy-unreal-title">🧊 ${esc(model.name)}
                <span class="comfy-unreal-cat">${esc(model.engine)} · ${esc(model.format)} · ${esc(model.status)}</span>
            </div>
            <button class="comfy-unreal-btn" data-act="close">✕ Close</button>
        </div>
        <div class="gameassetmake-preview-body">
            <div class="gameassetmake-preview-canvas">
                <div class="gameassetmake-preview-msg">Loading 3D viewer…</div>
            </div>
            <div class="gameassetmake-preview-params">
                <div class="gameassetmake-preview-msg" style="text-align:left">
                    Don't like it? Adjust and regenerate just this asset:</div>
                <label>Engine
                    <select data-p="engine">
                        ${["tripo", "meshy", "hitem3d"].map(e =>
                            `<option value="${e}" ${model.engine === e ? "selected" : ""}>${e}</option>`).join("")}
                    </select></label>
                <label>Format
                    <select data-p="format">
                        ${["FBX", "GLB", "OBJ"].map(f =>
                            `<option value="${f}" ${model.format === f ? "selected" : ""}>${f}</option>`).join("")}
                    </select></label>
                <label>Topology
                    <select data-p="topology">
                        ${["quad", "triangle"].map(t =>
                            `<option value="${t}" ${model.topology === t ? "selected" : ""}>${t}</option>`).join("")}
                    </select></label>
                <label>Face count
                    <input type="number" data-p="faces" min="1000" max="100000" step="1000"
                           value="${Number(model.face_count) || 15000}"></label>
                <label><input type="checkbox" data-p="texture" ${model.include_texture ? "checked" : ""}> PBR Texture</label>
                ${isChar ? `
                <label><input type="checkbox" data-p="rig" ${model.include_rigging ? "checked" : ""}> Auto-Rig</label>
                <label>Rig type
                    <select data-p="rigtype">
                        ${["biped", "quadruped", "none"].map(r =>
                            `<option value="${r}" ${model.rig_type === r ? "selected" : ""}>${r}</option>`).join("")}
                    </select></label>` : ""}
                <button class="comfy-unreal-btn comfy-unreal-btn-regen" data-act="regen">
                    🔁 Regenerate This Asset</button>
                <div class="gameassetmake-preview-note" data-role="note"></div>
            </div>
        </div>
      </div>`;
    document.body.appendChild(modal);

    let cleanupViewer = null;
    const close = () => { cleanupViewer?.(); modal.remove(); };
    modal.addEventListener("click", (ev) => {
        if (ev.target === modal || ev.target.dataset?.act === "close") close();
        else if (ev.target.dataset?.act === "regen") regenerateSingleAsset(model, generatorNode, modal);
    });

    const canvasHost = modal.querySelector(".gameassetmake-preview-canvas");
    startViewer(canvasHost, model).then(fn => { cleanupViewer = fn; })
        .catch(err => {
            canvasHost.innerHTML = `<div class="gameassetmake-preview-msg">
                ⚠ Could not preview this file.<br>${esc(err?.message || err)}<br><br>
                Mock files and some FBX variants cannot be shown — the file itself is at:<br>
                <span style="font-size:10px">${esc(model.model_path)}</span></div>`;
        });
}

async function startViewer(host, model) {
    const { THREE, OrbitControls, GLTFLoader, OBJLoader, FBXLoader } = await loadThree();
    const url = modelFileURL(model.model_path);
    const ext = String(model.model_path).split(".").pop().toLowerCase();

    let object;
    if (ext === "glb" || ext === "gltf") {
        const gltf = await new GLTFLoader().loadAsync(url);
        object = gltf.scene;
    } else if (ext === "obj") {
        object = await new OBJLoader().loadAsync(url);
    } else if (ext === "fbx") {
        object = await new FBXLoader().loadAsync(url);
    } else {
        throw new Error(`Unsupported preview format: .${ext}`);
    }

    host.innerHTML = "";
    const w = host.clientWidth || 640, h = host.clientHeight || 480;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a1c23);
    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(3, 5, 4);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x88aaff, 0.5);
    fill.position.set(-4, 2, -3);
    scene.add(fill);

    // center + frame the model
    const box = new THREE.Box3().setFromObject(object);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    object.position.sub(center);
    scene.add(object);
    const radius = Math.max(size.x, size.y, size.z) || 1;

    const camera = new THREE.PerspectiveCamera(45, w / h, radius / 100, radius * 100);
    camera.position.set(radius * 1.4, radius * 0.9, radius * 1.4);

    const grid = new THREE.GridHelper(radius * 3, 20, 0x3a3a46, 0x2a2a33);
    grid.position.y = -size.y / 2;
    scene.add(grid);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    let alive = true;
    (function animate() {
        if (!alive) return;
        requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
    })();

    return () => {
        alive = false;
        controls.dispose();
        renderer.dispose();
        renderer.domElement.remove();
    };
}

function regenerateSingleAsset(model, generatorNode, modal) {
    const note = modal.querySelector('[data-role="note"]');
    const val = sel => modal.querySelector(`[data-p="${sel}"]`);

    // 1) push the tweaked parameters onto the 3D Generator node.
    //    Values are checked against the widget's own option list first: writing
    //    `wdg.value` blind is what let an out-of-list value (a Meshy-only format
    //    on a HiTem3D run, say) sit in the node and reach the server, where it
    //    fails validation as "Value not in list".
    const setW = (name, v) => {
        const wdg = generatorNode.widgets?.find(x => x.name === name);
        if (!wdg) return;
        const allowed = wdg.options?.values;
        if (Array.isArray(allowed) && !allowed.includes(v)) {
            console.warn(`[GameAssetMake] refusing to set ${name}=${JSON.stringify(v)} — `
                + `not one of ${JSON.stringify(allowed)}`);
            return;
        }
        setWidgetValue(generatorNode, wdg, v, true);
    };
    setW("engine", val("engine").value);
    setW("output_format", val("format").value);
    setW("default_topology", val("topology").value);
    setW("target_face_count", Number(val("faces").value) || 15000);

    // 2) approve ONLY this asset on the Gallery node (same batch signature,
    //    so the concept images are reused and just this mesh is regenerated).
    //    Resolved from THIS generator's own graph, so a second workflow tab
    //    can't receive the approval meant for this one.
    const galleryNode = galleryForGenerator(generatorNode);
    const gc = galleryNode ? GALLERY_STATE.get(galleryNode)?.container : null;
    const sig = gc?.dataset?.batchSignature;
    if (!gc || !sig) {
        note.textContent = galleryNode
            ? "⚠ No concept batch loaded on the Gallery node yet — queue the workflow first."
            : "⚠ Could not tell which Gallery node feeds this generator — regenerate from the Gallery instead.";
        return;
    }
    const selection = { __batch__: sig };
    selection[model.id] = {
        approved: true,
        include_texture: val("texture").checked,
        include_rigging: val("rig") ? val("rig").checked : false,
        rig_type: val("rigtype") ? val("rigtype").value : "none",
    };
    const payloadStr = JSON.stringify(selection);
    gc.dataset.payload = payloadStr;
    const ow = galleryNode.widgets?.find(w => w.name === "user_selection_override");
    if (ow) setWidgetValue(galleryNode, ow, payloadStr);

    note.textContent = `Queued: regenerating "${model.name}" only. Watch the queue — the new model will appear in the results list.`;
    app.queuePrompt(0);
    modal.remove();
}

// ---------------------------------------------------------------
// Unreal Bridge: imported-assets checklist with live confirmation
// polled back from the Unreal Editor
// ---------------------------------------------------------------
app.registerExtension({
    name: "Geekatplay.GameAssetMake.UnrealImportStatus",

    async nodeCreated(node) {
        if (node.comfyClass !== "UnrealEngineBridgeNode") return;

        const panel = document.createElement("div");
        panel.className = "comfy-unreal-gallery-container";
        panel.innerHTML = `
            <div class="comfy-unreal-header">
                <div class="comfy-unreal-title">⚡ Unreal Import Status</div>
            </div>
            <div class="gameassetmake-import-summary" style="font-size:12px; color:#888; padding:4px 0;">
                Queue the workflow — every asset sent to Unreal is listed here
                with its actual import result.</div>
            <div class="comfy-unreal-asset-list gameassetmake-import-list"></div>
        `;
        addPresentationWidget(node, "unreal_import_status", "status_ui", panel);

        const summary = panel.querySelector(".gameassetmake-import-summary");
        const list = panel.querySelector(".gameassetmake-import-list");
        let pollTimer = null;

        const idleSummary = "Queue the workflow — every asset sent to Unreal is listed here "
            + "with its actual import result.";
        node.__gamClearCache = () => {
            clearInterval(pollTimer);
            pollTimer = null;
            summary.textContent = idleSummary;
            summary.style.color = "#888";
            list.innerHTML = "";
        };
        // Without this the 2-second poll keeps running against a deleted node.
        const origOnRemoved = node.onRemoved;
        node.onRemoved = function () {
            clearInterval(pollTimer);
            pollTimer = null;
            origOnRemoved?.apply(this, arguments);
        };

        function renderRows(assets, resultsById, done) {
            list.innerHTML = assets.map(a => {
                const r = resultsById?.[a.id];
                let icon, cls, detail;
                if (r && r.imported) { icon = "✅"; cls = "ok"; detail = r.detail || "imported"; }
                else if (r && done) { icon = "❌"; cls = "fail"; detail = r.detail || "failed"; }
                else if (done) { icon = "⚠️"; cls = "fail"; detail = "no result reported"; }
                else { icon = "⏳"; cls = ""; detail = "waiting for Unreal…"; }
                return `
                <div class="comfy-unreal-asset-card">
                    <div style="font-size:16px; min-width:22px; text-align:center;">${icon}</div>
                    <div class="comfy-unreal-card-body">
                        <div class="comfy-unreal-card-title">${esc(a.name)}
                            <span class="comfy-unreal-cat">${esc(a.category || "")}</span></div>
                        <div class="gameassetmake-model-status ${cls}">${esc(detail)}</div>
                    </div>
                </div>`;
            }).join("");
        }

        const origOnExecuted = node.onExecuted;
        node.onExecuted = function (message) {
            origOnExecuted?.apply(this, arguments);
            const info = message?.unreal_import?.[0];
            if (!info) return;

            clearInterval(pollTimer);
            summary.textContent = info.delivery || "";
            summary.style.color = info.sent ? "#34ce57" : "#ff6b7a";

            if (!info.assets?.length) {
                list.innerHTML = `<div style="font-size:12px; color:#888; text-align:center; padding:12px;">
                    No assets in this batch.</div>`;
                return;
            }
            renderRows(info.assets, null, false);

            if (!info.sent || !info.trackable) {
                // no live confirmation channel — show what we know and stop
                renderRows(info.assets, null, !info.sent);
                return;
            }

            // Poll the bridge (via the ComfyUI proxy route) for real per-asset results
            const started = Date.now();
            pollTimer = setInterval(async () => {
                try {
                    const resp = await api.fetchApi(
                        `/gameassetmake/unreal_import_status?host=${encodeURIComponent(info.host)}`
                        + `&port=${info.port}&batch_id=${encodeURIComponent(info.batch_id)}`);
                    const data = await resp.json();
                    const byId = {};
                    for (const r of (data.results || [])) byId[r.id] = r;
                    renderRows(info.assets, byId, !!data.done);
                    if (data.done) {
                        clearInterval(pollTimer);
                        const okCount = (data.results || []).filter(r => r.imported).length;
                        summary.textContent = `Unreal import finished: ${okCount}/${info.assets.length} asset(s) imported.`;
                        summary.style.color = okCount === info.assets.length ? "#34ce57" : "#ffcf6b";
                    }
                } catch (e) { /* keep polling until timeout */ }
                if (Date.now() - started > 180000) {
                    clearInterval(pollTimer);
                    summary.textContent += " (stopped waiting after 3 min — check the Unreal Output Log)";
                }
            }, 2000);
        };
    },
});

// ---------------------------------------------------------------
// Engine Connection Check: online/offline status badge
// ---------------------------------------------------------------
app.registerExtension({
    name: "Geekatplay.GameAssetMake.ConnectionCheck",

    async nodeCreated(node) {
        if (node.comfyClass !== "EngineConnectionCheckNode") return;

        const badge = document.createElement("div");
        badge.className = "gameassetmake-conn-badge";
        badge.textContent = "Not checked yet — queue the workflow to verify the engine bridge.";

        addPresentationWidget(node, "connection_badge", "status_ui", badge);

        node.__gamClearCache = () => {
            badge.textContent = "Not checked yet — queue the workflow to verify the engine bridge.";
            badge.classList.remove("online", "offline");
        };

        const origOnExecuted = node.onExecuted;
        node.onExecuted = function (message) {
            origOnExecuted?.apply(this, arguments);
            const status = message?.connection_status?.[0]?.[0];
            if (!status) return;
            badge.textContent = status.message;
            badge.classList.toggle("online", !!status.online);
            badge.classList.toggle("offline", !status.online);
        };
    },
});
