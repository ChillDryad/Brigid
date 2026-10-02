import { BaseApp } from "./baseApp.js";
import { registry } from "../registry.js";

const number = (value) => Number.isFinite(Number(value)) ? Number(value) : null;
const percent = (value) => value === null ? "—" : `${Math.max(0, Math.min(100, value)).toFixed(0)}%`;

function uptime(value) {
    const seconds = number(value);
    if (seconds === null || seconds < 0) return "—";
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
}

function updateMetric(el, key, value) {
    const metric = el.querySelector(`[data-homelab-metric="${key}"]`);
    if (!metric) return;
    metric.querySelector("strong").textContent = percent(number(value));
    metric.querySelector("b").style.width = `${number(value) ?? 0}%`;
}

export class HomelabStatsApp extends BaseApp {
    async render() {
        return `
            <div class="app-content app-type-homelab-stats">
                <div class="homelab-header">
                    <span><i class="fa-solid fa-house-signal"></i> HOME</span>
                    <span class="homelab-status">CONNECTING</span>
                </div>
                <div class="homelab-metrics">
                    <div class="homelab-metric" data-homelab-metric="cpu"><span>CPU</span><strong>—</strong><i><b></b></i></div>
                    <div class="homelab-metric" data-homelab-metric="memory"><span>MEM</span><strong>—</strong><i><b></b></i></div>
                    <div class="homelab-metric" data-homelab-metric="gpu"><span>GPU</span><strong>—</strong><i><b></b></i></div>
                    <div class="homelab-meta"><span data-homelab="disk">DISK —</span><span data-homelab="uptime">UP —</span><span data-homelab="load">LOAD —</span></div>
                </div>
            </div>`;
    }

    onMount(el, app) {
        const interval = Math.max(5000, Number(app.data.interval) || 15000);
        const update = async () => {
            if (!el.isConnected) return;
            const status = el.querySelector(".homelab-status");
            try {
                const response = await fetch("/api/homelab/stats", { credentials: "same-origin" });
                if (!response.ok) throw new Error(response.status === 503 ? "SETUP" : "OFFLINE");
                const stats = await response.json();
                updateMetric(el, "cpu", stats.cpuPercent);
                updateMetric(el, "memory", stats.memoryPercent);
                updateMetric(el, "gpu", stats.gpu?.utilizationPercent);
                el.querySelector('[data-homelab="disk"]').textContent = stats.diskPercent === null || stats.diskPercent === undefined
                    ? "DISK —"
                    : `DISK ${Number(stats.diskPercent).toFixed(0)}%`;
                el.querySelector('[data-homelab="uptime"]').textContent = `UP ${uptime(stats.uptimeSeconds)}`;
                el.querySelector('[data-homelab="load"]').textContent = stats.load === null || stats.load === undefined
                    ? "LOAD —"
                    : `LOAD ${Number(stats.load).toFixed(1)}`;
                const gpuMetric = el.querySelector('[data-homelab-metric="gpu"]');
                if (gpuMetric) {
                    const gpu = stats.gpu;
                    gpuMetric.title = gpu
                        ? `${gpu.names?.join(", ") || `${gpu.count} GPU${gpu.count === 1 ? "" : "s"}`} — memory ${percent(number(gpu.memoryPercent))}${gpu.temperatureC === null || gpu.temperatureC === undefined ? "" : `, ${Number(gpu.temperatureC).toFixed(0)}°C`}`
                        : "Glances GPU plugin is unavailable";
                }
                status.textContent = stats.hostname || "LIVE";
                status.title = stats.diskMount ? `Disk: ${stats.diskMount}` : "Glances telemetry live";
                status.classList.add("live");
            } catch (error) {
                status.textContent = error.message;
                status.title = "";
                status.classList.remove("live");
            }
        };
        update();
        setInterval(update, interval);
    }
}

registry.register("homelab-stats", HomelabStatsApp, {
    label: "Homelab Stats (Glances)",
    category: "data",
    defaultSize: { cols: 3, rows: 1 },
    settings: [{ name: "interval", label: "Refresh Interval (ms)", type: "text", defaultValue: "15000" }],
    css: `
        .app-type-homelab-stats { height:100%; padding:10px; display:flex; flex-direction:column; gap:9px; color:inherit; }
        .homelab-header { display:flex; align-items:center; gap:7px; border-bottom:1px solid var(--border-dim); padding-bottom:7px; font-size:.78rem; font-weight:700; letter-spacing:.08em; }
        .homelab-header i { color:var(--brand-secondary); margin-right:5px; }
        .homelab-status { margin-left:auto; max-width:42%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--status-warning); font-size:.58rem; letter-spacing:.06em; }
        .homelab-status.live { color:var(--status-success); }
        .homelab-metrics { flex:1; min-height:0; display:grid; grid-template-columns:repeat(3, 1fr); grid-template-rows:1fr auto; gap:8px; }
        .homelab-metric { min-width:0; display:grid; grid-template-columns:1fr auto; grid-template-rows:auto 5px; gap:4px 6px; align-content:center; }
        .homelab-metric span { color:var(--text-muted); font-size:.62rem; font-weight:700; letter-spacing:.06em; }
        .homelab-metric strong { font-family:monospace; font-size:.92rem; color:var(--text-main); }
        .homelab-metric i { grid-column:1 / -1; display:block; overflow:hidden; background:var(--bg-highlight); border-radius:999px; }
        .homelab-metric b { display:block; height:100%; width:0; background:var(--brand-secondary); transition:width .35s ease; }
        .homelab-meta { grid-column:1 / -1; display:flex; justify-content:space-between; gap:8px; color:var(--text-muted); font-family:monospace; font-size:.63rem; white-space:nowrap; overflow:hidden; }
        .app-card[data-cols="1"] .homelab-metrics { grid-template-columns:1fr; grid-template-rows:repeat(3, auto) auto; gap:4px; }
        .app-card[data-cols="1"] .homelab-metric { grid-template-columns:34px 1fr auto; grid-template-rows:auto; align-items:center; }
        .app-card[data-cols="1"] .homelab-metric i { grid-column:auto; }
    `,
});