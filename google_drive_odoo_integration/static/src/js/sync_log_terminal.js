/** @odoo-module **/

import { Component, onWillStart, useState, onMounted, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class SyncLogTerminal extends Component {
    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");

        this.terminalBody = useRef("terminalBody");

        this.state = useState({
            logs: [],
            loading: true,
            filter: 'all',       // all | success | fail
            typeFilter: 'all',   // all | manual | auto | cron | upload
            operationFilter: 'all',
            searchQuery: '',
            userFilter: 'all',
            users: [],
            dateFilter: 'all',    // all | today | 3days | week | 2weeks | month
            showDateMenu: false,
            limit: 200,
            totalCount: 0,
            autoScroll: true,
            stats: { total: 0, success: 0, fail: 0 },
        });

        onWillStart(async () => {
            // Fetch internal users for the dropdown
            const users = await this.orm.searchRead(
                "res.users",
                [["share", "=", false], ["active", "=", true]],
                ["id", "name"],
                { order: "name asc" }
            );
            this.state.users = users;
            await this.loadLogs();
        });

        onMounted(() => {
            this.scrollToBottom();
        });
    }

    async loadLogs() {
        this.state.loading = true;

        const domain = this._buildDomain();

        try {
            const logs = await this.orm.searchRead(
                "google.drive.sync.log",
                domain,
                [
                    "create_date", "drive_name", "root_folder_name",
                    "file_name", "file_type", "operation", "sync_type",
                    "state", "error_message", "duration", "user_id", "user_name",
                    "google_file_id", "file_size", "display_name", "folder_path"
                ],
                { limit: this.state.limit, order: "create_date asc, id asc" }
            );

            this.state.logs = logs;

            // Load DYNAMIC stats based on CURRENT filters
            const statsDomain = this._buildDomain();
            
            // 1. Success Count for current filters
            const sDomain = statsDomain.filter(d => d[0] !== 'state');
            sDomain.push(['state', '=', 'success']);
            const successCount = await this.orm.searchCount("google.drive.sync.log", sDomain);

            // 2. Fail Count for current filters
            const fDomain = statsDomain.filter(d => d[0] !== 'state');
            fDomain.push(['state', '=', 'fail']);
            const failCount = await this.orm.searchCount("google.drive.sync.log", fDomain);

            this.state.stats = { 
                total: successCount + failCount, 
                success: successCount, 
                fail: failCount 
            };
            this.state.totalCount = successCount + failCount;
        } catch (e) {
            console.error("Failed to load logs", e);
        } finally {
            this.state.loading = false;
            if (this.state.autoScroll) {
                setTimeout(() => this.scrollToBottom(), 50);
            }
        }
    }

    _buildDomain() {
        const domain = [];
        if (this.state.filter === 'success') {
            domain.push(["state", "=", "success"]);
        } else if (this.state.filter === 'fail') {
            domain.push(["state", "=", "fail"]);
        }
        if (this.state.typeFilter !== 'all') {
            domain.push(["sync_type", "=", this.state.typeFilter]);
        }
        if (this.state.operationFilter !== 'all') {
            domain.push(["operation", "=", this.state.operationFilter]);
        }
        if (this.state.searchQuery.trim()) {
            domain.push(["file_name", "ilike", this.state.searchQuery.trim()]);
        }
        if (this.state.userFilter !== 'all') {
            const uid = parseInt(this.state.userFilter);
            if (!isNaN(uid)) {
                domain.push(["user_id", "=", uid]);
            }
        }
        if (this.state.dateFilter !== 'all') {
            const dateLimit = this._getDateLimit(this.state.dateFilter);
            if (dateLimit) {
                domain.push(["create_date", ">=", dateLimit]);
            }
        }
        return domain;
    }

    _getDateLimit(filter) {
        const d = new Date();
        if (filter === 'today') {
            d.setHours(0, 0, 0, 0);
        } else if (filter === '3days') {
            d.setDate(d.getDate() - 3);
        } else if (filter === 'week') {
            d.setDate(d.getDate() - 7);
        } else if (filter === '2weeks') {
            d.setDate(d.getDate() - 14);
        } else if (filter === 'month') {
            d.setMonth(d.getMonth() - 1);
        } else {
            return null;
        }
        // Format to Odoo datetime string YYYY-MM-DD HH:MM:SS
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
    }

    scrollToBottom() {
        const el = this.terminalBody.el;
        if (el) {
            el.scrollTop = el.scrollHeight;
        }
    }

    // ─── Formatting ───

    formatTimestamp(dateStr) {
        if (!dateStr) return '00:00:00';
        const d = new Date(dateStr);
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    getOperationLabel(op) {
        const labels = {
            'upload': 'UPLOAD',
            'download': 'DOWNLOAD',
            'rename': 'RENAME',
            'delete': 'DELETE',
            'trash': 'TRASH',
            'move': 'MOVE',
            'sync': 'SYNC',
            'create_folder': 'MKDIR',
            'share_add': 'SHARE+',
            'share_update': 'SHARE~',
            'share_remove': 'SHARE-',
            'share_general': 'ACCESS',
            'share_settings': 'SETTINGS',
        };
        return labels[op] || op?.toUpperCase() || '???';
    }

    getSyncTypeLabel(st) {
        const labels = {
            'manual': 'MANUAL',
            'auto': 'AUTO',
            'upload': 'UPLOAD',
            'cron': 'CRON',
        };
        return labels[st] || st?.toUpperCase() || '';
    }

    getStateSymbol(state) {
        return state === 'success' ? '✔' : '✘';
    }

    getStateClass(state) {
        return state === 'success' ? 'term-success' : 'term-fail';
    }

    formatDuration(dur) {
        if (!dur) return '';
        if (dur < 1) return `${(dur * 1000).toFixed(0)}ms`;
        return `${dur.toFixed(1)}s`;
    }

    formatFileSize(bytes) {
        if (!bytes) return '';
        if (bytes < 1024) return `${bytes}B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
    }

    /**
     * Parse the move transition string into styled segments.
     * Format: Drive/Root/Folder/File ➜ MOVE TO ➜ Drive/Root/Folder/File
     */
    getStyledMoveSegments(fileName) {
        if (!fileName || !fileName.includes(" ➜ MOVE TO ➜ ")) {
            return [{ text: fileName || '', class: 'term-file' }];
        }

        const parts = fileName.split(" ➜ MOVE TO ➜ ");
        const result = [];

        const parsePath = (pathStr) => {
            // Split by '/' but keep the delimiters to maintain exact UI spacing if needed
            // However, the user shown Drive/Root / Path / File
            const segments = pathStr.split('/');
            segments.forEach((seg, idx) => {
                const trimmed = seg.trim();
                let cls = "term-folder-path";
                if (idx === 0) cls = "term-drive";
                else if (idx === 1) cls = "term-folder";
                else if (idx === segments.length - 1) cls = "term-file";
                
                // Keep the leading/trailing spaces in the text to match original string
                result.push({ text: seg, class: cls });
                if (idx < segments.length - 1) {
                    result.push({ text: "/", class: "term-sep" });
                }
            });
        };

        parsePath(parts[0]);
        result.push({ text: " ➜ MOVE TO ➜ ", class: "term-move-arrow" });
        parsePath(parts[1]);

        return result;
    }

    // ─── Filters ───

    setFilter(filter) {
        this.state.filter = filter;
        this.loadLogs();
    }

    setTypeFilter(ev) {
        this.state.typeFilter = ev.target.value;
        this.loadLogs();
    }

    setOperationFilter(ev) {
        this.state.operationFilter = ev.target.value;
        this.loadLogs();
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
        clearTimeout(this._searchTimeout);
        this._searchTimeout = setTimeout(() => this.loadLogs(), 400);
    }

    onUserFilterChange(ev) {
        this.state.userFilter = ev.target.value;
        this.loadLogs();
    }

    toggleDateMenu() {
        this.state.showDateMenu = !this.state.showDateMenu;
    }

    setDateFilter(filter) {
        this.state.dateFilter = filter;
        this.state.showDateMenu = false;
        this.loadLogs();
    }

    onSearchKeydown(ev) {
        if (ev.key === 'Enter') {
            clearTimeout(this._searchTimeout);
            this.loadLogs();
        }
    }

    async onRefresh() {
        await this.loadLogs();
    }

    async onClearLogs() {
        if (!confirm("Are you sure you want to clear all logs? This cannot be undone.")) return;
        try {
            const allIds = await this.orm.search("google.drive.sync.log", []);
            if (allIds.length) {
                await this.orm.unlink("google.drive.sync.log", allIds);
            }
            await this.loadLogs();
        } catch (e) {
            console.error("Failed to clear logs", e);
        }
    }

    getOperationOptions() {
        return [
            { value: 'all', label: 'All Operations' },
            { value: 'upload', label: 'Upload' },
            { value: 'download', label: 'Download' },
            { value: 'sync', label: 'Sync' },
            { value: 'rename', label: 'Rename' },
            { value: 'delete', label: 'Delete' },
            { value: 'trash', label: 'Trash' },
            { value: 'move', label: 'Move' },
            { value: 'create_folder', label: 'Create Folder' },
            { value: 'share_add', label: 'Share — Add' },
            { value: 'share_update', label: 'Share — Update' },
            { value: 'share_remove', label: 'Share — Remove' },
            { value: 'share_general', label: 'Share — Access' },
            { value: 'share_settings', label: 'Share — Settings' },
        ];
    }
}

SyncLogTerminal.template = "google_drive_odoo_integration.SyncLogTerminal";

registry.category("actions").add("google_drive_sync_log_terminal", SyncLogTerminal);
