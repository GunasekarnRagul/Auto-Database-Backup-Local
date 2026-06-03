/** @odoo-module **/

/**
 * Dropbox – Customizable Widget Dashboard (v3 — No External Dependencies)
 *
 * Architecture:
 *  • OWL state.activeWidgets drives the widget list via t-foreach in the template
 *  • Widget shells are rendered declaratively; only the body content is imperative
 *  • onPatched fills any newly added widget bodies
 *  • _loadLazyWidgets fills all bodies once RPC data is ready
 *  • HTML5 native drag-and-drop for reordering
 *  • CSS Grid (12-column) for layout — no GridStack
 */

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ShareDriveLinkDialog } from "../file_explorer/file_explorer";
import {
    Component,
    onWillStart,
    onMounted,
    onPatched,
    onWillUnmount,
    useState,
} from "@odoo/owl";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function _fileIcon(mime) {
    if (!mime) return "fa-file-o";
    if (mime.startsWith("image/"))             return "fa-file-image-o";
    if (mime === "application/pdf")            return "fa-file-pdf-o";
    if (mime.includes("spreadsheet") || mime.includes("excel")) return "fa-file-excel-o";
    if (mime.includes("wordprocessing") || mime === "application/msword") return "fa-file-word-o";
    if (mime.startsWith("video/"))             return "fa-file-video-o";
    if (mime.startsWith("text/"))              return "fa-file-text-o";
    if (mime.includes("zip") || mime.includes("compress")) return "fa-file-archive-o";
    return "fa-file-o";
}

function _pieColors(n) {
    const p = ["#4285F4","#34A853","#FBBC04","#EA4335","#7C4DFF","#00BCD4","#FF6D00","#43A047","#E91E63","#FF5722"];
    return Array.from({ length: n }, (_, i) => p[i % p.length]);
}

