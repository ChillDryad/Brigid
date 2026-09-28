import { BaseApp } from "./baseApp.js";
import { registry } from "../registry.js";

const percent = (used, total) => total ? Math.max(0, Math.min(100, (used / total) * 100)) : null;
const number = (value, suffix = "") => value === null || value === undefined ? "—" : `${Math.round(value)}${suffix}`;

export class GpuStatsApp extends BaseApp {
    async render() {
        return `
            <div class="app-content app-type-gpu">
                <div class="gpu-header"><span><i class="fa-solid fa-microchip"></i> GPU</span><span class="gpu-status">CONNECTING</span></div>
                <div class="gpu-grid">
                    <div><span>UTIL</span><strong data-gpu="util">—</strong></div>
                    <div><span>VRAM</span><strong data-gpu="vram">—</strong></div>
                    <div><span>TEMP</span><strong data-gpu="temp">—</strong></div>
                </div>
                <div class="gpu-name" data-gpu="name">NVIDIA telemetry</div>
            </div>`;
    }

    onMount(el, app) {
        const interval = Math.max(5000, Number(app.data.interval) || 15000);
        const update = async () => {
            if (!el.isConnected) return;
            const status = el.querySelector(".gpu-status");
            try {
                const response = await fetch("/api/gpu/stats", { credentials: "same-origin" });
                if (!response.ok) throw new Error(response.status === 503 ? "SETUP" : "OFFLINE");
                const { summary, gpus } = await response.json();
                const vram = percent(summary.vramUsedMiB, summary.vramTotalMiB);
                el.querySelector('[data-gpu="util"]').textContent = number(summary.utilization, "%");
                el.querySelector('[data-gpu="vram"]').textContent = number(vram, "%");
                el.querySelector('[data-gpu="temp"]').textContent = number(summary.temperatureC, "°C");
                el.querySelector('[data-gpu="name"]').textContent = gpus.length === 1 ? gpus[0].name : `${gpus.length} GPUs`;
                status.textContent = "LIVE";
                status.classList.add("live");
            } catch (error) {
                status.textContent = error.message;
                status.classList.remove("live");
            }
        };
        update();
        setInterval(update, interval);
    }
}

registry.register("gpu-stats", GpuStatsApp, {
    label: "NVIDIA GPU Stats",
    category: "data",
    defaultSize: { cols: 2, rows: 1 },
    settings: [{ name: "interval", label: "Refresh Interval (ms)", type: "text", defaultValue: "15000" }],
    css: `
        .app-type-gpu { height:100%; padding:10px; display:flex; flex-direction:column; gap:9px; color:inherit; }
        .gpu-header { display:flex; align-items:center; gap:7px; border-bottom:1px solid var(--border-dim); padding-bottom:7px; font-size:.78rem; font-weight:700; letter-spacing:.08em; }
        .gpu-header i { color:var(--brand-tertiary); margin-right:5px; }
        .gpu-status { margin-left:auto; color:var(--status-warning); font-size:.58rem; letter-spacing:.06em; }
        .gpu-status.live { color:var(--status-success); }
        .gpu-grid { flex:1; display:grid; grid-template-columns:repeat(3, 1fr); gap:7px; align-items:center; }
        .gpu-grid div { min-width:0; display:flex; flex-direction:column; gap:3px; }
        .gpu-grid span, .gpu-name { color:var(--text-muted); font-size:.62rem; font-weight:700; letter-spacing:.06em; }
        .gpu-grid strong { font-family:monospace; font-size:1rem; color:var(--text-main); }
        .gpu-name { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
        .app-card[data-cols="1"] .gpu-grid { grid-template-columns:1fr; grid-template-rows:repeat(3, 1fr); }
        .app-card[data-cols="1"] .gpu-grid div { flex-direction:row; justify-content:space-between; align-items:center; }
    `,
});
