/** @odoo-module **/

import { Component, onWillStart, useState, onMounted, onWillDestroy } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class FileExplorer extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.state = useState({
            files: [],
            allFiles: [],
            currentFolderId: null,
            currentFolderName: '',
            loading: true,
            viewMode: 'list',
            searchQuery: '',
            activeSection: 'my_drive',
            breadcrumbs: [],
            drives: [],
            activeDriveId: null,
            activeRootId: null,
            rootFolders: [],
            showAccountMenu: false,
            // Inline folder creation
            creatingFolder: false,
            newFolderName: '',
            // File selection
            selectedFiles: {},
            selectionMode: false,
            syncing: false,
            syncCompleted: false,
            // Inline renaming
            renamingFileId: null,
            renameValue: '',
            // Search filters
            searchMode: false,
            searchFilterDrive: null,
            searchRoots: [],
            searchFilterType: '',
            searchFilterRoot: null,
            searchFilterModified: '',
            showDriveDropdown: false,
            showTypeDropdown: false,
            showRootDropdown: false,
            showModifiedDropdown: false,
        });

        const onWindowClick = (ev) => this.onWindowClick(ev);
        onMounted(() => {
            window.addEventListener("click", onWindowClick);
        });
        onWillDestroy(() => {
            window.removeEventListener("click", onWindowClick);
        });

        onWillStart(async () => {
            await this.loadDrives();
            if (this.state.activeDriveId) {
                await this.loadRoots(this.state.activeDriveId);
            }
            const driveName = this.activeDriveName;
            this.state.currentFolderName = driveName;
            this.state.breadcrumbs = [{ id: 'section', name: driveName }];
            if (this.state.activeRootId) {
                const root = this.state.rootFolders.find(r => r.id === this.state.activeRootId);
                if (root) {
                    this.state.breadcrumbs.push({ id: null, name: root.name });
                    this.state.currentFolderName = root.name;
                }
            }
            await this.loadFiles(this.state.currentFolderId);
        });
    }

    async loadDrives() {
        const drives = await this.orm.searchRead(
            "google.drive.config",
            [["active", "=", true]],
            ["name", "state"]
        );
        this.state.drives = drives;
        // Auto-select first drive
        if (drives.length > 0 && !this.state.activeDriveId) {
            this.state.activeDriveId = drives[0].id;
        }
    }

    async loadRoots(driveId) {
        const roots = await this.orm.searchRead(
            "google.drive.root.folder",
            [["config_id", "=", driveId], ["active", "=", true]],
            ["name", "root_id"]
        );
        this.state.rootFolders = roots;
        if (roots.length > 0) {
            this.state.activeRootId = roots[0].id;
        } else {
            this.state.activeRootId = null;
        }
    }

    get hasNoDrives() {
        return this.state.drives.length === 0;
    }

    get activeDriveName() {
        const drive = this.state.drives.find(d => d.id === this.state.activeDriveId);
        return drive ? drive.name : 'No Drive';
    }

    // ─── Account / Drive Switcher ───

    toggleAccountMenu() {
        const target = !this.state.showAccountMenu;
        this.closeAllMenus();
        this.state.showAccountMenu = target;
    }

    closeAccountMenu() {
        this.state.showAccountMenu = false;
    }

    async switchDrive(driveId) {
        this.state.activeDriveId = driveId;
        await this.loadRoots(driveId);
        const driveName = this.activeDriveName;
        this.state.showAccountMenu = false;
        this.state.breadcrumbs = [{ id: 'section', name: driveName }];
        if (this.state.activeRootId) {
            const root = this.state.rootFolders.find(r => r.id === this.state.activeRootId);
            if (root) {
                this.state.breadcrumbs.push({ id: null, name: root.name });
                this.state.currentFolderName = root.name;
            }
        } else {
            this.state.currentFolderName = driveName;
        }
        await this.loadFiles(null);
    }

    async switchRoot(rootId) {
        this.state.activeRootId = rootId;
        this.state.activeSection = 'my_drive';
        const root = this.state.rootFolders.find(r => r.id === rootId);
        const driveName = this.activeDriveName;
        const rootName = root ? root.name : 'Root';

        this.state.currentFolderName = rootName;
        this.state.breadcrumbs = [
            { id: 'section', name: driveName },
            { id: null, name: rootName }
        ];
        await this.loadFiles(null);
    }

    async loadFiles(folderId = null) {
        if (this.hasNoDrives || !this.state.activeRootId) {
            this.state.loading = false;
            this.state.files = [];
            this.state.allFiles = [];
            return;
        }
        this.state.loading = true;
        this.state.currentFolderId = folderId;
        this.clearSelection();
        const domain = [
            ["parent_folder_id", "=", folderId],
            ["drive_config_id", "=", this.state.activeDriveId],
            ["root_folder_id", "=", this.state.activeRootId],
        ];

        const files = await this.orm.searchRead("google.drive.file", domain, [
            "name", "file_type", "mime_type", "google_url", "file_size",
            "owner_name", "last_modified", "sync_state", "starred",
            "drive_config_id", "google_file_id", "attachment_id",
        ]);

        this.state.allFiles = files;
        this.state.files = files;
        this.state.loading = false;
        await this.checkPendingChanges();
    }

    // ─── Formatting helpers ───

    formatFileSize(sizeBytes) {
        if (sizeBytes == null || sizeBytes === 0) return '–';
        if (sizeBytes < 1024) return sizeBytes.toFixed(0) + ' B';
        const kb = sizeBytes / 1024;
        if (kb < 1024) return kb.toFixed(0) + ' KB';
        const mb = kb / 1024;
        if (mb < 1024) return mb.toFixed(1) + ' MB';
        const gb = mb / 1024;
        return gb.toFixed(1) + ' GB';
    }

    formatDate(dateStr) {
        if (!dateStr) return '–';
        const d = new Date(dateStr);
        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        return `${months[d.getMonth()]} ${String(d.getDate()).padStart(2, '0')}, ${d.getFullYear()}`;
    }

    getFileIcon(file) {
        if (file.file_type === 'folder') return 'fa-folder';
        const mime = file.mime_type || '';
        if (mime.includes('pdf')) return 'fa-file-pdf-o';
        if (mime.includes('image')) return 'fa-file-image-o';
        if (mime.includes('video')) return 'fa-file-video-o';
        if (mime.includes('audio')) return 'fa-file-audio-o';
        if (mime.includes('spreadsheet') || mime.includes('excel') || mime.includes('csv')) return 'fa-file-excel-o';
        if (mime.includes('document') || mime.includes('word') || mime.includes('msword')) return 'fa-file-word-o';
        if (mime.includes('presentation') || mime.includes('powerpoint')) return 'fa-file-powerpoint-o';
        if (mime.includes('zip') || mime.includes('archive') || mime.includes('compressed')) return 'fa-file-archive-o';
        return 'fa-file-text-o';
    }

    getFileImageUrl(file, isPreview = false) {
        if (file.file_type === 'folder') return false;

        const mime = file.mime_type || '';
        const basePath = '/google_drive_odoo_integration/static/description/';

        if (mime.includes('image')) {
            if (isPreview && file.google_file_id) {
                return `https://drive.google.com/uc?export=view&id=${file.google_file_id}`;
            }
            return basePath + 'photo.png';
        }

        if (mime.includes('pdf')) return basePath + 'pdf.png';
        if (mime.includes('video')) return basePath + 'vedio.png';
        if (mime.includes('audio')) return basePath + 'audio.png';
        if (mime.includes('spreadsheet') || mime.includes('excel') || mime.includes('csv')) return basePath + 'xls.png';
        if (mime.includes('document') || mime.includes('word') || mime.includes('text')) return basePath + 'doc.png';
        if (mime.includes('zip') || mime.includes('archive') || mime.includes('compressed') || mime.includes('rar')) return basePath + 'zip.png';

        // Fallback for generic files
        return basePath + 'doc.png';
    }

    getFileIconColor(file) {
        if (file.file_type === 'folder') return '#5f6368';
        const mime = file.mime_type || '';
        if (mime.includes('pdf')) return '#ea4335';
        if (mime.includes('image')) return '#4285f4';
        if (mime.includes('video')) return '#ea4335';
        if (mime.includes('audio')) return '#fbbc04';
        if (mime.includes('spreadsheet') || mime.includes('excel')) return '#0f9d58';
        if (mime.includes('document') || mime.includes('word')) return '#4285f4';
        if (mime.includes('presentation') || mime.includes('powerpoint')) return '#fbbc04';
        return '#5f6368';
    }

    // ─── Search ───

    onSearchFocus(ev) {
        if (!this.state.searchMode && this.state.activeDriveId) {
            this.state.searchFilterDrive = this.state.activeDriveId;
            this.state.searchRoots = this.state.rootFolders;
            this.state.searchMode = true;
            this.state.files = []; // Optional: clear view or show current files
            this.executeSearch();  // Run an empty search to show results correctly
        }
    }

    onSearchInput(ev) {
        this.state.searchQuery = ev.target.value;
        clearTimeout(this.searchTimeout);

        if (this.state.searchQuery.trim() === '') {
            this.clearSearch();
        } else {
            this.searchTimeout = setTimeout(() => {
                this.executeSearch();
            }, 300);
        }
    }

    onSearchKeydown(ev) {
        if (ev.key === 'Enter') {
            clearTimeout(this.searchTimeout);
            if (this.state.searchQuery.trim() === '') {
                this.clearSearch();
            } else {
                this.executeSearch();
            }
        } else if (ev.key === 'Escape') {
            this.clearSearch();
        }
    }

    async executeSearch() {
        if (!this.state.activeDriveId) return;

        // Initialize filters on first search
        if (!this.state.searchMode) {
            this.state.searchFilterDrive = this.state.activeDriveId;
            this.state.searchRoots = this.state.rootFolders;
        }

        this.state.searchMode = true;
        this.state.loading = true;
        this.clearSelection();

        const query = this.state.searchQuery.trim();
        let domain = [
            ["name", "ilike", query]
        ];

        if (this.state.searchFilterDrive) {
            domain.push(["drive_config_id", "=", this.state.searchFilterDrive]);
        }

        if (this.state.searchFilterRoot) {
            domain.push(["root_folder_id", "=", this.state.searchFilterRoot]);
        }

        if (this.state.searchFilterType) {
            const type = this.state.searchFilterType;
            if (type === 'folder') {
                domain.push(["file_type", "=", "folder"]);
            } else {
                domain.push(["file_type", "!=", "folder"]);
                if (type === 'document') {
                    domain.push("|", "|", ["mime_type", "ilike", "document"], ["mime_type", "ilike", "word"], ["mime_type", "ilike", "text"]);
                } else if (type === 'image') {
                    domain.push(["mime_type", "ilike", "image"]);
                } else if (type === 'video') {
                    domain.push(["mime_type", "ilike", "video"]);
                } else if (type === 'audio') {
                    domain.push(["mime_type", "ilike", "audio"]);
                } else if (type === 'spreadsheet') {
                    domain.push("|", "|", ["mime_type", "ilike", "spreadsheet"], ["mime_type", "ilike", "excel"], ["mime_type", "ilike", "csv"]);
                } else if (type === 'presentation') {
                    domain.push("|", ["mime_type", "ilike", "presentation"], ["mime_type", "ilike", "powerpoint"]);
                } else if (type === 'archive') {
                    domain.push("|", "|", ["mime_type", "ilike", "zip"], ["mime_type", "ilike", "archive"], ["mime_type", "ilike", "compressed"]);
                } else if (type === 'pdf') {
                    domain.push(["mime_type", "ilike", "pdf"]);
                }
            }
        }

        if (this.state.searchFilterModified) {
            const modDate = new Date();
            const formatStr = (d) => d.toISOString().replace('T', ' ').substring(0, 19);

            if (this.state.searchFilterModified === 'today') {
                modDate.setHours(0, 0, 0, 0);
                domain.push(["last_modified", ">=", formatStr(modDate)]);
            } else if (this.state.searchFilterModified === '7days') {
                modDate.setDate(modDate.getDate() - 7);
                domain.push(["last_modified", ">=", formatStr(modDate)]);
            } else if (this.state.searchFilterModified === '30days') {
                modDate.setDate(modDate.getDate() - 30);
                domain.push(["last_modified", ">=", formatStr(modDate)]);
            } else if (this.state.searchFilterModified === 'year') {
                modDate.setFullYear(modDate.getFullYear() - 1);
                domain.push(["last_modified", ">=", formatStr(modDate)]);
            }
        }

        try {
            const files = await this.orm.searchRead("google.drive.file", domain, [
                "name", "file_type", "mime_type", "google_url", "file_size",
                "owner_name", "last_modified", "sync_state", "starred",
                "drive_config_id", "google_file_id", "attachment_id",
            ]);
            this.state.files = files;
        } catch (e) {
            console.error("Search failed", e);
            this.notification.add("Search failed.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    clearSearch() {
        this.state.searchQuery = '';
        this.state.searchMode = false;
        this.state.searchFilterDrive = null;
        this.state.searchRoots = [];
        this.state.searchFilterType = '';
        this.state.searchFilterRoot = null;
        this.state.searchFilterModified = '';
        this.loadFiles(this.state.currentFolderId);
    }

    toggleDriveDropdown() {
        const tgt = !this.state.showDriveDropdown;
        this.closeAllMenus();
        this.state.showDriveDropdown = tgt;
    }

    toggleTypeDropdown() {
        const tgt = !this.state.showTypeDropdown;
        this.closeAllMenus();
        this.state.showTypeDropdown = tgt;
    }

    toggleRootDropdown() {
        const tgt = !this.state.showRootDropdown;
        this.closeAllMenus();
        this.state.showRootDropdown = tgt;
    }

    toggleModifiedDropdown() {
        const tgt = !this.state.showModifiedDropdown;
        this.closeAllMenus();
        this.state.showModifiedDropdown = tgt;
    }

    async setSearchFilterDrive(driveId) {
        this.state.searchFilterDrive = driveId;
        this.state.showDriveDropdown = false;

        // Reset root folder related to old drive
        this.state.searchFilterRoot = null;

        // Fetch roots for the new selected drive
        if (driveId) {
            this.state.searchRoots = await this.orm.searchRead(
                "google.drive.root.folder",
                [["config_id", "=", driveId], ["active", "=", true]],
                ["name", "root_id"]
            );
        } else {
            this.state.searchRoots = [];
        }

        this.executeSearch();
    }

    setSearchFilterType(type) {
        this.state.searchFilterType = type;
        this.state.showTypeDropdown = false;
        this.executeSearch();
    }

    setSearchFilterRoot(rootId) {
        this.state.searchFilterRoot = rootId;
        this.state.showRootDropdown = false;
        this.executeSearch();
    }

    setSearchFilterModified(period) {
        this.state.searchFilterModified = period;
        this.state.showModifiedDropdown = false;
        this.executeSearch();
    }

    getTypeFilterOptions() {
        return [
            { value: 'folder', label: 'Folders' },
            { value: 'document', label: 'Documents' },
            { value: 'image', label: 'Images' },
            { value: 'video', label: 'Videos' },
            { value: 'audio', label: 'Audio' },
            { value: 'spreadsheet', label: 'Spreadsheets' },
            { value: 'presentation', label: 'Presentations' },
            { value: 'archive', label: 'Archives' },
            { value: 'pdf', label: 'PDFs' },
        ];
    }

    getModifiedFilterOptions() {
        return [
            { value: 'today', label: 'Today' },
            { value: '7days', label: 'Last 7 days' },
            { value: '30days', label: 'Last 30 days' },
            { value: 'year', label: 'This year' },
        ];
    }

    getTypeFilterName() {
        const opt = this.getTypeFilterOptions().find(o => o.value === this.state.searchFilterType);
        return opt ? opt.label : '';
    }

    getDriveFilterName() {
        if (!this.state.searchFilterDrive) return 'All Drives';
        const drive = this.state.drives.find(d => d.id === this.state.searchFilterDrive);
        return drive ? drive.name : 'All Drives';
    }

    getRootFilterName() {
        if (!this.state.searchFilterRoot) return '';
        const root = this.state.searchRoots.find(r => r.id === this.state.searchFilterRoot);
        return root ? root.name : '';
    }

    getModifiedFilterName() {
        const opt = this.getModifiedFilterOptions().find(o => o.value === this.state.searchFilterModified);
        return opt ? opt.label : '';
    }

    // ─── Sidebar navigation ───

    async onSidebarClick(section) {
        this.state.activeSection = section;
        let breadcrumbs = [{ id: 'section', name: this._sectionLabel(section) }];

        if (section === 'my_drive' && this.state.activeRootId) {
            const root = this.state.rootFolders.find(r => r.id === this.state.activeRootId);
            if (root) {
                breadcrumbs.push({ id: null, name: root.name });
            }
        }

        this.state.breadcrumbs = breadcrumbs;
        this.state.currentFolderName = breadcrumbs[breadcrumbs.length - 1].name;

        if (section === 'starred') {
            this.state.loading = true;
            const files = await this.orm.searchRead("google.drive.file",
                [["starred", "=", true]],
                ["name", "file_type", "mime_type", "google_url", "file_size",
                    "owner_name", "last_modified", "sync_state", "starred", "drive_config_id", "google_file_id", "attachment_id"]
            );
            this.state.allFiles = files;
            this.state.files = files;
            this.state.loading = false;
        } else if (section === 'recent') {
            this.state.loading = true;
            const recentIds = JSON.parse(localStorage.getItem('gd_recent_file_ids') || '[]');
            if (recentIds.length === 0) {
                this.state.allFiles = [];
                this.state.files = [];
            } else {
                const files = await this.orm.searchRead("google.drive.file",
                    [["id", "in", recentIds], ["file_type", "!=", "folder"]],
                    ["name", "file_type", "mime_type", "google_url", "file_size",
                        "owner_name", "last_modified", "sync_state", "starred", "drive_config_id", "google_file_id", "display_path", "parent_folder_id", "root_folder_id", "attachment_id"]
                );
                // Sort the files to match the stack order (most recent first)
                files.sort((a, b) => recentIds.indexOf(a.id) - recentIds.indexOf(b.id));
                this.state.allFiles = files;
                this.state.files = files;
            }
            this.state.loading = false;
        } else {
            await this.loadFiles(null);
        }
    }

    _sectionLabel(section) {
        const labels = {
            my_drive: this.activeDriveName,
            shared_drives: 'Shared Drives',
            shared_with_me: 'Shared with me',
            recent: 'Recent Files',
            starred: 'Starred',
            trash: 'Trash',
        };
        return labels[section] || this.activeDriveName;
    }

    // ─── Folder navigation ───

    async onFolderClick(file) {
        if (file.file_type === 'folder') {
            this.state.breadcrumbs.push({ id: file.id, name: file.name });
            this.state.currentFolderName = file.name;
            await this.loadFiles(file.id);
        }
    }

    async onBreadcrumbClick(index) {
        const bc = this.state.breadcrumbs[index];
        this.state.breadcrumbs = this.state.breadcrumbs.slice(0, index + 1);
        this.state.currentFolderName = bc.name;

        // If they click the Drive name, we still want to show the root files of the active root
        const folderId = bc.id === 'section' ? null : bc.id;
        await this.loadFiles(folderId);
    }

    async onBackClick() {
        if (this.state.breadcrumbs.length > 1) {
            this.state.breadcrumbs.pop();
            const last = this.state.breadcrumbs[this.state.breadcrumbs.length - 1];
            this.state.currentFolderName = last.name;
            await this.loadFiles(last.id);
        }
    }

    // ─── View mode ───

    setViewMode(mode) {
        this.state.viewMode = mode;
    }

    async onRefresh() {
        await this.loadFiles(this.state.currentFolderId);
    }

    // ─── File click: single click = select, double click = open ───

    addToRecent(file) {
        if (!file || file.file_type === 'folder') return;
        let recentIds = JSON.parse(localStorage.getItem('gd_recent_file_ids') || '[]');
        // Remove if it exists
        recentIds = recentIds.filter(id => id !== file.id);
        // Add to the top of the stack
        recentIds.unshift(file.id);
        // Keep only top 20
        if (recentIds.length > 20) {
            recentIds = recentIds.slice(0, 20);
        }
        localStorage.setItem('gd_recent_file_ids', JSON.stringify(recentIds));
    }

    clearRecent() {
        localStorage.removeItem('gd_recent_file_ids');
        this.state.files = [];
        this.state.allFiles = [];
    }

    onFileClick(ev, file) {
        // Single click always toggles selection
        this.toggleFileSelection(ev, file);
    }

    onFileDblClick(file) {
        this.addToRecent(file);
        if (file.file_type === 'folder') {
            this.onFolderClick(file);
        } else {
            const previewableTypes = ['image', 'pdf', 'video', 'audio', 'text', 'document', 'spreadsheet', 'presentation'];
            const mime = file.mime_type || '';
            const isPreviewable = previewableTypes.some(t => mime.includes(t));

            if (isPreviewable && file.google_file_id) {
                this.dialog.add(FilePreviewDialog, {
                    file: file,
                    onDownload: () => {
                        const downloadUrl = `/google_drive/download/${file.id}`;
                        window.open(downloadUrl, '_blank');
                    },
                    onOpenDetails: () => this.action.doAction({
                        type: 'ir.actions.act_window',
                        res_model: 'google.drive.file',
                        res_id: file.id,
                        views: [[false, 'form']],
                        target: 'new',
                        context: {},
                    }),
                });
            } else {
                // Open form view as wizard dialog for non-previewable or missing ID
                this.action.doAction({
                    type: 'ir.actions.act_window',
                    res_model: 'google.drive.file',
                    res_id: file.id,
                    views: [[false, 'form']],
                    target: 'new',
                    context: {},
                });
            }
        }
    }

    // ─── File Selection ───

    toggleFileSelection(ev, file) {
        ev.stopPropagation();
        const selected = { ...this.state.selectedFiles };
        if (selected[file.id]) {
            delete selected[file.id];
        } else {
            selected[file.id] = file;
        }
        this.state.selectedFiles = selected;
        this.state.selectionMode = Object.keys(selected).length > 0;
    }

    isFileSelected(file) {
        return !!this.state.selectedFiles[file.id];
    }

    get selectedCount() {
        return Object.keys(this.state.selectedFiles).length;
    }

    get selectedFilesList() {
        return Object.values(this.state.selectedFiles);
    }

    clearSelection() {
        this.state.selectedFiles = {};
        this.state.selectionMode = false;
    }

    // ─── Selection Actions ───

    onOpenSelected() {
        const files = this.selectedFilesList;
        if (files.length === 1) {
            this.onFileDblClick(files[0]);
            this.clearSelection();
        }
    }

    onShareSelected() {
        const files = this.selectedFilesList;
        if (files.length === 0) return;

        // Build share links
        const links = files
            .filter(f => f.google_url)
            .map(f => f.google_url);

        if (links.length === 0) {
            this.notification.add("No shareable links available for the selected files.", { type: "warning" });
            return;
        }

        // Copy links to clipboard
        const linkText = links.join('\n');
        navigator.clipboard.writeText(linkText).then(() => {
            this.notification.add(
                `${links.length} share link(s) copied to clipboard!`,
                { type: "success" }
            );
        }).catch(() => {
            // Fallback: open first link
            window.open(links[0], '_blank');
        });
    }

    onDownloadSelected() {
        const files = this.selectedFilesList;
        if (files.length === 0) return;

        let downloadCount = 0;
        for (const file of files) {
            if (file.file_type !== 'folder') {
                const downloadUrl = `/google_drive/download/${file.id}`;
                window.open(downloadUrl, '_blank');
                downloadCount++;
            }
        }

        if (downloadCount > 0) {
            this.notification.add(`Downloading ${downloadCount} file(s)...`, { type: "info" });
        } else {
            this.notification.add("No downloadable files in selection.", { type: "warning" });
        }
    }

    async onDeleteSelected() {
        const files = this.selectedFilesList;
        if (files.length === 0) return;

        const ids = files.map(f => f.id);
        const names = files.map(f => f.name);
        const nameList = names.join(', ');

        this.clearSelection();

        try {
            // Step 1: Archive instantly (hide from user)
            await this.orm.call("google.drive.file", "action_archive_recursive", [ids]);

            // Reload UI instantly
            if (this.state.activeSection === 'recent') {
                await this.onSidebarClick('recent');
            } else if (this.state.searchMode) {
                await this.executeSearch();
            } else {
                await this.loadFiles(this.state.currentFolderId);
            }

            // Step 2: Try background delete ONLY in Auto Sync mode
            if (this.state.syncMode === 'auto') {
                this.orm.call("google.drive.file", "delete_on_drive_and_unlink", [ids])
                    .then((success) => {
                        if (success) {
                            this.notification.add(
                                `Deleted from Google Drive: ${nameList}`,
                                { type: "success" }
                            );
                        } else {
                            this.notification.add(
                                "Odoo items hidden. Some items will be deleted from Google Drive during the next sync.",
                                { type: "info" }
                            );
                        }
                    }).catch(() => {
                        console.log("Background Drive deletion deferred to next sync.");
                    });
            } else {
                // Manual Sync Mode
                this.notification.add(
                    "Item(s) archived locally. Please click 'Sync' to remove from Google Drive.",
                    { type: "info" }
                );
            }
            await this.checkPendingChanges();
        } catch (e) {
            this.notification.add("Failed to delete: " + (e.message || "Unknown error"), { type: "danger" });
        }
    }

    // ─── Rename ───

    onRenameSelected() {
        const files = this.selectedFilesList;
        if (files.length !== 1) return;

        const file = files[0];
        this.state.renamingFileId = file.id;
        this.state.renameValue = file.name;

        // Focus the input after render
        setTimeout(() => {
            const input = document.querySelector('.gd_rename_input, .gd_grid_rename_input');
            if (input) {
                input.focus();
                input.select();
            }
        }, 50);
    }

    onRenameInput(ev) {
        this.state.renameValue = ev.target.value;
    }

    onRenameKeydown(ev, file) {
        if (ev.key === 'Enter') {
            this.confirmRename(file);
        } else if (ev.key === 'Escape') {
            this.cancelRename();
        }
    }

    async confirmRename(file) {
        if (this.state.renamingFileId !== file.id) return;

        const newName = this.state.renameValue.trim();
        const fileId = file.id;

        if (!newName || newName === file.name) {
            this.cancelRename();
            return;
        }

        this.state.renamingFileId = null;
        this.state.renameValue = '';

        try {
            // Step 1: Update Odoo instantly
            await this.orm.write("google.drive.file", [fileId], {
                name: newName,
                sync_state: 'pending'
            });
            this.clearSelection();

            // Step 2: Reload UI instantly
            if (this.state.activeSection === 'recent') {
                await this.onSidebarClick('recent');
            } else if (this.state.searchMode) {
                await this.executeSearch();
            } else {
                await this.loadFiles(this.state.currentFolderId);
            }

            // Step 3: Call Drive rename in background only if in Auto Sync mode
            if (file.google_file_id && this.state.syncMode === 'auto') {
                this.orm.call("google.drive.file", "rename_on_drive_by_id", [], {
                    record_id: fileId,
                    new_name: newName
                }).then(() => {
                    this.loadFiles(this.state.currentFolderId); // Refresh to clear 'pending'
                    this.notification.add(`Renamed to "${newName}" on Google Drive`, { type: "success" });
                }).catch(() => {
                    this.notification.add(`Failed to rename "${newName}" on Google Drive`, { type: "warning" });
                });
            } else {
                this.notification.add("Item renamed successfully locally!", { type: "success" });
                await this.checkPendingChanges();
            }
        } catch (e) {
            this.notification.add("Failed to rename item.", { type: "danger" });
        }
    }

    cancelRename() {
        this.state.renamingFileId = null;
        this.state.renameValue = '';
        this.clearSelection();
    }

    onRenameBlur(ev, file) {
        if (this.state.renamingFileId === file.id) {
            this.confirmRename(file);
        }
    }

    async checkPendingChanges() {
        if (!this.state.activeDriveId) return;
        const count = await this.orm.searchCount("google.drive.file", [
            ["sync_state", "in", ["pending", "pending_delete"]],
            ["drive_config_id", "=", this.state.activeDriveId],
            ["root_folder_id", "=", this.state.activeRootId],
        ], { context: { active_test: false } });
        this.state.hasPendingChanges = count > 0;
    }

    // ─── Sync helpers ───

    getSyncIcon(state) {
        if (state === 'synced') return 'fa-check-circle';
        if (state === 'pending') return 'fa-refresh';
        if (state === 'error') return 'fa-exclamation-circle';
        return 'fa-circle-o';
    }

    hasSyncBadgeLabel(file) {
        return file.sync_state === 'error' || file.sync_state === 'pending';
    }

    getSyncBadgeText(file) {
        if (file.sync_state === 'error') return 'SYNCING';
        if (file.sync_state === 'pending') return 'WAITING';
        return '';
    }

    // ─── + New menu toggle ───

    closeAllMenus() {
        this.state.showAccountMenu = false;
        this.state.showSyncModeMenu = false;
        this.state.showDriveDropdown = false;
        this.state.showTypeDropdown = false;
        this.state.showRootDropdown = false;
        this.state.showModifiedDropdown = false;
    }

    onWindowClick(ev) {
        this.closeAllMenus();
    }

    // ─── Inline Folder Creation ───

    onCreateFolder() {
        this.state.creatingFolder = true;
        this.state.newFolderName = '';
        // Focus the input after render
        setTimeout(() => {
            const input = document.querySelector('.gd_folder_input');
            if (input) input.focus();
        }, 50);
    }

    onFolderNameInput(ev) {
        this.state.newFolderName = ev.target.value;
    }

    onFolderNameKeydown(ev) {
        if (ev.key === 'Enter') {
            this.confirmCreateFolder();
        } else if (ev.key === 'Escape') {
            this.cancelCreateFolder();
        }
    }

    async confirmCreateFolder() {
        if (!this.state.creatingFolder) return;

        const name = this.state.newFolderName.trim();
        if (!name) {
            this.notification.add("Please enter a folder name.", { type: "warning" });
            return;
        }

        let driveConfigId = false;
        if (this.state.drives.length > 0) {
            driveConfigId = this.state.drives[0].id;
        }

        if (!driveConfigId) {
            this.notification.add("No active drive configured.", { type: "warning" });
            this.cancelCreateFolder();
            return;
        }

        // Reset state immediately to prevent double-trigger
        this.state.creatingFolder = false;
        this.state.newFolderName = '';

        try {
            await this.orm.create("google.drive.file", [{
                name: name,
                file_type: 'folder',
                drive_config_id: driveConfigId,
                root_folder_id: this.state.activeRootId,
                parent_folder_id: this.state.currentFolderId || false,
                owner_name: 'Me',
                sync_state: 'pending',
            }]);
            await this.loadFiles(this.state.currentFolderId);
            this.notification.add(`Folder "${name}" created!`, { type: "success" });

            // Auto sync in background (non-blocking)
            if (this.state.syncMode === 'auto') {
                this.triggerAutoSync();
            } else {
                await this.checkPendingChanges();
            }
        } catch (e) {
            this.notification.add("Failed to create folder.", { type: "danger" });
        }
    }

    cancelCreateFolder() {
        this.state.creatingFolder = false;
        this.state.newFolderName = '';
    }

    // ─── Upload File ───

    onUploadFile() {
        const input = document.createElement('input');
        input.type = 'file';
        input.multiple = true;
        input.addEventListener('change', async (ev) => {
            const files = ev.target.files;
            if (!files || files.length === 0) return;

            for (const file of files) {
                await this._uploadSingleFile(file);
            }
            await this.loadFiles(this.state.currentFolderId);
            this.notification.add("Files uploaded successfully!", { type: "success" });

            // Auto sync in background (non-blocking)
            if (this.state.syncMode === 'auto') {
                this.triggerAutoSync();
            } else {
                await this.checkPendingChanges();
            }
        });
        input.click();
    }

    async _uploadSingleFile(file) {
        const base64 = await new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = () => {
                const result = reader.result.split(',')[1];
                resolve(result);
            };
            reader.readAsDataURL(file);
        });

        let driveConfigId = false;
        if (this.state.drives.length > 0) {
            driveConfigId = this.state.drives[0].id;
        }

        if (!driveConfigId) {
            this.notification.add("No active drive configured.", { type: "warning" });
            return;
        }

        await this.orm.call("google.drive.file", "action_upload_from_explorer", [], {
            file_name: file.name,
            file_data: base64,
            mime_type: file.type || 'application/octet-stream',
            drive_config_id: driveConfigId,
            parent_folder_id: this.state.currentFolderId || false,
            root_folder_id: this.state.activeRootId,
        });
    }

    // ─── Manual Sync ───

    async onManualSync() {
        this.state.syncing = true;
        this.state.syncCompleted = false;
        this.notification.add("Sync started...", { type: "info" });
        try {
            // Only sync the active root folder as requested
            await this.orm.call("google.drive.config", "action_trigger_sync", [], {
                root_folder_id: this.state.activeRootId || false
            });
            await this.loadFiles(this.state.currentFolderId);
            this.notification.add("Sync completed!", { type: "success" });
            this.state.syncCompleted = true;
            await this.checkPendingChanges();
            setTimeout(() => {
                this.state.syncCompleted = false;
            }, 3000);
        } catch (e) {
            this.notification.add("Sync failed: " + (e.message || "Unknown error"), { type: "danger" });
        } finally {
            this.state.syncing = false;
        }
    }

    // ─── Sync Mode ───

    toggleSyncModeMenu() {
        const target = !this.state.showSyncModeMenu;
        this.closeAllMenus();
        this.state.showSyncModeMenu = target;
    }

    setSyncMode(mode) {
        this.state.syncMode = mode;
        this.state.showSyncModeMenu = false;
        localStorage.setItem('gd_sync_mode', mode);
    }

    async triggerAutoSync() {
        try {
            await this.orm.call("google.drive.config", "action_trigger_sync", [], {
                root_folder_id: this.state.activeRootId || false
            });
            await this.loadFiles(this.state.currentFolderId);
        } catch (e) {
            this.notification.add("Auto sync failed: " + (e.message || "Unknown error"), { type: "danger" });
        }
    }
}

FileExplorer.template = "google_drive_odoo_integration.FileExplorer";

registry.category("actions").add("google_drive_file_explorer", FileExplorer);

class FilePreviewDialog extends Component {
    static template = "google_drive_odoo_integration.FilePreviewDialog";
    static components = { Dialog };
    static props = {
        file: Object,
        close: Function,
        onDownload: Function,
        onOpenDetails: Function,
    };

    get previewUrl() {
        const { file } = this.props;
        // Use Google's standard previewer for a consistent, rich experience
        return `https://drive.google.com/file/d/${file.google_file_id}/preview`;
    }
}
