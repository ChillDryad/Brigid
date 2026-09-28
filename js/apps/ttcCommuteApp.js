import { BaseApp } from "./baseApp.js";
import { registry } from "../registry.js";

export class TtcCommuteApp extends BaseApp {
    async render() {
        return `<div class="app-content app-type-ttc">
            <div class="ttc-header"><span><i class="fa-solid fa-train-subway"></i> COMMUTE</span><span data-ttc="status">CHECKING</span></div>
            <strong data-ttc="headline">TTC commute monitor</strong>
            <p data-ttc="detail">Checking your configured route…</p>
        </div>`;
    }

    onMount(el, app) {
        const interval = Math.max(60000, Number(app.data.interval) || 120000);
        const update = async () => {
            const status = el.querySelector('[data-ttc="status"]');
            try {
                const response = await fetch("/api/commute/ttc", { credentials: "same-origin" });
                if (!response.ok) throw new Error(response.status === 503 ? "SETUP" : "OFFLINE");
                const payload = await response.json();
                const first = payload.alerts[0];
                status.textContent = payload.status.toUpperCase();
                status.dataset.level = payload.status;
                el.querySelector('[data-ttc="headline"]').textContent = first ? first.headline : "Route clear";
                el.querySelector('[data-ttc="detail"]').textContent = first ? first.description : "No TTC alerts match your configured commute.";
            } catch (error) {
                status.textContent = error.message;
                el.querySelector('[data-ttc="headline"]').textContent = error.message === "SETUP" ? "Configure commute monitoring" : "TTC status unavailable";
                el.querySelector('[data-ttc="detail"]').textContent = error.message === "SETUP" ? "Add route or stop IDs in Brigid's server configuration." : "Do not assume normal service until the TTC feed recovers.";
            }
        };
        update();
        setInterval(update, interval);
    }
}

registry.register("ttc-commute", TtcCommuteApp, {
    label: "TTC Commute Sentinel",
    category: "data",
    defaultSize: { cols: 2, rows: 1 },
    settings: [{ name: "interval", label: "Refresh Interval (ms)", type: "text", defaultValue: "120000" }],
    css: `.app-type-ttc{height:100%;padding:10px;display:flex;flex-direction:column;gap:8px}.ttc-header{display:flex;gap:7px;align-items:center;border-bottom:1px solid var(--border-dim);padding-bottom:7px;font-size:.72rem;font-weight:700;letter-spacing:.08em}.ttc-header i{color:var(--brand-primary)}.ttc-header [data-ttc=status]{margin-left:auto;color:var(--status-success);font-size:.58rem}.ttc-header [data-level=watch]{color:var(--status-warning)}.ttc-header [data-level=action]{color:var(--status-error)}.app-type-ttc strong{font-size:.85rem}.app-type-ttc p{margin:0;color:var(--text-muted);font-size:.7rem;line-height:1.35;overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}`,
});
