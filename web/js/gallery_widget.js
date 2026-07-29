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

const GROUP_LABELS = {
    character: "👤 Characters (can be auto-rigged)",
    accessory: "🎒 Accessories & props",
    environment: "🧱 Environment kit (walls, floors, stairs…)",
};
const GROUP_ORDER = ["character", "accessory", "environment"];

function renderCards(container, items, awaiting) {
    const list = container.querySelector("#gameforge-asset-list-container");
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
            const imgUrl = item.filename
                ? api.apiURL(`/view?filename=${encodeURIComponent(item.filename)}&subfolder=${encodeURIComponent(item.subfolder || "")}&type=${encodeURIComponent(item.type || "temp")}`)
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
            <div class="comfy-unreal-asset-list" id="gameforge-asset-list-container">
                <div style="font-size:12px; color:#888; text-align:center; padding:12px;">
                    Queue workflow to load generated 2D concept images for 3D selection.
                </div>
            </div>
        `;

        node.addDOMWidget("interactive_gallery", "gallery_ui", widgetContainer, {
            // This is presentation-only state. Serializing it changes the
            // positional widgets_values array and can corrupt the real inputs
            // when the workflow is opened by another frontend version.
            serialize: false,
            getValue() { return widgetContainer.dataset.payload || ""; },
            setValue(val) { widgetContainer.dataset.payload = val; },
        });

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
                const ow = node.widgets?.find(w => w.name === "user_selection_override");
                if (ow) ow.value = "";
                for (const n of app.graph._nodes) {
                    if (n.comfyClass === "BatchConceptGeneratorNode" ||
                        n.comfyClass === "GameAssetPlannerNode") {
                        const seedW = n.widgets?.find(w => w.name === "seed");
                        if (seedW) seedW.value = Math.floor(Math.random() * 0xffffffff);
                    }
                }
                app.queuePrompt(0);
            }
        });

        widgetContainer.addEventListener("change", () => updatePayload(node, widgetContainer));

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

        node.addDOMWidget("model_results", "results_ui", panel, {
            getValue() { return ""; },
            setValue() {},
        });

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

            list.innerHTML = models.map(m => {
                const ok = String(m.status || "").includes("SUCCESS");
                return `
                <div class="comfy-unreal-asset-card">
                    <div class="gameassetmake-status-dot ${ok ? "ok" : "fail"}"></div>
                    <div class="comfy-unreal-card-body">
                        <div class="comfy-unreal-card-title">${esc(m.name)}
                            <span class="comfy-unreal-cat">${esc(m.engine)} · ${esc(m.format)}</span>
                        </div>
                        <div class="gameassetmake-model-status ${ok ? "ok" : "fail"}">${esc(m.status)}</div>
                        <div class="gameassetmake-model-path" title="${esc(m.model_path)}">${esc(m.model_path)}</div>
                    </div>
                </div>`;
            }).join("");
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

        node.addDOMWidget("connection_badge", "status_ui", badge, {
            getValue() { return ""; },
            setValue() {},
        });

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