function _esc(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ─── Widget Catalog ────────────────────────────────────────────────────────────
// colSpan: 1-12 grid columns | rowClass: controls min-height via CSS

const WIDGET_CATALOG = [
    { id: "kpi_total_files",   label: "Total Files",         icon: "fa-files-o",          cat: "KPI",      colSpan: 3, rowClass: "kpi" },
    { id: "kpi_total_size",    label: "Total Cloud Usage",   icon: "fa-cloud-upload",      cat: "KPI",      colSpan: 3, rowClass: "kpi" },
    { id: "kpi_files_month",   label: "Files This Month",    icon: "fa-calendar-plus-o",    cat: "KPI",      colSpan: 3, rowClass: "kpi" },
    { id: "kpi_pending",       label: "Pending Syncs",       icon: "fa-clock-o",            cat: "KPI",      colSpan: 3, rowClass: "kpi" },
    // Fleet (full width)
    { id: "fleet_overview",    label: "Connected Drivers",   icon: "fa-hdd-o",              cat: "Fleet",    colSpan: 12, rowClass: "fleet" },
    // Storage
    { id: "storage_meter",     label: "Storage Meter",       icon: "fa-pie-chart",          cat: "Storage",  colSpan: 4, rowClass: "chart" },
    { id: "top_file_types",    label: "Top File Types",      icon: "fa-pie-chart",          cat: "Storage",  colSpan: 4, rowClass: "chart" },
    { id: "largest_files",     label: "Largest Files",       icon: "fa-sort-amount-desc",   cat: "Storage",  colSpan: 8, rowClass: "chart" },
    // Sync
    { id: "sync_trend",        label: "Sync Activity Chart", icon: "fa-area-chart",         cat: "Sync",     colSpan: 8, rowClass: "chart" },
    { id: "top_uploaders",     label: "Top Uploaders",       icon: "fa-trophy",             cat: "Users",    colSpan: 4, rowClass: "sm" },
    { id: "failed_syncs",      label: "Failed Syncs",        icon: "fa-times-circle",       cat: "Sync",     colSpan: 4, rowClass: "sm" },
    { id: "duplicates",        label: "Duplicate Files",     icon: "fa-clone",              cat: "Sync",     colSpan: 4, rowClass: "sm" },
    // Files
    { id: "activity_log",      label: "Activity Log",        icon: "fa-list-alt",           cat: "Sync",     colSpan: 8, rowClass: "lg" },
    { id: "recent_files",      label: "Recent Files",        icon: "fa-folder-open-o",      cat: "Files",    colSpan: 4, rowClass: "lg" },
    { id: "active_shares",     label: "Active Share Links",  icon: "fa-share-alt",          cat: "Files",    colSpan: 6,  rowClass: "lg" },
    // Business
    { id: "model_breakdown",   label: "Model Breakdown",     icon: "fa-tasks",              cat: "Business", colSpan: 6,  rowClass: "chart" },
];

const CATALOG_MAP = Object.fromEntries(WIDGET_CATALOG.map(w => [w.id, w]));

// Default ordered list of active widget IDs
const DEFAULT_WIDGETS = [
    "kpi_total_files", "kpi_total_size", "kpi_files_month", "kpi_pending",
    "fleet_overview",
    "sync_trend", "storage_meter",
    "top_uploaders", "failed_syncs", "duplicates",
    "activity_log", "recent_files",
    "active_shares", "model_breakdown",
];

// ─── Dashboard Component ──────────────────────────────────────────────────────

export class GoogleDriveDashboard extends Component {
    setup() {
        this.orm           = useService("orm");
        this.action        = useService("action");
        this.dialogService = useService("dialog");

        this.state = useState({
            isLoading:    true,
            editMode:     false,
            showAddPanel: false,
            panelFilter:  "all",
            activeWidgets: [...DEFAULT_WIDGETS],

            // Data stores
            kpis: {
                total_files_synced: "…", total_folders_synced: 0,
                synced_today: 0, pending_syncs: "…", pending_count: 0,
                uploading_count: 0, fails_today: 0,
                last_sync_time: "Never", storage_saved_formatted: "—",
                drive_space_used_formatted: "—", files_this_month: 0,
            },
            health:        { fleet: [], online_drives: 0, total_drives: 0 },
            recent_logs:   [],
            logFilter:     "today",
            activityLogPage: 1,
            recentFilesPage: 1,
            activeSharesPage: 1,
            asl_drive_id: null,
            duplicate_count: null,
            model_breakdown: [],
            error_summary: { fails_24h: 0, fails_7d: 0, top_errors: [] },
            quota:         { available: false },
            fleet_quotas:  {},
            cron:          { available: false },
            trendData:     null,
            files_by_model:    [],
                        active_shares:    [],
            top_uploaders:     [],
            top_file_types:    [],
            largest_files:     [],
            recent_files:      [],
            orphan_count:      0,

            // Global Trend Filter State
            globalTrendFilter: '7days',
            globalTrendStart:  '',
            globalTrendEnd:    '',

            // Loader for actions
            showActionLoader: false,
            loaderMessage: '',
        });

        // Initialize default global trend dates
        const today = new Date();
        const start = new Date();
        start.setDate(today.getDate() - 6);
        this.state.globalTrendStart = start.toISOString().split('T')[0];
        this.state.globalTrendEnd = today.toISOString().split('T')[0];

        this._charts          = {};    // Chart.js instances keyed by widgetId
        this._dataLoaded      = false; // true after first full lazy load
        this._dragSrcId       = null;  // drag-and-drop source widget id
        this._widgetRefreshing = new Set(); // widget ids currently being refreshed
        this._widgetToasts     = {};   // widgetId -> timeout handle for toast cleanup

        onWillStart(async () => {
            await this._loadLayout();
            await this._fetchCoreData();
        });

        onMounted(() => {
            this._fixParentScroll();
            this._loadLazyWidgets();
        });

        // After every OWL re-render, fill any widget body still showing a spinner
        onPatched(() => {
            if (this._dataLoaded) {
                this._renderPendingWidgets();
            }
        });

        onWillUnmount(() => {
            // Destroy charts
            Object.values(this._charts).forEach(c => { try { c.destroy(); } catch (_) {} });
            // Restore any parent element styles we patched for scrolling
            if (this._scrollPatched) {
                this._scrollPatched.forEach(({ el, overflowY, height }) => {
                    el.style.overflowY = overflowY;
                    el.style.height    = height;
                });
            }
        });
    }

    // ── Catalog helpers ───────────────────────────────────────────────────────

    getCatalog(id)    { return CATALOG_MAP[id] || { id, label: id, icon: "fa-question", colSpan: 4, rowClass: "sm" }; }
    getCellStyle(id)  { return `--col-span: ${this.getCatalog(id).colSpan};`; }
    getCellClass(id)  { return `gd-cell--${this.getCatalog(id).rowClass}`; }

    // ── Scroll fix ────────────────────────────────────────────────────────────
    // Odoo's .o_action_manager clips overflow. Walk up the DOM and ensure every
    // ancestor up to .o_action_manager has height:100% and overflow:auto/hidden
    // so our own .one_drive-dash can scroll internally.
    _fixParentScroll() {
        let el = this.__owl__?.bdom?.el || document.querySelector(".one_drive-dash");
        if (!el) return;
        // Walk up through ancestors until we hit body or o_action_manager
        let current = el.parentElement;
        this._scrollPatched = [];   // track what we patched so we can revert
        while (current && current !== document.body) {
            const tag = current.tagName.toLowerCase();
            const cls = current.className || "";
            const original = {
                el: current,
                overflowY: current.style.overflowY,
                height:    current.style.height,
            };
            if (cls.includes("o_action_manager")) {
                // Stop here — action manager keeps overflow:hidden, that is correct
                break;
            }
            if (cls.includes("o_action") || cls.includes("o_view") || tag === "main") {
                current.style.height    = "100%";
                current.style.overflowY = "hidden";
                this._scrollPatched.push(original);
            }
            current = current.parentElement;
        }
    }

    get panelCategories() {
        return ["all", ...new Set(WIDGET_CATALOG.map(w => w.cat))];
    }

    get catalogForPanel() {
        const active = new Set(this.state.activeWidgets);
        return WIDGET_CATALOG.filter(w =>
            this.state.panelFilter === "all" || w.cat === this.state.panelFilter
        ).map(w => ({ ...w, active: active.has(w.id) }));
    }

    // ── Layout persistence ────────────────────────────────────────────────────

    async _loadLayout() {
        try {
            const saved = await this.orm.call("one.drive.dashboard", "load_dashboard_layout", []);
            if (saved) {
                const parsed = JSON.parse(saved);
                // Support both old format (array of objects) and new format (array of id strings)
                if (Array.isArray(parsed)) {
                    const ids = parsed.map(item => (typeof item === "string" ? item : item.id)).filter(Boolean);
                    const validIds = ids.filter(id => CATALOG_MAP[id]);
                    if (validIds.length > 0) this.state.activeWidgets = validIds;
                }
            }
        } catch (e) {
            console.warn("Could not load dashboard layout, using defaults:", e);
        }
    }

    async _saveLayout() {
        try {
            await this.orm.call("one.drive.dashboard", "save_dashboard_layout",
                [JSON.stringify(this.state.activeWidgets)]);
        } catch (e) {
            console.error("Failed to save layout:", e);
        }
    }

    // ── Data fetching ─────────────────────────────────────────────────────────

    async _fetchCoreData() {
        this.state.isLoading = true;
        try {
            const d = await this.orm.call("one.drive.dashboard", "get_kpi_data", []);
            Object.assign(this.state.kpis, d.kpis);
            this.state.health = d.health;
        } catch (e) {
            console.error("Core data load failed:", e);
        } finally {
            this.state.isLoading = false;
        }
    }

    // ── Global Trend Filter Handlers ──────────────────────────────────────────

    async onGlobalTrendFilterChange(ev) {
        this.state.globalTrendFilter = ev.target.value;
        if (this.state.globalTrendFilter !== 'custom') {
            const today = new Date();
            let start = new Date();
            if (this.state.globalTrendFilter === 'today') start = today;
            if (this.state.globalTrendFilter === '7days') start.setDate(today.getDate() - 6);
            if (this.state.globalTrendFilter === '30days') start.setDate(today.getDate() - 29);
            
            this.state.globalTrendStart = start.toISOString().split('T')[0];
            this.state.globalTrendEnd = today.toISOString().split('T')[0];
            
            await this._fetchSyncTrendData();
        }
    }

    async onGlobalTrendDatesChange(ev) {
        // Will be triggered by individual input changes
        if (this.state.globalTrendStart && this.state.globalTrendEnd) {
            await this._fetchSyncTrendData();
        }
    }

    async _fetchSyncTrendData() {
        try {
            const d = await this.orm.call("one.drive.dashboard", "get_sync_trend_data", [], { 
                start_date: this.state.globalTrendStart, 
                end_date: this.state.globalTrendEnd 
            });
            this.state.trendData = d;
            if (this._dataLoaded) {
                this._renderWidget("sync_trend");
            }
        } catch (e) {
            console.error("Failed to fetch sync trend data:", e);
        }
    }

    async _loadLazyWidgets() {
        const s = this.state;
        const tasks = [
            this.orm.call("one.drive.dashboard", "get_activity_logs", [], { period: s.logFilter })
                .then(d => { s.recent_logs = d; }),
            this.orm.call("one.drive.dashboard", "get_storage_stats", [])
                .then(d => { Object.assign(s.kpis, d); }),
            this.orm.call("one.drive.dashboard", "get_duplicate_summary", [])
                .then(d => { s.duplicate_count = d.duplicate_count; }),
            this.orm.call("one.drive.dashboard", "get_model_breakdown", [])
                .then(d => { s.model_breakdown = d; }),
            this.orm.call("one.drive.dashboard", "get_error_summary", [])
                .then(d => { s.error_summary = d; }),
            this.orm.call("one.drive.dashboard", "get_sync_trend_data", [], { start_date: s.globalTrendStart, end_date: s.globalTrendEnd })
                .then(d => { s.trendData = d; }),
            this.orm.call("one.drive.dashboard", "get_files_by_model", [])
                .then(d => { s.files_by_model = d; }),
            this.orm.call("one.drive.dashboard", "get_top_uploaders", [])
                .then(d => { s.top_uploaders = d; }),
            this.orm.call("one.drive.dashboard", "get_top_file_types", [], { config_id: s.tft_drive_id || false })
                .then(d => { if (!s.top_file_types_data) s.top_file_types_data = {}; s.top_file_types_data[s.tft_drive_id] = d; s.top_file_types = d; }),
            this.orm.call("one.drive.dashboard", "get_largest_files", [])
                .then(d => { s.largest_files = d; }),
            this._widgetFetchers.recent_files(),
            this._widgetFetchers.active_shares(),
            this.orm.call("one.drive.dashboard", "get_storage_by_model", [])
                .then(d => { s.storage_by_model = d; }),
            this.orm.call("one.drive.dashboard", "get_orphan_attachments", [])
                .then(d => { s.orphan_count = d.orphan_count; }),
        ];

        // Fleet quotas per drive
        if (s.health && s.health.fleet) {
            for (const drive of s.health.fleet) {
                s.fleet_quotas[drive.id] = { available: false, loading: true };
                this.orm.call("one.drive.dashboard", "get_drive_quota", [], { config_id: drive.id })
                    .then(q => { q.loading = false; s.fleet_quotas[drive.id] = q; this._renderWidget("fleet_overview"); })
                    .catch(() => { s.fleet_quotas[drive.id] = { available: false, loading: false }; });
            }
        }

        await Promise.allSettled(tasks);

        // Mark data as loaded and render all active widgets
        this._dataLoaded = true;
        this._renderAllWidgets();
    }

    // ── Widget body rendering ─────────────────────────────────────────────────

    _getBody(id) { return document.getElementById(`wb-${id}`); }

    _renderAllWidgets() {
        for (const id of this.state.activeWidgets) {
            this._renderWidget(id);
        }
    }

    _renderPendingWidgets() {
        for (const id of this.state.activeWidgets) {
            const body = this._getBody(id);
            if (body && body.querySelector(".gd-spinner")) {
                this._renderWidget(id);
            }
        }
    }

    _renderWidget(id) {
        const body = this._getBody(id);
        if (!body) return;
        const fn = this._renderers[id];
        if (fn) fn.call(this, body);
    }

    // ── Per-widget refresh (with loader + toast) ──────────────────────────

    /**
     * Map of widget id → async function that fetches fresh data for that widget.
     * Must update this.state before resolving.
     */
    get _widgetFetchers() {
        const s = this.state;
        return {
            kpi_total_files:  async () => { const d = await this.orm.call("one.drive.dashboard", "get_kpi_data", []); Object.assign(s.kpis, d.kpis); s.health = d.health; },
            kpi_total_size:   async () => { const d = await this.orm.call("one.drive.dashboard", "get_storage_stats", []); Object.assign(s.kpis, d); },
            kpi_files_month:  async () => { const d = await this.orm.call("one.drive.dashboard", "get_kpi_data", []); Object.assign(s.kpis, d.kpis); },
            kpi_pending:      async () => { const d = await this.orm.call("one.drive.dashboard", "get_kpi_data", []); Object.assign(s.kpis, d.kpis); },
            fleet_overview:   async () => {
                const d = await this.orm.call("one.drive.dashboard", "get_kpi_data", []);
                s.health = d.health;
                Object.assign(s.kpis, d.kpis);
                s.fleet_quotas = {};
                if (s.health && s.health.fleet) {
                    for (const drive of s.health.fleet) {
                        s.fleet_quotas[drive.id] = { available: false, loading: true };
                        this.orm.call("one.drive.dashboard", "get_drive_quota", [], { config_id: drive.id })
                            .then(q => { q.loading = false; s.fleet_quotas[drive.id] = q; this._renderWidget("fleet_overview"); })
                            .catch(() => { s.fleet_quotas[drive.id] = { available: false, loading: false }; });
                    }
                }
            },
            storage_meter:    async () => { s.sm_quotas = {}; },
            top_file_types:   async () => { 
                if (s.tft_drive_id) {
                    const d = await this.orm.call("one.drive.dashboard", "get_top_file_types", [], { config_id: s.tft_drive_id });
                    if (!s.top_file_types_data) s.top_file_types_data = {};
                    s.top_file_types_data[s.tft_drive_id] = d;
                }
            },
            largest_files:    async () => { const d = await this.orm.call("one.drive.dashboard", "get_largest_files", []); s.largest_files = d; },
            sync_trend:       async () => { const d = await this.orm.call("one.drive.dashboard", "get_sync_trend_data", [], { start_date: s.globalTrendStart, end_date: s.globalTrendEnd }); s.trendData = d; },
            failed_syncs:     async () => { const d = await this.orm.call("one.drive.dashboard", "get_error_summary", []); s.error_summary = d; },
            duplicates:       async () => { const d = await this.orm.call("one.drive.dashboard", "get_duplicate_summary", []); s.duplicate_count = d.duplicate_count; },
            activity_log:     async () => { const d = await this.orm.call("one.drive.dashboard", "get_activity_logs", [], { period: s.logFilter }); s.recent_logs = d; },
            recent_files: async () => {
                // Use the same localStorage key as the File Explorer so 'Clear Recent' syncs both
                const recentIds = JSON.parse(localStorage.getItem('gd_recent_files') || '[]');
                if (!recentIds.length) { s.recent_files = []; return; }
                const records = await this.orm.searchRead(
                    'one.drive.file',
                    [['id', 'in', recentIds], ['file_type', '!=', 'folder'], ['active', '=', true]],
                    ['id', 'name', 'file_size', 'mime_type', 'one_drive_url', 'one_drive_file_id', 'sync_state', 'write_date']
                );
                // Sort by localStorage order (most recent first)
                records.sort((a, b) => recentIds.indexOf(a.id) - recentIds.indexOf(b.id));
                s.recent_files = records.map(f => ({
                    id: f.id,
                    name: f.name,
                    size: f.file_size ? (f.file_size < 1024 ? f.file_size.toFixed(0) + ' KB' : (f.file_size / 1024).toFixed(1) + ' MB') : '—',
                    mime_type: f.mime_type || '',
                    drive_url: f.one_drive_url || '',
                    one_drive_file_id: f.one_drive_file_id || '',
                    date: f.write_date ? f.write_date.replace('T', ' ').slice(0, 16) : '',
                    sync_state: f.sync_state || '',
                }));
            },
            active_shares:    async () => { 
                const d = await this.orm.call("one.drive.dashboard", "get_active_shares", [], { 
                    config_id: s.asl_drive_id || false 
                }); 
                s.active_shares = d; 
            },
            top_uploaders:    async () => { const d = await this.orm.call("one.drive.dashboard", "get_top_uploaders", []); s.top_uploaders = d; },
            model_breakdown:  async () => { const d = await this.orm.call("one.drive.dashboard", "get_model_breakdown", []); s.model_breakdown = d; },
        };
    }

    /**
     * Refresh a single widget:
     *  1. Show spinner overlay on the body (content stays dim behind it)
     *  2. Spin the refresh icon in the header
     *  3. Fetch fresh data via _widgetFetchers
     *  4. Re-render the widget
     *  5. Show success / fail toast, auto-dismiss after 2.5 s
     */
    async _refreshWidget(wId) {
        if (this._widgetRefreshing.has(wId)) return; // prevent double-click
        this._widgetRefreshing.add(wId);

        // 1. Spinner overlay on body
        const body = this._getBody(wId);
        let overlay = null;
        if (body) {
            overlay = document.createElement("div");
            overlay.className = "gd-refresh-overlay";
            overlay.innerHTML = `<div class="gd-refresh-overlay__ring"></div>`;
            body.style.position = "relative";
            body.appendChild(overlay);
        }

        // 2. Spin the header refresh icon
        const cell        = body && body.closest(".gd-cell");
        const refreshBtn  = cell && cell.querySelector(".gd-hd-btn:not(.gd-hd-btn--remove)");
        const refreshIcon = refreshBtn && refreshBtn.querySelector(".fa");
        if (refreshIcon) refreshIcon.classList.add("fa-spin");
        if (refreshBtn)  refreshBtn.disabled = true;

        // 3. Fetch
        let success = true;
        let errMsg  = "";
        try {
            const fetcher = this._widgetFetchers[wId];
            if (fetcher) await fetcher();
            else         await this._fetchCoreData();
        } catch (e) {
            success = false;
            errMsg  = e.message || "Unknown error";
            console.error(`[Dashboard] Refresh failed for widget "${wId}":`, e);
        }

        // 4. Remove overlay + re-render
        if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
        if (refreshIcon) refreshIcon.classList.remove("fa-spin");
        if (refreshBtn)  refreshBtn.disabled = false;
        this._widgetRefreshing.delete(wId);
        this._renderWidget(wId);

        // 5. Toast
        this._showWidgetToast(wId, success, errMsg);
    }

    /** Show a small success/fail chip in the widget header, auto-dismiss after 2.5 s. */
    _showWidgetToast(wId, success, message) {
        const body = this._getBody(wId);
        const cell = body && body.closest(".gd-cell");
        const hd   = cell && cell.querySelector(".gd-widget__hd");
        if (!hd) return;

        const prev = hd.querySelector(".gd-refresh-toast");
        if (prev) prev.remove();
        if (this._widgetToasts[wId]) clearTimeout(this._widgetToasts[wId]);

        const toast = document.createElement("span");
        toast.className = `gd-refresh-toast gd-refresh-toast--${success ? "ok" : "fail"}`;
        toast.title     = success ? "Refreshed successfully" : message;
        toast.innerHTML = success
            ? `<i class="fa fa-check-circle"></i> Updated`
            : `<i class="fa fa-times-circle"></i> Failed`;
        hd.appendChild(toast);

        requestAnimationFrame(() => toast.classList.add("gd-refresh-toast--visible"));

        this._widgetToasts[wId] = setTimeout(() => {
            toast.classList.remove("gd-refresh-toast--visible");
            setTimeout(() => { if (toast.parentNode) toast.remove(); }, 350);
        }, 2500);
    }

    _kpi(el, value, label, icon, color) {
        el.innerHTML = `
          <div class="gd-kpi gd-kpi--${color}">
            <div class="gd-kpi__ring"><i class="fa ${icon}"></i></div>
            <div class="gd-kpi__val">${value !== null && value !== undefined ? value : "—"}</div>
            <div class="gd-kpi__lbl">${label}</div>
          </div>`;
    }

    // ── All widget renderers ──────────────────────────────────────────────────

    get _renderers() {
        const s = this.state;
        return {
            // ─── KPI Cards ───────────────────────────────────
            kpi_total_files:  el => this._kpi(el, s.kpis.total_files_synced,          "Total Files Synced",  "fa-files-o",           "blue"),
            kpi_total_size:   el => this._kpi(el, s.kpis.drive_space_used_formatted,  "Total Cloud Usage",   "fa-cloud-upload",      "green"),
            kpi_files_month:  el => this._kpi(el, s.kpis.files_this_month,            "Files This Month",    "fa-calendar-plus-o",   "purple"),
            kpi_pending:      el => this._kpi(el, s.kpis.pending_syncs,               "Pending Syncs",       "fa-clock-o",           "orange"),

            // ─── Storage Meter (donut) ────────────────────────
            storage_meter: el => {
                const fleet = s.health && s.health.fleet ? s.health.fleet : [];
                if (!fleet.length) {
                    el.innerHTML = `<div class="gd-empty"><i class="fa fa-pie-chart"></i><span>No drivers connected</span></div>`;
                    return;
                }
                
                if (!s.sm_drive_id) s.sm_drive_id = fleet[0].id;
                if (!s.sm_quotas) s.sm_quotas = {};
                
                const q = s.sm_quotas[s.sm_drive_id];
                
                let html = `
                  <div style="margin-bottom:12px;">
                    <select class="gd-sm-select" style="width:100%; padding:6px 10px; border-radius:8px; border:1px solid var(--gd-border); font-size:13px; font-family:var(--gd-font); font-weight:500; color:var(--gd-tx); background:var(--gd-page-bg); outline:none; cursor:pointer;">
                      ${fleet.map(d => `<option value="${d.id}" ${d.id == s.sm_drive_id ? 'selected' : ''}>${_esc(d.name)}</option>`).join('')}
                    </select>
                  </div>
                `;

                if (!q || (!q.available && !q.error)) {
                    el.innerHTML = html + `<div class="gd-empty" style="min-height:140px;"><i class="fa fa-pie-chart fa-spin"></i><span>Fetching quota…</span></div>`;
                    this.orm.call("one.drive.dashboard", "get_drive_quota", [], { config_id: s.sm_drive_id })
                        .then(q2 => { s.sm_quotas[s.sm_drive_id] = q2; this._renderWidget("storage_meter"); })
                        .catch(e => { s.sm_quotas[s.sm_drive_id] = { available: false, error: e.message || "RPC Error" }; this._renderWidget("storage_meter"); });
                        
                    el.querySelector('.gd-sm-select').addEventListener('change', (e) => {
                        s.sm_drive_id = parseInt(e.target.value);
                        this._renderWidget("storage_meter");
                    });
                    return;
                }

                if (q.error) {
                    el.innerHTML = html + `<div class="gd-empty" style="min-height:140px;" title="${_esc(q.error)}"><i class="fa fa-exclamation-triangle" style="color:var(--gd-red);"></i><span>Quota Error</span></div>`;
                    el.querySelector('.gd-sm-select').addEventListener('change', (e) => {
                        s.sm_drive_id = parseInt(e.target.value);
                        this._renderWidget("storage_meter");
                    });
                    return;
                }

                const pct = q.usage_pct || 0;
                const clr = pct > 90 ? "#ea4335" : pct > 70 ? "#fbbc04" : "#1a73e8"; // Using Google Blue instead of green for general
                html += `
                  <div class="gd-donut-wrap">
                    <canvas id="c-storage-meter" style="max-height:150px;width:100%;"></canvas>
                    <div class="gd-donut-center"><span class="gd-donut-pct">${pct}%</span></div>
                  </div>
                  <div class="gd-quota-row"><span>${_esc(q.usage_formatted)} used</span><span class="t-muted">of ${_esc(q.limit_formatted)}</span></div>`;
                  
                el.innerHTML = html;
                
                el.querySelector('.gd-sm-select').addEventListener('change', (e) => {
                    s.sm_drive_id = parseInt(e.target.value);
                    this._renderWidget("storage_meter");
                });

                this._chart("storage_meter", el.querySelector("canvas"), {
                    type: "doughnut",
                    data: { labels: ["Used","Free"], datasets: [{ data: [pct, 100-pct], backgroundColor: [clr,"#e8eaed"], borderWidth: 0 }] },
                    options: { cutout: "75%", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
                });
            },

            // ─── Top File Types (doughnut) ────────────────────
            top_file_types: el => {
                const fleet = s.health && s.health.fleet ? s.health.fleet : [];
                if (!fleet.length) { el.innerHTML = this._empty("fa-pie-chart", "No drivers connected"); return; }
                
                if (!s.tft_drive_id) s.tft_drive_id = fleet[0].id;
                if (!s.top_file_types_data) s.top_file_types_data = {};
                
                let html = `
                  <div style="margin-bottom:12px;">
                    <select class="gd-tft-select" style="width:100%; padding:6px 10px; border-radius:8px; border:1px solid var(--gd-border); font-size:13px; font-family:var(--gd-font); font-weight:500; color:var(--gd-tx); background:var(--gd-page-bg); outline:none; cursor:pointer;">
                      ${fleet.map(d => `<option value="${d.id}" ${d.id == s.tft_drive_id ? 'selected' : ''}>${_esc(d.name)}</option>`).join('')}
                    </select>
                  </div>
                `;

                const d = s.top_file_types_data[s.tft_drive_id];
                if (!d) {
                    el.innerHTML = html + `<div class="gd-empty" style="min-height:140px;"><i class="fa fa-spinner fa-spin"></i><span>Loading…</span></div>`;
                    this.orm.call("one.drive.dashboard", "get_top_file_types", [], { config_id: s.tft_drive_id })
                        .then(res => { s.top_file_types_data[s.tft_drive_id] = res; this._renderWidget("top_file_types"); })
                        .catch(e => { s.top_file_types_data[s.tft_drive_id] = []; this._renderWidget("top_file_types"); });
                    
                    el.querySelector('.gd-tft-select').addEventListener('change', (e) => {
                        s.tft_drive_id = parseInt(e.target.value);
                        this._renderWidget("top_file_types");
                    });
                    return;
                }

                if (!d || !d.length) { 
                    el.innerHTML = html + this._empty("fa-pie-chart", "No file type data");
                    el.querySelector('.gd-tft-select').addEventListener('change', (e) => {
                        s.tft_drive_id = parseInt(e.target.value);
                        this._renderWidget("top_file_types");
                    });
                    return; 
                }

                const labels = d.map(r => r.file_type_label), counts = d.map(r => r.cnt), colors = _pieColors(labels.length);
                html += `
                  <div class="gd-donut-wrap">
                    <canvas id="c-file-types" style="max-height:160px;width:100%;"></canvas>
                  </div>
                  <div class="gd-legend">${labels.map((l,i) => `<span class="gd-legend-item"><span class="gd-dot" style="background:${colors[i]}"></span>${_esc(l)} <b>${counts[i]}</b></span>`).join("")}</div>`;
                
                el.innerHTML = html;
                
                el.querySelector('.gd-tft-select').addEventListener('change', (e) => {
                    s.tft_drive_id = parseInt(e.target.value);
                    this._renderWidget("top_file_types");
                });

                this._chart("top_file_types", el.querySelector("canvas"), {
                    type: "doughnut",
                    data: { labels, datasets: [{ data: counts, backgroundColor: colors, borderWidth: 0, hoverOffset: 6 }] },
                    options: { cutout: "55%", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
                });
            },

            // ─── Largest Files ────────────────────────────────
            largest_files: el => {
                const d = s.largest_files;
                if (!d || !d.length) { el.innerHTML = this._empty("fa-sort-amount-desc", "No files found"); return; }
                el.innerHTML = `<div class="gd-scroll"><table class="gd-table">
                  <thead><tr><th>#</th><th>File</th><th>Location</th><th>Size</th></tr></thead>
                  <tbody>${d.map((f,i) => `<tr>
                    <td class="gd-rank">${i+1}</td>
                    <td class="gd-fn"><i class="fa ${_fileIcon(f.mime_type)}"></i> ${_esc(f.name)}</td>
                    <td class="text-muted" style="max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${_esc(f.display_path || '—')}">${_esc(f.display_path || '—')}</td>
                    <td><span class="gd-size-badge">${f.size}</span></td>
                  </tr>`).join("")}</tbody></table></div>`;
            },

            // ─── Sync Activity Chart ──────────────────────────
            sync_trend: el => {
                if (!s.trendData) { el.innerHTML = this._empty("fa-spinner fa-spin", "Loading chart…"); return; }

                let html = `
                  <div style="height:250px; width:100%; position:relative;">
                    <canvas id="c-sync-trend"></canvas>
                  </div>
                `;
                el.innerHTML = html;

                const d = s.trendData;
                const lbls = d.labels.map(dt => new Date(dt+"T00:00:00").toLocaleDateString("en",{weekday:"short",month:"short",day:"numeric"}));
                this._chart("sync_trend", el.querySelector("canvas"), {
                    type: "bar",
                    data: { labels: lbls, datasets: [
                        { label:"Success", data: d.success, backgroundColor:"rgba(19, 115, 51, 0.8)",  borderColor:"#137333", borderWidth:1, borderRadius:5 },
                        { label:"Failed",  data: d.failed,  backgroundColor:"rgba(217, 48, 37, 0.8)", borderColor:"#d93025", borderWidth:1, borderRadius:5 },
                    ]},
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        interaction: { intersect: false, mode: "index" },
                        plugins: {
                            legend: { display: true, position: "top", labels: { boxWidth: 12, padding: 14, font: { size: 12 } } },
                            tooltip: { backgroundColor: "rgba(32,33,36,0.9)", cornerRadius: 8, padding: 12 },
                        },
                        scales: {
                            x: { grid: { display: false }, ticks: { color: "#5f6368", font: { size: 11 } } },
                            y: { beginAtZero: true, grid: { color: "#e8eaed" }, ticks: { stepSize: 1, color: "#5f6368", font: { size: 11 } } },
                        },
                    },
                });
            },

            // ─── Sync Status Badge ────────────────────────────

            // ─── Activity Log ─────────────────────────────────
            activity_log: el => {
                const logs = s.recent_logs;
                const itemsPerPage = 10;
                const totalPages = Math.ceil(logs.length / itemsPerPage) || 1;
                const currentPage = s.activityLogPage;
                const startIndex = (currentPage - 1) * itemsPerPage;
                const paginatedLogs = logs.slice(startIndex, startIndex + itemsPerPage);

                let paginationHtml = '';
                if (totalPages > 1) {
                    let pagesHtml = '';
                    for (let i = 1; i <= totalPages; i++) {
                        pagesHtml += `<button class="gd-page-btn${i === currentPage ? ' active' : ''}" data-page="${i}">${i}</button>`;
                    }
                    paginationHtml = `<div class="gd-pagination" style="display:flex; justify-content:center; gap:5px; margin-top:10px; padding-bottom:5px;">
                        <button class="gd-page-nav" data-page="${currentPage > 1 ? currentPage - 1 : 1}" ${currentPage === 1 ? 'disabled' : ''}>&laquo; Prev</button>
                        ${pagesHtml}
                        <button class="gd-page-nav" data-page="${currentPage < totalPages ? currentPage + 1 : totalPages}" ${currentPage === totalPages ? 'disabled' : ''}>Next &raquo;</button>
                    </div>`;
                }

                el.innerHTML = `
                  <div class="gd-log-bar">
                    ${["today","7d","30d","all"].map(p => `<button class="gd-fpill${s.logFilter===p?" active":""}" data-period="${p}">${p==="today"?"Today":p==="all"?"All":p}</button>`).join("")}
                  </div>
                  <div class="gd-scroll"><table class="gd-table gd-log-tbl">
                    <thead><tr><th>File</th><th>Op</th><th>Status</th><th>Time</th></tr></thead>
                    <tbody>${!paginatedLogs.length
                        ? `<tr><td colspan="4" class="gd-empty-row">No activity for this period.</td></tr>`
                        : paginatedLogs.map(l => `<tr>
                            <td class="gd-fn gd-fn--sm">${_esc(l.file_name)}</td>
                            <td><span class="gd-op-badge">${_esc(l.operation)}</span></td>
                            <td><span class="gd-status-badge gd-status-badge--${l.state==="success"?"ok":"fail"}">${l.state}</span></td>
                            <td class="gd-time">${l.date}</td>
                          </tr>`).join("")}
                    </tbody></table>
                    ${paginationHtml}
                  </div>`;
                  
                el.querySelectorAll(".gd-fpill").forEach(b =>
                    b.addEventListener("click", () => this._filterLogs(b.dataset.period)));
                
                el.querySelectorAll(".gd-page-btn, .gd-page-nav").forEach(b => {
                    b.addEventListener("click", () => {
                        if (!b.disabled) {
                            s.activityLogPage = parseInt(b.dataset.page);
                            this._renderWidget("activity_log");
                        }
                    });
                });
            },

            // ─── Failed Syncs ─────────────────────────────────
            failed_syncs: el => {
                const e = s.error_summary;
                el.innerHTML = `
                  <div class="gd-err-nums">
                    <div class="gd-err-num"><span>${e.fails_24h}</span><small>24 h</small></div>
                    <div class="gd-err-num gd-err-num--7d"><span>${e.fails_7d}</span><small>7 days</small></div>
                  </div>
                  ${e.top_errors && e.top_errors.length
                    ? `<div class="gd-top-errs">${e.top_errors.map(r => `<div class="gd-top-err"><b>${r.cnt}×</b><span>${_esc(r.error_message)}</span></div>`).join("")}</div>`
                    : `<div class="gd-ok-msg"><i class="fa fa-check-circle"></i> No errors this week</div>`}
                  <button class="gd-link-btn" id="view-logs-btn">View All Failed Logs →</button>`;
                el.querySelector("#view-logs-btn").addEventListener("click", () => this._openSyncLogs());
            },

            // ─── Duplicates ───────────────────────────────────
            duplicates: el => {
                const cnt = s.duplicate_count;
                if (cnt === null) { el.innerHTML = this._empty("fa-spinner fa-spin", "Scanning…"); return; }
                if (cnt > 0) {
                    el.innerHTML = `
                      <div class="gd-dupe-warn"><i class="fa fa-exclamation-triangle"></i> ${cnt} duplicates</div>
                      <button class="gd-btn gd-btn--warning gd-btn--block" id="prune-btn">Review &amp; Prune</button>`;
                    el.querySelector("#prune-btn").addEventListener("click", () => this._openDuplicatePruner());
                } else {
                    el.innerHTML = `<div class="gd-dupe-ok"><i class="fa fa-check-circle"></i> Zero duplicates</div>`;
                }
            },

            // ─── Recent Files ─────────────────────────────────
            recent_files: el => {
                const d = s.recent_files;
                if (!d || !d.length) { el.innerHTML = this._empty("fa-folder-open-o", "No recent files"); return; }
                
                const itemsPerPage = 8;
                const totalPages = Math.ceil(d.length / itemsPerPage) || 1;
                const currentPage = s.recentFilesPage;
                const startIndex = (currentPage - 1) * itemsPerPage;
                const paginatedFiles = d.slice(startIndex, startIndex + itemsPerPage);

                let paginationHtml = '';
                if (totalPages > 1) {
                    let pagesHtml = '';
                    const maxVisiblePages = 5;
                    let startPage = Math.max(1, currentPage - Math.floor(maxVisiblePages / 2));
                    let endPage = Math.min(totalPages, startPage + maxVisiblePages - 1);
                    if (endPage - startPage + 1 < maxVisiblePages) {
                        startPage = Math.max(1, endPage - maxVisiblePages + 1);
                    }
                    for (let i = startPage; i <= endPage; i++) {
                        pagesHtml += `<button class="gd-page-btn${i === currentPage ? ' active' : ''}" data-page="${i}">${i}</button>`;
                    }
                    paginationHtml = `<div class="gd-pagination" style="display:flex; justify-content:center; gap:6px; margin-top:10px; padding-bottom:5px;">
                        <button class="gd-page-nav" data-page="${currentPage > 1 ? currentPage - 1 : 1}" ${currentPage === 1 ? 'disabled' : ''}>&laquo;</button>
                        ${pagesHtml}
                        <button class="gd-page-nav" data-page="${currentPage < totalPages ? currentPage + 1 : totalPages}" ${currentPage === totalPages ? 'disabled' : ''}>&raquo;</button>
                    </div>`;
                }

                el.innerHTML = `<div class="gd-file-feed gd-scroll">${paginatedFiles.map(f => `
                  <div class="gd-file-row gd-file-row--clickable" data-file-id="${f.id}" data-file-url="${f.drive_url || ''}" data-file-name="${_esc(f.name)}" title="Click to preview ${_esc(f.name)}">
                    <div class="gd-ficon"><i class="fa ${_fileIcon(f.mime_type)}"></i></div>
                    <div class="gd-finfo">
                      <span class="gd-fname">${_esc(f.name)}</span>
                      <span class="gd-fmeta">${f.size} · ${f.date}</span>
                    </div>
                    <span class="gd-sbadge gd-sbadge--${f.sync_state}">${f.sync_state}</span>
                    <span class="gd-file-open-icon"><i class="fa fa-external-link"></i></span>
                  </div>`).join("")}
                  ${paginationHtml}
                </div>`;

                // Pagination click
                el.querySelectorAll(".gd-page-btn, .gd-page-nav").forEach(b => {
                    b.addEventListener("click", (e) => {
                        e.stopPropagation();
                        if (!b.disabled) {
                            s.recentFilesPage = parseInt(b.dataset.page);
                            this._renderWidget("recent_files");
                        }
                    });
                });

                // File row click → open preview dialog
                el.querySelectorAll(".gd-file-row--clickable").forEach(row => {
                    row.addEventListener("click", () => {
                        const fileId = parseInt(row.dataset.fileId);
                        const driveUrl = row.dataset.fileUrl;
                        if (!fileId) return;

                        if (driveUrl) {
                            window.open(driveUrl, '_blank');
                        } else {
                            // Fallback: open Odoo form view as a dialog
                            this.action.doAction({
                                type: 'ir.actions.act_window',
                                res_model: 'one.drive.file',
                                res_id: fileId,
                                views: [[false, 'form']],
                                target: 'new',
                            });
                        }
                    });
                });
            },


            // ─── Files by Model ───────────────────────────────

            // ─── Active Shares (Table Design) ─────────
            active_shares: el => {
                const fleet = s.health && s.health.fleet ? s.health.fleet : [];
                if (!fleet.length) { el.innerHTML = this._empty("fa-share-alt", "No drivers connected"); return; }
                
                // Initialize asl_drive_id if not set (default to first driver or 'all')
                if (s.asl_drive_id === null) s.asl_drive_id = 0; // 0 means 'All'

                const d = s.active_shares;
                const permIcon  = (p) => p === 'anyone' ? 'fa-globe' : p === 'domain' ? 'fa-building' : 'fa-lock';
                const permClass = (p) => p === 'anyone' ? 'gd-sl-badge--public' : p === 'domain' ? 'gd-sl-badge--domain' : 'gd-sl-badge--private';
                const permLabel = (f) => f.access_label || (f.permission_type === 'anyone' ? 'Public' : f.permission_type === 'domain' ? 'Domain' : 'Restricted');
                const fileIcon  = (f) => f.file_type === 'folder' ? 'fa-folder' : 'fa-file-o';

                const itemsPerPage = 6;
                const totalPages = Math.ceil(d.length / itemsPerPage) || 1;
                const currentPage = s.activeSharesPage;
                const startIndex = (currentPage - 1) * itemsPerPage;
                const paginatedShares = d.slice(startIndex, startIndex + itemsPerPage);

                let paginationHtml = '';
                if (totalPages > 1) {
                    let pagesHtml = '';
                    const maxVisiblePages = 5;
                    let startPage = Math.max(1, currentPage - Math.floor(maxVisiblePages / 2));
                    let endPage = Math.min(totalPages, startPage + maxVisiblePages - 1);
                    if (endPage - startPage + 1 < maxVisiblePages) {
                        startPage = Math.max(1, endPage - maxVisiblePages + 1);
                    }
                    for (let i = startPage; i <= endPage; i++) {
                        pagesHtml += `<button class="gd-page-btn${i === currentPage ? ' active' : ''}" data-page="${i}">${i}</button>`;
                    }
                    paginationHtml = `<div class="gd-pagination" style="display:flex; justify-content:center; gap:6px; margin-top:10px; padding-bottom:5px;">
                        <button class="gd-page-nav" data-page="${currentPage > 1 ? currentPage - 1 : 1}" ${currentPage === 1 ? 'disabled' : ''}>&laquo;</button>
                        ${pagesHtml}
                        <button class="gd-page-nav" data-page="${currentPage < totalPages ? currentPage + 1 : totalPages}" ${currentPage === totalPages ? 'disabled' : ''}>&raquo;</button>
                    </div>`;
                }

                let summaryHtml = '';
                if (d.length > 0) {
                    summaryHtml = `
                      <div class="gd-sl-summary" style="margin-bottom: 12px; display:flex; align-items:center; flex-wrap:wrap; gap:12px;">
                        <span class="gd-sl-sum-item"><i class="fa fa-link"></i> <b>${d.length}</b> shared items</span>
                        <span class="gd-sl-sum-divider"></span>
                        <span class="gd-sl-sum-item gd-sl-sum-item--pub">
                          <i class="fa fa-globe"></i> <b>${d.filter(f => f.permission_type === 'anyone').length}</b> public
                        </span>
                        <span class="gd-sl-sum-item gd-sl-sum-item--priv">
                          <i class="fa fa-lock"></i> <b>${d.filter(f => f.permission_type === 'restricted').length}</b> restricted
                        </span>
                      </div>`;
                }

                el.innerHTML = `
                  <div style="margin-bottom:12px; display:flex; gap:10px; align-items:center;">
                    <i class="fa fa-filter" style="color:var(--gd-txm); font-size:14px;"></i>
                    <select class="gd-asl-select" style="flex:1; padding:6px 12px; border-radius:8px; border:1px solid var(--gd-border); font-size:13px; font-family:var(--gd-font); font-weight:500; color:var(--gd-tx); background:var(--gd-page-bg); outline:none; cursor:pointer;">
                      <option value="0" ${s.asl_drive_id === 0 ? 'selected' : ''}>All Drivers</option>
                      ${fleet.map(drv => `<option value="${drv.id}" ${drv.id == s.asl_drive_id ? 'selected' : ''}>${_esc(drv.name)}</option>`).join('')}
                    </select>
                  </div>
                  ${summaryHtml}
                  <div class="gd-scroll">
                    ${d.length === 0 ? this._empty("fa-share-alt", "No shared items for this driver") : `
                    <table class="gd-table">
                      <thead>
                        <tr>
                          <th>File</th>
                          <th>Location</th>
                          <th>Record</th>
                          <th>Access</th>
                          <th style="width: 80px; text-align: right;">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${paginatedShares.map(f => `
                        <tr>
                          <td class="gd-fn"><i class="fa ${fileIcon(f)}"></i> ${_esc(f.name)}</td>
                          <td class="text-muted" style="max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${_esc(f.display_path || '—')}">${_esc(f.display_path || '—')}</td>
                          <td>
                            ${f.res_model && f.res_id && f.record_name ? `
                              <a href="#" class="gd-record-link" data-model="${f.res_model}" data-id="${f.res_id}" style="color: var(--gd-blue); text-decoration: none; font-weight: 500;" title="Open Related Record">
                                <i class="fa fa-external-link" style="margin-right: 4px;"></i>${_esc(f.res_model_label)}: ${_esc(f.record_name)}
                              </a>
                            ` : `<span class="text-muted">—</span>`}
                          </td>
                          <td>
                            <span class="gd-sl-badge ${permClass(f.permission_type)}">
                              <i class="fa ${permIcon(f.permission_type)}"></i> ${permLabel(f)}
                            </span>
                          </td>
                          <td style="text-align: right;">
                            <button class="gd-btn gd-btn--ghost gd-btn--sm gd-share-wizard-btn" data-id="${f.id}" title="Manage Share Link"><i class="fa fa-cog"></i></button>
                          </td>
                        </tr>`).join('')}
                      </tbody>
                    </table>`}
                  </div>
                  ${paginationHtml}
                `;

                // Driver filter change
                el.querySelector('.gd-asl-select').addEventListener('change', async (e) => {
                    s.asl_drive_id = parseInt(e.target.value);
                    s.activeSharesPage = 1;
                    await this._refreshWidget("active_shares");
                });

                // Manage Share Link button → open the file explorer's ShareDriveLinkDialog
                el.querySelectorAll('.gd-share-wizard-btn').forEach(btn => {
                    btn.addEventListener('click', () => {
                        const fileId = parseInt(btn.dataset.id);
                        const file = d.find(f => f.id === fileId);
                        if (file) this._openShareWizard(file);
                    });
                });

                // Record link click → open the related Odoo record form view
                el.querySelectorAll('.gd-record-link').forEach(link => {
                    link.addEventListener('click', (e) => {
                        e.preventDefault();
                        const model = link.dataset.model;
                        const resId = parseInt(link.dataset.id);
                        if (model && resId) {
                            this.action.doAction({
                                type: 'ir.actions.act_window',
                                res_model: model,
                                res_id: resId,
                                views: [[false, 'form']],
                                target: 'current',
                            });
                        }
                    });
                });



                // Pagination click
                el.querySelectorAll(".gd-page-btn, .gd-page-nav").forEach(b => {
                    b.addEventListener("click", (e) => {
                        e.stopPropagation();
                        if (!b.disabled) {
                            s.activeSharesPage = parseInt(b.dataset.page);
                            this._renderWidget("active_shares");
                        }
                    });
                });
            },

            // ─── Top Uploaders ────────────────────────────────
            top_uploaders: el => {
                const d = s.top_uploaders;
                if (!d || !d.length) { el.innerHTML = this._empty("fa-trophy", "No upload data"); return; }
                const medals = ["🥇","🥈","🥉"];
                el.innerHTML = `<div class="gd-scroll gd-lboard">${d.map((u,i) => `
                  <div class="gd-lead-row">
                    <span class="gd-lead-medal">${medals[i] || (i+1)}</span>
                    <div class="gd-lead-av"><i class="fa fa-user-circle"></i></div>
                    <div class="gd-lead-info">
                      <span class="gd-lead-name">${_esc(u.user_name || "Unknown")}</span>
                      <span class="gd-lead-meta">${u.total_formatted}</span>
                    </div>
                    <span class="gd-lead-cnt">${u.upload_count} files</span>
                  </div>`).join("")}</div>`;
            },

            // ─── Model Breakdown ──────────────────────────────
            model_breakdown: el => {
                const d = s.model_breakdown;
                if (!d || !d.length) { el.innerHTML = this._empty("fa-tasks", "No active sync configurations"); return; }

                const _renderMBC = (filter = "", sortKey = "name") => {
                    let rows = d.filter(m => !filter || m.name.toLowerCase().includes(filter.toLowerCase()) || (m.model_name||"").toLowerCase().includes(filter.toLowerCase()));
                    if (sortKey === "pct")  rows = [...rows].sort((a,b) => b.sync_pct - a.sync_pct);
                    if (sortKey === "total") rows = [...rows].sort((a,b) => b.total - a.total);
                    if (sortKey === "name")  rows = [...rows].sort((a,b) => a.name.localeCompare(b.name));

                    const totFiles  = d.reduce((s,m) => s + (m.total||0), 0);
                    const totSynced = d.reduce((s,m) => s + (m.synced||0), 0);
                    const totPend   = d.reduce((s,m) => s + (m.unsynced||0), 0);
                    // Weighted average: total synced across ALL configs / total files across ALL configs
                    const avgPct    = totFiles > 0 ? Math.round(totSynced / totFiles * 100) : 0;

                    const modeClass = (mode) => {
                        const lm = (mode||"").toLowerCase();
                        if (lm.includes("drive") && !lm.includes("dual")) return "drive";
                        if (lm.includes("dual"))  return "dual";
                        if (lm.includes("odoo"))  return "odoo";
                        return "drive";
                    };

                    const cards = rows.map(m => {
                        const pct = m.sync_pct || 0;
                        const fillCls = pct >= 90 ? "gd-mbc-fill--full" : pct < 30 ? "gd-mbc-fill--low" : "";
                        const modCls  = modeClass(m.storage_mode);
                        return `
                        <div class="gd-mbc-card" data-cfg-id="${m.id}" style="cursor:pointer;" title="Click to open ${_esc(m.name)}">
                          <div class="gd-mbc-header">
                            <div class="gd-mbc-icon"><i class="fa fa-cube"></i></div>
                            <div class="gd-mbc-title">
                              <h4 title="${_esc(m.name)}">${_esc(m.name)}</h4>
                              <small>${_esc(m.model_name || "—")}</small>
                            </div>
                            <span class="gd-mbc-mode gd-mbc-mode--${modCls}">${_esc(m.storage_mode)}</span>
                          </div>
                          <div class="gd-mbc-pbar-wrap">
                            <div class="gd-mbc-pbar-top">
                              <span class="gd-mbc-pbar-lbl">Sync Progress</span>
                              <span class="gd-mbc-pbar-pct">${pct}%</span>
                            </div>
                            <div class="gd-mbc-track"><div class="gd-mbc-fill ${fillCls}" style="width:${pct}%"></div></div>
                          </div>
                          <div class="gd-mbc-stats">
                            <div class="gd-mbc-stat gd-mbc-stat--ok">
                              <b>${m.synced||0}</b><span>Synced</span>
                            </div>
                            <div class="gd-mbc-stat gd-mbc-stat--pend">
                              <b>${m.unsynced||0}</b><span>Pending</span>
                            </div>
                            <div class="gd-mbc-stat gd-mbc-stat--tot">
                              <b>${m.total||0}</b><span>Total</span>
                            </div>
                          </div>
                        </div>`;
                    }).join("");

                    const emptyCards = !rows.length ? `<div class="gd-empty" style="grid-column:1/-1;min-height:100px;"><i class="fa fa-search"></i><span>No matching configs</span></div>` : "";

                    el.innerHTML = `
                      <div class="gd-mbc-summary">
                        <div class="gd-mbc-sum-item"><b>${d.length}</b><small>Active Configs</small></div>
                        <div class="gd-mbc-sum-div"></div>
                        <div class="gd-mbc-sum-item"><b>${totFiles.toLocaleString()}</b><small>Total Files</small></div>
                        <div class="gd-mbc-sum-div"></div>
                        <div class="gd-mbc-sum-item"><b style="color:var(--gd-green)">${totSynced.toLocaleString()}</b><small>Synced</small></div>
                        <div class="gd-mbc-sum-div"></div>
                        <div class="gd-mbc-sum-item"><b style="color:var(--gd-orange)">${totPend.toLocaleString()}</b><small>Pending</small></div>
                        <div class="gd-mbc-sum-div"></div>
                        <div class="gd-mbc-sum-item"><b style="color:var(--gd-blue)">${avgPct}%</b><small>Avg Sync</small></div>
                      </div>
                      <div class="gd-mbc-grid gd-scroll">${cards}${emptyCards}</div>`;
                };

                _renderMBC();

                // ── Card click → open specific config form ──
                el.querySelectorAll('.gd-mbc-card[data-cfg-id]').forEach(card => {
                    card.addEventListener('click', () => {
                        const cfgId = parseInt(card.dataset.cfgId);
                        if (!cfgId) return;
                        this.action.doAction({
                            type: 'ir.actions.act_window',
                            res_model: 'attachment.sync.config',
                            res_id: cfgId,
                            views: [[false, 'form']],
                            target: 'current',
                        });
                    });
                });
            },


            // ─── Fleet Overview ───────────────────────────────
            fleet_overview: el => {
                const fleet = (s.health && s.health.fleet) || [];
                if (!fleet.length) { el.innerHTML = this._empty("fa-hdd-o", "No Drives connected"); return; }
                el.innerHTML = `<div class="gd-fleet-list">${fleet.map(drive => {
                    const q = s.fleet_quotas[drive.id] || {};
                    let quotaHtml = "";
                    if (q.loading) {
                        quotaHtml = `<div class="gd-fq-circular loading"><i class="fa fa-spinner fa-spin"></i></div>`;
                    } else if (q.available) {
                        quotaHtml = `
                          <div class="gd-fq-circular">
                            <div class="gd-fq-chart-wrap">
                                <canvas id="chart-fleet-${drive.id}"></canvas>
                                <span class="gd-fq-pct-mini">${q.usage_pct}%</span>
                            </div>
                            <div class="gd-fq-details">
                                <span class="gd-fq-used">${q.usage_formatted} / ${q.limit_formatted}</span>
                                <span class="gd-fq-label">Storage Used</span>
                            </div>
                          </div>`;
                    } else if (q.error) {
                        quotaHtml = `<div class="gd-fq-circular failure" title="${_esc(q.error)}"><small>Error</small></div>`;
                    } else {
                        quotaHtml = `<div class="gd-fq-circular failure"><small>Quota N/A</small></div>`;
                    }
                    return `<div class="gd-fleet-row gd-fleet-row--${drive.is_connected?"on":"off"}">
                      <div class="gd-fleet-id">
                        <div class="gd-fleet-ico"><i class="fa fa-hdd-o"></i></div>
                        <div>
                          <div class="gd-fleet-name">${_esc(drive.name)}</div>
                          <div class="gd-fleet-st">
                            <span class="gd-dot2 gd-dot2--${drive.is_connected?"on":"off"}"></span>
                            ${drive.is_connected ? "Connected" : "Offline"}
                          </div>
                        </div>
                      </div>
                      <div class="gd-fleet-folders"><b>${drive.folders_count}</b><small>Folders</small></div>
                      <div class="gd-fleet-odoo">
                        <div class="gd-fleet-odoo-item"><b>${drive.odoo_file_count}</b><small>Synced Files</small></div>
                        <div class="gd-fleet-odoo-item"><b>${drive.odoo_storage_formatted}</b><small>Odoo Space</small></div>
                      </div>
                      ${quotaHtml}
                      <button class="gd-btn gd-btn--ghost gd-btn--sm gd-fleet-cfg" data-id="${drive.id}"><i class="fa fa-sliders"></i></button>
                    </div>`;
                }).join("")}</div>`;
                
                // Initialize small donuts for each drive
                fleet.forEach(drive => {
                    const q = s.fleet_quotas[drive.id];
                    if (q && q.available) {
                        const can = el.querySelector(`#chart-fleet-${drive.id}`);
                        if (can) {
                            const pct = q.usage_pct || 0;
                            const clr = pct > 90 ? "#ea4335" : pct > 70 ? "#fbbc04" : "#34a853";
                            this._chart(`fleet_${drive.id}`, can, {
                                type: "doughnut",
                                data: { datasets: [{ data:[pct, 100-pct], backgroundColor:[clr, "#e2e8f0"], borderWidth:0 }] },
                                options: { cutout:"70%", responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false},tooltip:{enabled:false}} }
                            });
                        }
                    }
                });

                el.querySelectorAll(".gd-fleet-cfg").forEach(b =>
                    b.addEventListener("click", () => this._openDriveSettings(parseInt(b.dataset.id))));
            },
        };
    }

    // ── Empty state helper ────────────────────────────────────────────────────
    _empty(icon, msg) {
        return `<div class="gd-empty"><i class="fa ${icon}"></i><span>${msg}</span></div>`;
    }

    // ── Chart helper (destroy-before-create) ──────────────────────────────────
    _chart(id, canvas, config) {
        if (!canvas || typeof Chart === "undefined") return;
        if (this._charts[id]) { try { this._charts[id].destroy(); } catch (_) {} }
        this._charts[id] = new Chart(canvas.getContext("2d"), config);
    }

    // ── Add / Remove widgets ──────────────────────────────────────────────────

    toggleWidget(widgetId) {
        if (this.state.activeWidgets.includes(widgetId)) {
            this.removeWidget(widgetId);
        } else {
            this.addWidget(widgetId);
        }
    }

    addWidget(widgetId) {
        if (this.state.activeWidgets.includes(widgetId)) return;
        this.state.activeWidgets = [...this.state.activeWidgets, widgetId];
        // onPatched will fire and _renderPendingWidgets will fill it
        this._saveLayout();
    }

    removeWidget(widgetId) {
        if (this._charts[widgetId]) {
            try { this._charts[widgetId].destroy(); } catch (_) {}
            delete this._charts[widgetId];
        }
        this.state.activeWidgets = this.state.activeWidgets.filter(id => id !== widgetId);
        this._saveLayout();
    }

    // ── Drag-and-drop (HTML5 native) ──────────────────────────────────────────

    onDragStart(ev) {
        this._dragSrcId = ev.currentTarget.dataset.widgetId;
        ev.dataTransfer.effectAllowed = "move";
        ev.currentTarget.classList.add("gd-dragging");
    }

    onDragOver(ev) {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
        ev.currentTarget.classList.add("gd-dragover");
    }

    onDragLeave(ev) {
        ev.currentTarget.classList.remove("gd-dragover");
    }

    onDrop(ev) {
        ev.preventDefault();
        ev.currentTarget.classList.remove("gd-dragover");
        const targetId = ev.currentTarget.dataset.widgetId;
        if (!this._dragSrcId || !targetId || this._dragSrcId === targetId) return;
        const arr = [...this.state.activeWidgets];
        const from = arr.indexOf(this._dragSrcId);
        const to   = arr.indexOf(targetId);
        if (from < 0 || to < 0) return;
        arr.splice(from, 1);
        arr.splice(to, 0, this._dragSrcId);
        this.state.activeWidgets = arr;
        this._saveLayout();
    }

    onDragEnd(ev) {
        document.querySelectorAll(".gd-dragging, .gd-dragover").forEach(el => {
            el.classList.remove("gd-dragging", "gd-dragover");
        });
        this._dragSrcId = null;
    }

    // ── Actions ───────────────────────────────────────────────────────────────

    async _filterLogs(period) {
        this.state.logFilter = period;
        this.state.activityLogPage = 1;
        try {
            const logs = await this.orm.call("one.drive.dashboard", "get_activity_logs", [], { period });
            this.state.recent_logs = logs;
            this._renderWidget("activity_log");
        } catch (e) { console.error(e); }
    }

    _openSyncLogs() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "one.drive.sync.log",
            name: "Sync Logs", views: [[false, "list"], [false, "form"]], domain: [["state","=","fail"]] });
    }

    async _openDuplicatePruner() {
        const wizardId = await this.orm.create("duplicate.pruner.wizard", [{}]);
        this.action.doAction({ type: "ir.actions.act_window", res_model: "duplicate.pruner.wizard",
            res_id: wizardId[0], name: "Prune Duplicates", views: [[false, "form"]], target: "new" });
    }

    _openDriveSettings(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "one.drive.config",
            res_id: id, name: "Drive Configuration", views: [[false, "form"]], target: "current" });
    }

    /**
     * Open the existing file explorer ShareDriveLinkDialog for a file from the dashboard.
     * Reuses the same wizard component without duplicating any logic.
     */
    _openShareWizard(file) {
        this.state.loaderMessage = "Opening share settings...";
        this.state.showActionLoader = true;
        this.dialogService.add(ShareDriveLinkDialog, {
            files: [file],
            onReady: () => {
                this.state.showActionLoader = false;
            }
        });
    }

    openFileExplorer() {
        this.action.doAction({ type: "ir.actions.client",
            tag: "dropbox_odoo_integration.file_explorer", name: "File Explorer" });
    }

    openActivityLogs() {
        this.action.doAction({ type: "ir.actions.client",
            tag: "dropbox_odoo_integration.sync_log_terminal", name: "Activity Log Terminal" });
    }

    configureModels() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "attachment.sync.config",
            name: "Sync Configurations", views: [[false, "kanban"], [false, "list"], [false, "form"]] });
    }

    async resetLayout() {
        this.state.activeWidgets = [...DEFAULT_WIDGETS];
        this.state.showAddPanel = false;
        await this._saveLayout();
        this._renderAllWidgets();
    }
}

GoogleDriveDashboard.template = "dropbox_odoo_integration.Dashboard";
registry.category("actions").add("dropbox_odoo_integration.dashboard", GoogleDriveDashboard);
