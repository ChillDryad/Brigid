import { BaseApp } from "./baseApp.js";
import { registry } from "../registry.js";

const asNumber = (value) => Number.isFinite(Number(value)) ? Number(value) : null;

function firstNumber(source, names) {
    for (const name of names) {
        const value = name.split(".").reduce((current, key) => current?.[key], source);
        const parsed = asNumber(value);
        if (parsed !== null) return parsed;
    }
    return null;
}

function percentage(used, total, direct) {
    if (direct !== null) return Math.max(0, Math.min(100, direct));
    if (used !== null && total) return Math.max(0, Math.min(100, (used / total) * 100));
    return null;
}

function displayPercent(value) {
    return value === null ? "—" : `${value.toFixed(0)}%`;
}

function updateMetric(el, name, value) {
    const metric = el.querySelector(`[data-komodo-metric="${name}"]`);
    if (!metric) return;
    metric.querySelector(".komodo-value").textContent = displayPercent(value);
    metric.querySelector(".komodo-fill").style.width = `${value ?? 0}%`;
}

export class KomodoStatsApp extends BaseApp {
    async render() {
        return `
            <div class="app-content app-type-komodo">
                <div class="komodo-header">
                    <span><i class="fa-solid fa-server"></i> KOMODO</span>
                    <span class="komodo-status">CONNECTING</span>
                </div>
                <div class="komodo-metrics">
                    <div class="komodo-metric" data-komodo-metric="cpu"><span>CPU</span><strong class="komodo-value">—</strong><i><b class="komodo-fill"></b></i></div>
                    <div class="komodo-metric" data-komodo-metric="memory"><span>MEM</span><strong class="komodo-value">—</strong><i><b class="komodo-fill"></b></i></div>
                    <div class="komodo-metric" data-komodo-metric="disk"><span>DISK</span><strong class="komodo-value">—</strong><i><b class="komodo-fill"></b></i></div>
                </div>
            </div>`;
    }

    onMount(el, app) {
        const interval = Math.max(5000, Number(app.data.interval) || 15000);
        const update = async () => {
            if (!el.isConnected) return;
            const status = el.querySelector(".komodo-status");
            try {
                const response = await fetch("/api/komodo/stats", { credentials: "same-origin" });
                if (!response.ok) throw new Error(response.status === 503 ? "NOT CONFIGURED" : "OFFLINE");
                const payload = await response.json();
                const stats = payload.data || payload;
                const cpu = percentage(null, null, firstNumber(stats, ["cpu_perc", "cpu_percent", "cpu.usage", "cpu.percent"]));
                const memory = percentage(
                    firstNumber(stats, ["mem_used_gb", "memory.used_gb", "memory.used", "mem_used"]),
                    firstNumber(stats, ["mem_total_gb", "memory.total_gb", "memory.total", "mem_total"]),
                    firstNumber(stats, ["mem_perc", "memory.percent", "memory.usage"]),
                );
                const disk = percentage(
                    firstNumber(stats, ["disk_used_gb", "disk.used_gb", "disk.used"]),
                    firstNumber(stats, ["disk_total_gb", "disk.total_gb", "disk.total"]),
                    firstNumber(stats, ["disk_perc", "disk.percent", "disk.usage"]),
                );
                updateMetric(el, "cpu", cpu);
                updateMetric(el, "memory", memory);
                updateMetric(el, "disk", disk);
                status.textContent = "LIVE";
                status.classList.add("live");
            } catch (error) {
                status.textContent = error.message === "NOT CONFIGURED" ? "SETUP" : "OFFLINE";
                status.classList.remove("live");
            }
        };
        update();
        setInterval(update, interval);
    }
}

registry.register("komodo-stats", KomodoStatsApp, {
    label: "Komodo Server Stats",
    category: "data",
    defaultSize: { cols: 2, rows: 1 },
    settings: [
        { name: "interval", label: "Refresh Interval (ms)", type: "text", defaultValue: "15000" },
    ],
    css: `
        .app-type-komodo { height:100%; padding:10px; display:flex; flex-direction:column; gap:10px; color:inherit; }
        .komodo-header { display:flex; align-items:center; gap:7px; font-size:.78rem; font-weight:700; letter-spacing:.08em; border-bottom:1px solid var(--border-dim); padding-bottom:7px; }
        .komodo-header i { color:var(--brand-primary); margin-right:5px; }
        .komodo-status { margin-left:auto; color:var(--status-warning); font-size:.58rem; letter-spacing:.06em; white-space:nowrap; }
        .komodo-status.live { color:var(--status-success); }
        .komodo-metrics { flex:1; min-height:0; display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; }
        .komodo-metric { min-width:0; display:grid; grid-template-columns:1fr auto; grid-template-rows:auto 5px; gap:4px 6px; align-content:center; }
        .komodo-metric span { color:var(--text-muted); font-size:.62rem; font-weight:700; letter-spacing:.06em; }
        .komodo-value { font-family:monospace; font-size:.92rem; color:var(--text-main); }
        .komodo-metric i { grid-column:1 / -1; display:block; overflow:hidden; background:var(--bg-highlight); border-radius:999px; }
        .komodo-fill { display:block; height:100%; width:0; background:var(--brand-primary); transition:width .35s ease; }
        .app-card[data-cols="1"] .komodo-metrics { grid-template-columns:1fr; gap:4px; }
        .app-card[data-cols="1"] .komodo-metric { grid-template-columns:34px 1fr auto; grid-template-rows:auto; align-items:center; }
        .app-card[data-cols="1"] .komodo-metric i { grid-column:auto; }
    `,
});
