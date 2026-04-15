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
            limit: 200,
            totalCount: 0,
            autoScroll: true,
            stats: { total: 0, success: 0, fail: 0 },
        });

        onWillStart(async () => {
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
                    "state", "error_message", "duration", "user_id",
                    "google_file_id", "file_size", "display_name", "folder_path"
                ],
                { limit: this.state.limit, order: "create_date asc, id asc" }
            );

            this.state.logs = logs;

            // Load stats
            const allLogs = await this.orm.searchCount("google.drive.sync.log", []);
            const successLogs = await this.orm.searchCount("google.drive.sync.log", [["state", "=", "success"]]);
            const failLogs = await this.orm.searchCount("google.drive.sync.log", [["state", "=", "fail"]]);
            this.state.stats = { total: allLogs, success: successLogs, fail: failLogs };
            this.state.totalCount = allLogs;
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
        return domain;
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
            'download': 'PULL',
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
            { value: 'sync', label: 'Sync / Pull' },
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
