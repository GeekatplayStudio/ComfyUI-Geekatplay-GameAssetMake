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

function renderCards(container, items) {
    const list = container.querySelector("#gameforge-asset-list-container");
    if (!items || !items.length) {
        list.innerHTML = `<div style="font-size:12px; color:#888; text-align:center; padding:12px;">
            No assets received. Queue the workflow to load concept images.</div>`;
        return;
    }

    list.innerHTML = items.map(item => {
        const imgUrl = item.filename
            ? api.apiURL(`/view?filename=${encodeURIComponent(item.filename)}&subfolder=${encodeURIComponent(item.subfolder || "")}&type=${encodeURIComponent(item.type || "temp")}`)
            : "";
        return `
        <div class="comfy-unreal-asset-card" data-id="${esc(item.id)}">
            ${imgUrl ? `<img class="comfy-unreal-thumb" src="${imgUrl}" alt="${esc(item.name)}">` : ""}
            <div class="comfy-unreal-card-body">
                <label class="comfy-unreal-card-title">
                    <input type="checkbox" class="comfy-unreal-checkbox" checked>
                    ${esc(item.name)} <span class="comfy-unreal-cat">(${esc(item.category)})</span>
                </label>
                <label>Engine:
                    <select class="opt-engine">
                        <option value="tripo" ${item.engine_target === "tripo" ? "selected" : ""}>Tripo3D</option>
                        <option value="meshy" ${item.engine_target === "meshy" ? "selected" : ""}>Meshy</option>
                    </select>
                </label>
                <label><input type="checkbox" class="opt-texture" ${item.include_texture ? "checked" : ""}> PBR Texture</label>
                <label><input type="checkbox" class="opt-rigging" ${item.include_rigging ? "checked" : ""}> Auto-Rig</label>
                <label>Rig:
                    <select class="opt-rigtype">
                        <option value="biped" ${item.rig_type === "biped" ? "selected" : ""}>Biped</option>
                        <option value="quadruped" ${item.rig_type === "quadruped" ? "selected" : ""}>Quadruped</option>
                        <option value="none" ${(!item.rig_type || item.rig_type === "none") ? "selected" : ""}>None</option>
                    </select>
                </label>
            </div>
        </div>`;
    }).join("");
}

function updatePayload(node, container) {
    const cards = container.querySelectorAll(".comfy-unreal-asset-card");
    const selectionMap = {};

    cards.forEach(card => {
        selectionMap[card.dataset.id] = {
            approved: card.querySelector(".comfy-unreal-checkbox").checked,
            engine_target: card.querySelector(".opt-engine").value,
            include_texture: card.querySelector(".opt-texture").checked,
            include_rigging: card.querySelector(".opt-rigging").checked,
            rig_type: card.querySelector(".opt-rigtype").value,
        };
    });

    const payloadStr = JSON.stringify(selectionMap);
    container.dataset.payload = payloadStr;

    const overrideWidget = node.widgets?.find(w => w.name === "user_selection_override");
    if (overrideWidget) {
        overrideWidget.value = payloadStr;
    }
    return payloadStr;
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
            } else if (act === "approve") {
                updatePayload(node, widgetContainer);
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
                renderCards(widgetContainer, items);
                updatePayload(node, widgetContainer);
            }
        };
    },
});
