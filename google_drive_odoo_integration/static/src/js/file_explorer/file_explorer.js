/** @odoo-module **/

import { Component, onWillStart, useState, onMounted, onWillDestroy } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { TrashRestrictedDialog } from "./trash_restricted_dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class FileExplorer extends Component {
    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notificationService = useService("notification");
        this.dialogService = useService("dialog");
        this.busService = this.env.services.bus_service;

        if (this.busService) {
            // Subscribe to real-time sync notifications
            this.busService.addChannel('google.drive.sync');
            this.busService.subscribe('notification', (notifications) => {
                notifications.forEach(notif => {
                    if (notif.type === 'google.drive.sync' && notif.payload.type === 'folder_synced') {
                        const payload = notif.payload;
                        // If the updated folder is the one we are currently looking at, refresh the view
                        if (payload.folder_id === this.state.currentFolderId || (payload.folder_id === false && !this.state.currentFolderId)) {
                             this.loadFiles(this.state.currentFolderId);
                        }
                    }
                });
            });
        }

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
            trashBreadcrumbs: [],
            trashCurrentFolderId: null,
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
            // Per-drive auto-sync tracking
            // { driveId: true }  — true while that drive's auto-sync is in progress
            autoSyncingDrives: {},
            // { driveId: true }  — briefly true after sync completes (for icon hide timing)
            autoSyncCompleted: {},

            // Per-drive manual-sync tracking
            manualSyncingDrives: {},
            manualSyncCompleted: {},
            // Inline renaming
            renamingFileId: null,
            renameValue: '',
            // Search filters
            searchMode: false,
            sidebarCollapsed: localStorage.getItem('gd_sidebar_collapsed') === 'true',
            searchFilterDrive: null,
            searchRoots: [],
            searchFilterType: '',
            searchFilterRoot: null,
            searchFilterModified: '',
            showDriveDropdown: false,
            showTypeDropdown: false,
            showRootDropdown: false,
            showModifiedDropdown: false,

            // New improvements
            isRootTreeExpanded: false,
            activeFolderTreeId: null,
            sortBy: 'name',
            sortOrder: 'asc',

            // Sidebar tree
            folderTree: {}, // { parentId: { children: [], loaded: false } }
            expandedFolders: [], // list of IDs
            showDeleteConfirm: false,
            syncMode: localStorage.getItem('gd_sync_mode') || 'manual',
            hasPendingChanges: false,
            isDriveOverview: false,

            uploading: false,
            uploadProgress: { current: 0, total: 0 },

            clipboard: {
                items: [],
                sourceDriveId: null,
                sourceRootId: null,
                sourceParentId: null,
            },

            showShareLoader: false,
            showDownloadLoader: false,
            showPasteLoader: false,
            showDeleteLoader: false,
            showUploadLoader: false,
            showRenameLoader: false,
            showManualSyncLoader: false,
            showManualSyncLoader: false,
            loaderMessage: 'Loading...',
        });

        const params = this.props.action && this.props.action.params ? this.props.action.params : {};
        if (params.drive_config_id) this.state.activeDriveId = params.drive_config_id;
        if (params.parent_folder_id) this.state.currentFolderId = params.parent_folder_id;
        if (params.root_folder_id) this.state.activeRootId = params.root_folder_id;

        // Also check URL hash for deep linking (when opened in new tab)
        const hashString = window.location.hash.substring(1);
        const urlParams = new URLSearchParams(hashString);
        if (urlParams.get('gd_drive_id')) this.state.activeDriveId = parseInt(urlParams.get('gd_drive_id'));
        if (urlParams.get('gd_parent_id')) this.state.currentFolderId = parseInt(urlParams.get('gd_parent_id'));
        if (urlParams.get('gd_root_id')) this.state.activeRootId = parseInt(urlParams.get('gd_root_id'));
        if (urlParams.get('gd_file_id')) this.state.selectedFiles = { [parseInt(urlParams.get('gd_file_id'))]: true };

        this.uploadProgressTimers = {};

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
            if (this.state.currentFolderId) {
                try {
                    const breadcrumbs = await this.orm.call("google.drive.file", "get_folder_breadcrumbs", [this.state.currentFolderId]);
                    this.state.breadcrumbs = breadcrumbs;
                    this.state.currentFolderName = breadcrumbs.length ? breadcrumbs[breadcrumbs.length - 1].name : this.activeDriveName;
                } catch (e) {
                    // Fallback
                    this.state.breadcrumbs = [{ id: 'section', name: this.activeDriveName }];
                    this.state.currentFolderName = this.activeDriveName;
                }
            } else {
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
            }
            await this.loadFiles(this.state.currentFolderId);
            
            // Apply deep-linked file selection after loadFiles clears it
            const hashString = window.location.hash.substring(1);
            const urlParams = new URLSearchParams(hashString);
            if (urlParams.get('gd_file_id')) {
                this.state.selectedFiles = { [parseInt(urlParams.get('gd_file_id'))]: true };
            }
        });
    }

    async loadDrives() {
        const drives = await this.orm.searchRead(
            "google.drive.config",
            [["active", "=", true]],
            ["name", "state"]
        );
        this.state.drives = drives;
        // Auto-select first drive or saved drive
        if (drives.length > 0 && !this.state.activeDriveId) {
            const savedDriveId = localStorage.getItem('gd_active_drive_id');
            if (savedDriveId && drives.some(d => d.id == savedDriveId)) {
                this.state.activeDriveId = parseInt(savedDriveId);
            } else {
                this.state.activeDriveId = drives[0].id;
                localStorage.setItem('gd_active_drive_id', drives[0].id);
            }
        }
    }

    async loadRoots(driveId) {
        const roots = await this.orm.searchRead(
            "google.drive.root.folder",
            [["config_id", "=", driveId], ["active", "=", true]],
            ["name", "root_id", "id"]
        );
        this.state.rootFolders = roots;

        // If there's only one root, auto-select it. Otherwise show overview.
        if (roots.length === 1) {
            this.state.activeRootId = roots[0].id;
            this.state.isDriveOverview = false;
        } else {
            this.state.activeRootId = null;
            this.state.isDriveOverview = roots.length > 0;
        }
    }

    get hasNoDrives() {
        return this.state.drives.length === 0;
    }

    get isAutoSyncingCurrentDrive() {
        return !!this.state.autoSyncingDrives[this.state.activeDriveId];
    }

    get isManualSyncingCurrentDrive() {
        return !!this.state.manualSyncingDrives[this.state.activeDriveId];
    }

    get isManualSyncCompletedCurrentDrive() {
        return !!this.state.manualSyncCompleted[this.state.activeDriveId];
    }

    get activeDriveName() {
        const drive = this.state.drives.find(d => d.id === this.state.activeDriveId);
        return drive ? drive.name : 'No Drive';
    }

    // Helper to get the correct breadcrumbs based on current section
    _getActiveBreadcrumbs() {
        return this.state.activeSection === 'trash' ? this.state.trashBreadcrumbs : this.state.breadcrumbs;
    }

    // Build independent trash breadcrumbs
    _buildTrashBreadcrumbs(folderId, folderName) {
        const breadcrumbs = [{ id: 'section', name: 'Trash' }];
        
        if (folderId && folderId !== 'trash_root') {
            breadcrumbs.push({ id: folderId, name: folderName || 'Trash Folder' });
        }
        
        return breadcrumbs;
    }

    toggleSort(field) {
        if (this.state.sortBy === field) {
            this.state.sortOrder = (this.state.sortOrder === 'asc') ? 'desc' : 'asc';
        } else {
            this.state.sortBy = field;
            this.state.sortOrder = 'asc';
        }
    }

    get sortedFiles() {
        let files = [...this.state.files];
        const field = this.state.sortBy;
        const order = this.state.sortOrder === 'asc' ? 1 : -1;

        files.sort((a, b) => {
            // Folders always first
            if (a.file_type === 'folder' && b.file_type !== 'folder') return -1;
            if (a.file_type !== 'folder' && b.file_type === 'folder') return 1;

            let valA = a[field] || '';
            let valB = b[field] || '';

            if (typeof valA === 'string') valA = valA.toLowerCase();
            if (typeof valB === 'string') valB = valB.toLowerCase();

            if (valA < valB) return -1 * order;
            if (valA > valB) return 1 * order;
            return 0;
        });

        return files;
    }

    get gridFolders() {
        return this.sortedFiles.filter(f => f.file_type === 'folder');
    }

    get gridFiles() {
        return this.sortedFiles.filter(f => f.file_type === 'file');
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
        localStorage.setItem('gd_active_drive_id', driveId);
        this.state.activeSection = 'my_drive';
        this.state.activeRootId = null;
        this.state.activeFolderTreeId = null;
        this.state.isRootTreeExpanded = false;
        
        await this.loadRoots(driveId);
        
        const driveName = this.activeDriveName;
        this.state.showAccountMenu = false;
        this.state.breadcrumbs = [{ id: 'section', name: driveName }];
        this.state.currentFolderName = driveName;

        this.state.searchQuery = '';
        this.state.searchMode = false;
        this.state.searchFilterDrive = null;
        this.state.searchRoots = [];
        this.state.searchFilterType = '';
        this.state.searchFilterRoot = null;
        this.state.searchFilterModified = '';

        await this.loadFiles(null);
    }

    async switchRoot(rootId) {
        this.state.activeSection = 'my_drive';
        const isAlreadySelected = this.state.activeRootId === rootId && !this.state.activeFolderTreeId;

        this.state.activeFolderTreeId = null;
        this.state.activeRootId = rootId;
        this.state.isDriveOverview = false;

        const root = this.state.rootFolders.find(r => r.id === rootId);
        const driveName = this.activeDriveName;
        const rootName = root ? root.name : 'Root';

        this.state.currentFolderName = rootName;
        this.state.breadcrumbs = [
            { id: 'section', name: driveName },
            { id: null, name: rootName }
        ];

        if (!this.state.isRootTreeExpanded) {
            this.state.isRootTreeExpanded = true;
            // The tree node toggle logic below will load children.
        }

        if (isAlreadySelected) {
            await this.toggleFolderTree(`root_${rootId}`);
        } else {
            if (!this.isTreeNodeExpanded(`root_${rootId}`)) {
                await this.toggleFolderTree(`root_${rootId}`);
            }
        }

        await this.loadFiles(null);
    }

    async loadFiles(folderId = null) {
        if (this.hasNoDrives) {
            this.state.loading = false;
            this.state.files = [];
            this.state.allFiles = [];
            return;
        }

        const section = this.state.activeSection;
        this.state.loading = true;
        this.state.currentFolderId = folderId;
        this.clearSelection();

        let files = [];
        const commonFields = [
            "name", "file_type", "mime_type", "google_url", "file_size",
            "owner_name", "last_modified", "sync_state", "upload_progress", "starred",
            "drive_config_id", "google_file_id", "attachment_id", "display_path",
            "parent_folder_id"
        ];

        try {
            if (section === 'starred') {
                files = await this.orm.searchRead("google.drive.file",
                    [["starred", "=", true], ["active", "=", true]],
                    commonFields
                );
            } else if (section === 'recent') {
                let recentIds = [];
                try {
                    recentIds = JSON.parse(localStorage.getItem('gd_recent_files') || '[]');
                } catch (e) {
                    recentIds = [];
                }
                
                if (recentIds.length === 0) {
                    files = [];
                } else {
                    const domain = [["id", "in", recentIds], ["active", "=", true], ["file_type", "!=", "folder"]];
                    const recentFiles = await this.orm.searchRead("google.drive.file",
                        domain,
                        commonFields
                    );
                    
                    // Maintain the order from recentIds (most recently opened first)
                    const fileMap = {};
                    recentFiles.forEach(f => fileMap[f.id] = f);
                    
                    files = recentIds.map(id => fileMap[id]).filter(f => f !== undefined);
                }
            } else if (section === 'trash') {
                if (folderId === 'trash_root' || !folderId) {
                    files = await this.orm.call("google.drive.file", "get_trash_roots", []);
                } else {
                    files = await this.orm.searchRead("google.drive.file",
                        [["parent_folder_id", "=", folderId]],
                        commonFields,
                        { context: { active_test: false } }
                    );
                }
            } else {
                // Default: My Drive or specific folder
                // If a specific folderId was requested (e.g. from a deep-link), always
                // load that folder's contents regardless of isDriveOverview mode.
                if (this.state.isDriveOverview && !folderId) {
                    this.state.loading = false;
                    this.state.allFiles = [];
                    this.state.files = [];
                    return;
                }
                if (!this.state.activeRootId && !folderId) {
                    this.state.loading = false;
                    return;
                }
                const domain = [
                    ["parent_folder_id", "=", folderId],
                    ["drive_config_id", "=", this.state.activeDriveId],
                    ["active", "=", true],
                ];
                if (!folderId) {
                    domain.push(["root_folder_id", "=", this.state.activeRootId]);
                }
                files = await this.orm.searchRead("google.drive.file", domain, commonFields);
            }

            this.state.allFiles = files;
            this.state.files = files;
        } catch (e) {
            console.error("Failed to load files", e);
            this.notificationService.add("Failed to load files.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }

        if (this.state.syncMode === 'manual') {
            await this.checkPendingChanges();
        }
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
            // Save current navigation state to restore when search is cleared
            this.state.previousSearchState = {
                activeSection: this.state.activeSection,
                activeRootId: this.state.activeRootId,
                activeFolderTreeId: this.state.activeFolderTreeId,
                currentFolderId: this.state.currentFolderId,
                currentFolderName: this.state.currentFolderName,
                breadcrumbs: [...this.state.breadcrumbs]
            };

            // Set the search filters based on current location
            this.state.searchFilterDrive = this.state.activeDriveId;
            this.state.searchRoots = this.state.rootFolders;
            if (this.state.activeRootId) {
                this.state.searchFilterRoot = this.state.activeRootId;
            }

            // Enter search mode
            this.state.searchMode = true;

            // Uncheck the tabs so no folder tree is highlighted
            this.state.activeSection = null;
            this.state.activeRootId = null;
            this.state.activeFolderTreeId = null;

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
                "owner_name", "last_modified", "sync_state", "upload_progress", "starred",
                "drive_config_id", "google_file_id", "attachment_id", "display_path",
                "parent_folder_id"
            ]);
            this.state.files = files;
        } catch (e) {
            console.error("Search failed", e);
            this.notificationService.add("Search failed.", { type: "danger" });
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

        // Restore the navigation state to re-check the folder tree
        if (this.state.previousSearchState) {
            this.state.activeSection = this.state.previousSearchState.activeSection;
            this.state.activeRootId = this.state.previousSearchState.activeRootId;
            this.state.activeFolderTreeId = this.state.previousSearchState.activeFolderTreeId;
            this.state.currentFolderId = this.state.previousSearchState.currentFolderId;
            this.state.currentFolderName = this.state.previousSearchState.currentFolderName;
            this.state.breadcrumbs = this.state.previousSearchState.breadcrumbs;
            this.state.previousSearchState = null;
        } else {
            // Fallback if no state was saved, default to my_drive
            this.state.activeSection = 'my_drive';
            this.state.activeRootId = null;
            this.state.activeFolderTreeId = null;
        }

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
                ["name", "root_id", "id"]
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
        if (!this.state.searchFilterRoot) return 'All Folders';
        const root = this.state.searchRoots.find(r => r.id === this.state.searchFilterRoot);
        return root ? root.name : 'All Folders';
    }

    getModifiedFilterName() {
        const opt = this.getModifiedFilterOptions().find(o => o.value === this.state.searchFilterModified);
        return opt ? opt.label : '';
    }

    // ─── Sidebar navigation ───

    async onSidebarClick(section) {
        this.state.activeSection = section;
        this.state.activeFolderTreeId = null;

        if (section === 'my_drive') {
            // Reset to roots overview if they click the Drive name
            this.state.activeRootId = null;
            this.state.isDriveOverview = true;
        } else {
            this.state.isDriveOverview = false;
        }

        // Handle trash section separately to maintain independent breadcrumbs
        if (section === 'trash') {
            this.state.trashBreadcrumbs = [{ id: 'section', name: 'Trash' }];
            this.state.trashCurrentFolderId = null;
            this.state.currentFolderName = 'Trash';
            await this.loadFiles('trash_root');
        } else {
            await this._updateNavigationState(null, this._sectionLabel(section));

            if (section === 'my_drive') {
                this.state.isRootTreeExpanded = !this.state.isRootTreeExpanded;
                await this.loadFiles(null);
            } else {
                this.state.isRootTreeExpanded = false;
                await this.loadFiles(null);
            }
        }
    }

    toggleSidebar() {
        this.state.sidebarCollapsed = !this.state.sidebarCollapsed;
        localStorage.setItem('gd_sidebar_collapsed', this.state.sidebarCollapsed);
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
            if (this.state.activeSection === 'trash') {
                // In trash section: handle independently
                this.state.currentFolderName = file.name;
                this.state.trashCurrentFolderId = file.id;
                
                // Build trash-specific breadcrumbs
                this.state.trashBreadcrumbs = [
                    { id: 'section', name: 'Trash' },
                    { id: file.id, name: file.name }
                ];
            } else {
                // In drive section: use normal navigation
                this.state.activeSection = 'my_drive';
                await this._updateNavigationState(file.id, file.name);
            }
            await this.loadFiles(file.id);
        }
    }

    async onBreadcrumbClick(index) {
        // Use the appropriate breadcrumbs based on current section
        const breadcrumbs = this.state.activeSection === 'trash' ? this.state.trashBreadcrumbs : this.state.breadcrumbs;
        const bc = breadcrumbs[index];
        
        if (bc.id === 'section') {
            // Section header clicked
            if (bc.name === 'Trash') {
                this.state.activeSection = 'trash';
                this.state.trashBreadcrumbs = [{ id: 'section', name: 'Trash' }];
                this.state.trashCurrentFolderId = null;
                this.state.currentFolderName = 'Trash';
                await this.loadFiles('trash_root');
            } else {
                // Drive section clicked
                this.state.activeSection = 'my_drive';
                this.state.activeRootId = null;
                this.state.isDriveOverview = true;
                await this._updateNavigationState(null, bc.name);
                await this.loadFiles(null);
            }
        } else {
            // Folder in the breadcrumb trail clicked
            if (this.state.activeSection === 'trash') {
                // Trash folder navigation - rebuild breadcrumbs up to clicked level
                const folderId = (bc.id === false) ? null : bc.id;
                this.state.currentFolderName = bc.name;
                this.state.trashCurrentFolderId = folderId;
                
                // Rebuild breadcrumbs: keep all items up to (and including) the clicked one
                this.state.trashBreadcrumbs = breadcrumbs.slice(0, index + 1);
                await this.loadFiles(folderId);
            } else {
                // Drive folder navigation
                const folderId = (bc.id === false) ? null : bc.id;
                await this._updateNavigationState(folderId, bc.name);
                await this.loadFiles(folderId);
            }
        }
    }

    async onBackClick() {
        if (this.state.activeSection === 'trash') {
            // Handle trash back button
            if (this.state.trashBreadcrumbs.length > 1) {
                this.state.trashBreadcrumbs.pop();
                const last = this.state.trashBreadcrumbs[this.state.trashBreadcrumbs.length - 1];
                
                if (last.id === 'section') {
                    // Going back to trash root
                    this.state.trashCurrentFolderId = null;
                    this.state.currentFolderName = 'Trash';
                    await this.loadFiles('trash_root');
                } else {
                    // Going back to a parent trash folder
                    this.state.trashCurrentFolderId = last.id;
                    this.state.currentFolderName = last.name;
                    await this.loadFiles(last.id);
                }
            }
        } else {
            // Handle drive back button
            if (this.state.breadcrumbs.length > 1) {
                const last = this.state.breadcrumbs[this.state.breadcrumbs.length - 2];
                if (last.id === 'section') {
                    this.state.activeRootId = null;
                    this.state.isDriveOverview = true;
                    await this._updateNavigationState(null, last.name);
                    await this.loadFiles(null);
                } else {
                    const folderId = (last.id === false) ? null : last.id;
                    await this._updateNavigationState(folderId, last.name);
                    await this.loadFiles(folderId);
                }
            }
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
        
        let recentIds = [];
        try {
            recentIds = JSON.parse(localStorage.getItem('gd_recent_files') || '[]');
        } catch (e) {
            recentIds = [];
        }
        
        recentIds = recentIds.filter(id => id !== file.id);
        recentIds.unshift(file.id);
        
        if (recentIds.length > 50) {
            recentIds = recentIds.slice(0, 50);
        }
        
        localStorage.setItem('gd_recent_files', JSON.stringify(recentIds));
    }

    clearRecent() {
        localStorage.removeItem('gd_recent_files');
        this.state.files = [];
        this.state.allFiles = [];
    }

    onFileClick(ev, file) {
        // Single click now opens folders or previews files
        this.addToRecent(file);
        if (file.file_type === 'folder') {
            this.onFolderClick(file);
        } else {
            // Google Drive's previewer handles virtually all formats natively (PDF, Office,
            // images, video, audio, code, etc.). Only skip the dialog for types Drive truly
            // cannot preview: archives and raw executables.
            const mime = file.mime_type || '';
            const skipPreviewMimes = ['zip', 'x-rar', 'x-tar', 'archive', 'compressed', 'x-msdownload', 'x-executable'];
            const canSkipPreview = skipPreviewMimes.some(t => mime.includes(t));

            if (file.google_file_id && !canSkipPreview) {
                this.dialogService.add(FilePreviewDialog, {
                    file: file,
                    onDownload: () => {
                        window.open(`/google_drive/download/${file.id}`, '_blank');
                    },
                    onOpenDetails: () => {
                        this.actionService.doAction({
                            type: 'ir.actions.act_window',
                            res_model: 'google.drive.file',
                            res_id: file.id,
                            views: [[false, 'form']],
                            target: 'new'
                        });
                    }
                });
            } else {
                // If not previewable or missing Google ID, open form
                this.actionService.doAction({
                    type: 'ir.actions.act_window',
                    res_model: 'google.drive.file',
                    res_id: file.id,
                    views: [[false, 'form']],
                    target: 'new'
                });
            }
        }
    }

    onDownloadSelected() {
        if (this.hasFolderInSelection) {
            this.notificationService.add("Cannot download folders. Please select only files.", { type: "warning" });
            return;
        }

        const files = this.selectedFilesList;
        if (files.length === 0) return;

        // Trigger a download for each selected file
        files.forEach((file, index) => {
            // Add a small delay between each download to prevent browser from blocking multiple popups/downloads at once
            setTimeout(() => {
                window.open(`/google_drive/download/${file.id}`, '_blank');
            }, index * 200);
        });

        // Optional: clear selection after download triggered
        this.clearSelection();
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

    isItemCut(fileId) {
        return this.state.clipboard.items.includes(fileId);
    }

    get selectedCount() {
        return Object.keys(this.state.selectedFiles).length;
    }

    get selectedFilesList() {
        return Object.values(this.state.selectedFiles);
    }

    get hasFolderInSelection() {
        return this.selectedFilesList.some(f => f.file_type === 'folder');
    }

    get hasSelectedFiles() {
        return Object.keys(this.state.selectedFiles).length > 0;
    }

    get isAllSelected() {
        if (this.state.files.length === 0) return false;
        return this.state.files.every(f => this.state.selectedFiles[f.id]);
    }

    onSelectAll() {
        if (this.isAllSelected) {
            this.clearSelection();
        } else {
            const selected = { ...this.state.selectedFiles };
            this.state.files.forEach(f => {
                selected[f.id] = f;
            });
            this.state.selectedFiles = selected;
            this.state.selectionMode = true;
        }
    }

    // ─── Cut & Paste ───

    onCutSelected() {
        const items = this.selectedFilesList.map(f => f.id);
        if (items.length === 0) return;

        this.state.clipboard = {
            items: items,
            sourceDriveId: this.state.activeDriveId,
            sourceRootId: this.state.activeRootId,
            sourceParentId: this.state.currentFolderId,
        };

        this.notificationService.add(`${items.length} item(s) cut to clipboard.`, { type: "info" });
        this.clearSelection();
    }

    async onPaste() {
        if (!this.state.clipboard.items.length) return;

        // Validation: Drive must match
        if (this.state.activeDriveId !== this.state.clipboard.sourceDriveId) {
            this.notificationService.add("Other drive not pasted. Moving items across different Google Drives is not supported.", { 
                type: "danger",
                sticky: true 
            });
            return;
        }

        this.state.loaderMessage = 'Moving items...';
        this.state.showPasteLoader = true;
        try {
            const success = await this.orm.call(
                "google.drive.file",
                "action_move_items",
                [this.state.clipboard.items],
                {
                    target_parent_id: this.state.currentFolderId,
                    target_root_id: !this.state.currentFolderId ? this.state.activeRootId : false,
                }
            );

            if (success) {
                this.notificationService.add("Items moved successfully.", { type: "success" });
                
                // Save clipboard data before resetting
                const savedSourceParent = this.state.clipboard.sourceParentId;
                const savedSourceRoot = this.state.clipboard.sourceRootId;
                const targetParent = this.state.currentFolderId;
                const targetRoot = this.state.activeRootId;
                
                this.state.clipboard = { items: [], sourceDriveId: null, sourceParentId: null, sourceRootId: null };
                
                // Refresh current file list view
                await this.loadFiles(targetParent);
                
                // Always refresh the target parent in the tree (where item landed)
                await this._refreshTreeForParent(targetParent, true, targetRoot);

                if (savedSourceRoot && savedSourceRoot !== targetRoot) {
                    // Cross-root move: refresh the source root's parent node with the correct rootId
                    await this._refreshTreeForParent(savedSourceParent, false, savedSourceRoot);
                    // Also refresh the root-level entry for the source root
                    await this._loadTreeChildren(`root_${savedSourceRoot}`, savedSourceRoot);
                } else {
                    // Same-root move: refresh the source parent so the item disappears from there
                    await this._refreshTreeForParent(savedSourceParent, false, targetRoot);
                }
            } else {
                this.notificationService.add("Move failed. Please check folder permissions.", { type: "danger" });
            }
        } catch (e) {
            this.notificationService.add("An error occurred while moving items.", { type: "danger" });
            console.error(e);
        } finally {
            this.state.showPasteLoader = false;
        }
    }

    clearClipboard() {
        this.state.clipboard = { items: [], sourceDriveId: null, sourceParentId: null, sourceRootId: null };
        this.notificationService.add("Cut operation cancelled.", { type: "info" });
    }

    startFileSyncProgressAnimation(fileId) {
        this.stopFileSyncProgressAnimation(fileId);
        const timer = setInterval(() => {
            const idx = this.state.files.findIndex(f => f.id === fileId);
            if (idx === -1) {
                this.stopFileSyncProgressAnimation(fileId);
                return;
            }
            const file = this.state.files[idx];
            if (file.sync_state !== 'uploading' && file.sync_state !== 'pending') {
                this.stopFileSyncProgressAnimation(fileId);
                return;
            }
            const current = file.upload_progress || 0;
            if (current >= 90) {
                return;
            }
            const step = current < 50 ? 8 : current < 75 ? 5 : 2;
            file.upload_progress = Math.min(90, current + step);
        }, 700);
        this.uploadProgressTimers[fileId] = timer;
    }

    stopFileSyncProgressAnimation(fileId) {
        const timer = this.uploadProgressTimers[fileId];
        if (timer) {
            clearInterval(timer);
            delete this.uploadProgressTimers[fileId];
        }
    }

    // ─── Sidebar Tree Logic ───

    getTreeRootChildren() {
        if (!this.state.activeRootId) return [];
        return this.state.folderTree[`root_${this.state.activeRootId}`]?.children || [];
    }

    getTreeNodeChildren(folderId) {
        return this.state.folderTree[folderId]?.children || [];
    }

    isTreeNodeExpanded(folderId) {
        return this.state.expandedFolders.includes(folderId);
    }

    isTreeNodeLoaded(folderId) {
        return this.state.folderTree[folderId]?.loaded || false;
    }

    toggleRootTreeExpanded() {
        this.state.isRootTreeExpanded = !this.state.isRootTreeExpanded;
        if (this.state.isRootTreeExpanded && this.state.activeRootId) {
            this._loadTreeChildren(`root_${this.state.activeRootId}`);
        }
    }

    async toggleFolderTree(folderId) {

        const idx = this.state.expandedFolders.indexOf(folderId);
        if (idx >= 0) {
            this.state.expandedFolders.splice(idx, 1);
        } else {
            this.state.expandedFolders.push(folderId);
            if (!this.isTreeNodeLoaded(folderId)) {
                await this._loadTreeChildren(folderId);
            }
        }
    }

    async _loadTreeChildren(parentId, rootId = null) {
        // Use the provided rootId, or fall back to the active one
        const resolvedRootId = rootId !== null ? rootId : this.state.activeRootId;
        if (!resolvedRootId && String(parentId).startsWith("root_")) return;

        try {
            const domain = [
                ["file_type", "=", "folder"],
                ["drive_config_id", "=", this.state.activeDriveId],
                ["active", "=", true]
            ];

            if (resolvedRootId) {
                domain.push(["root_folder_id", "=", resolvedRootId]);
            }

            let cacheKey = parentId;
            if (String(parentId).startsWith("root_") || parentId === false || parentId === null) {
                cacheKey = `root_${resolvedRootId}`;
                domain.push(["parent_folder_id", "=", false]);
            } else {
                domain.push(["parent_folder_id", "=", parentId]);
            }

            const children = await this.orm.searchRead("google.drive.file", domain, ["id", "name"]);
            
            // Reactivity Fix: Update the folderTree object reference using spread operator 
            // so Owl detects the change in the nested property.
            this.state.folderTree = {
                ...this.state.folderTree,
                [cacheKey]: {
                    children: children,
                    loaded: true
                }
            };
        } catch (e) {
            console.error("Failed to load tree children", e);
        }
    }

    async _refreshTreeForParent(parentId, expand = false, rootId = null) {
        const resolvedRootId = rootId !== null ? rootId : this.state.activeRootId;
        const id = (parentId === false || parentId === null || parentId === undefined) 
            ? `root_${resolvedRootId}` 
            : parentId;
            
        // Always refresh the cache for this parent
        await this._loadTreeChildren(id, resolvedRootId);
        
        // If expansion requested and it's a folder (not root string), expand it
        if (expand && !String(id).startsWith("root_")) {
            if (!this.state.expandedFolders.includes(id)) {
                this.state.expandedFolders.push(id);
            }
        }
    }

    async _refreshEntireTree() {
        this.state.folderTree = {};
        if (this.state.activeRootId) {
            await this._loadTreeChildren(`root_${this.state.activeRootId}`);
        }
        // Re-load any expanded folders
        for (const folderId of this.state.expandedFolders) {
            await this._loadTreeChildren(folderId);
        }
    }

    async onTreeFolderClick(folderId, folderName) {
        this.state.activeSection = 'my_drive';

        // Set active state immediately so the UI feels responsive
        this.state.activeFolderTreeId = folderId;
        this.state.currentFolderName = folderName;

        // Always expand the node when clicked (never collapse on click — use the arrow toggle for that)
        if (!this.isTreeNodeExpanded(folderId)) {
            await this.toggleFolderTree(folderId);
        }

        // Load files and update breadcrumbs in parallel
        await Promise.all([
            this._updateNavigationState(folderId, folderName),
            this.loadFiles(folderId),
        ]);
    }

    async _updateNavigationState(folderId, folderName) {
        // Normalize false to null for Python RPC empty values
        folderId = folderId === false ? null : folderId;



        this.state.activeFolderTreeId = folderId;
        this.state.currentFolderName = folderName;

        if ((this.state.activeSection !== 'my_drive' && this.state.activeSection !== 'trash') || folderId === null) {
            // Root or non-MyDrive section
            let breadcrumbs = [{ id: 'section', name: this._sectionLabel(this.state.activeSection) }];
            if (this.state.activeSection === 'my_drive') {
                if (this.state.activeRootId) {
                    const root = this.state.rootFolders.find(r => r.id === this.state.activeRootId);
                    if (root) {
                        breadcrumbs.push({ id: null, name: root.name });
                    }
                    this.state.isDriveOverview = false;
                } else {
                    this.state.isDriveOverview = true;
                }
            } else {
                this.state.isDriveOverview = false;
            }
            this.state.breadcrumbs = breadcrumbs;
            if (folderId === null) {
                this.state.currentFolderName = breadcrumbs[breadcrumbs.length - 1].name;
            }
            return;
        }

        this.state.isDriveOverview = false;

        try {
            const breadcrumbs = await this.orm.call("google.drive.file", "get_folder_breadcrumbs", [folderId]);
            if (breadcrumbs && breadcrumbs.length > 0) {
                this.state.breadcrumbs = breadcrumbs;

                // Update activeSection based on breadcrumb section
                if (breadcrumbs.length > 0 && breadcrumbs[0].id === 'section') {
                    if (breadcrumbs[0].name === 'Trash') {
                        this.state.activeSection = 'trash';
                    } else if (this.state.rootFolders.some(r => r.name === breadcrumbs[0].name)) {
                        this.state.activeSection = 'my_drive';
                    }
                }

                // Ensure activeRootId is set based on breadcrumbs
                // breadcrumbs structure is [ {id: 'section', name: ...}, {id: null, name: RootName}, {id: FolderID, name: ...}, ... ]
                if (breadcrumbs.length > 1 && breadcrumbs[1].id === null) {
                    const rootObj = this.state.rootFolders.find(r => r.name === breadcrumbs[1].name);
                    if (rootObj) {
                        this.state.activeRootId = rootObj.id;
                    }
                }

                // Auto-expand tree to this folder
                const expanded = new Set(this.state.expandedFolders);
                breadcrumbs.forEach(bc => {
                    if (bc.id && bc.id !== 'section') {
                        expanded.add(bc.id);
                    }
                });
                this.state.expandedFolders = Array.from(expanded);

                // Re-load children for any newly expanded folders if needed
                // (Optional: this might be handled by tree rendering)
            }
        } catch (e) {
            console.error("Failed to update navigation state", e);
        }
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

    async toggleStarSelected() {
        const allSelected = this.selectedFilesList;
        const files = allSelected.filter(f => f.file_type === 'file');
        const folders = allSelected.filter(f => f.file_type === 'folder');

        if (files.length === 0) {
            if (folders.length > 0) {
                this.notificationService.add("Only files can be starred.", { type: "warning" });
            }
            return;
        }

        this.state.loading = true;
        try {
            const ids = files.map(f => f.id);
            // Toggle star based on the first item's state
            const newState = !files[0].starred;
            await this.orm.write("google.drive.file", ids, { starred: newState });

            if (this.state.activeSection === 'starred' && !newState) {
                // Remove from view immediately
                this.state.files = this.state.files.filter(f => !ids.includes(f.id));
                this.state.allFiles = this.state.allFiles.filter(f => !ids.includes(f.id));
                this.clearSelection();
            } else {
                await this.loadFiles(this.state.currentFolderId);
            }

            if (folders.length > 0) {
                this.notificationService.add("Folders cannot be starred. Selected files processed.", { type: "warning" });
            } else {
                this.notificationService.add(newState ? "Items starred" : "Items unstarred", { type: "success" });
            }
        } catch (e) {
            this.notificationService.add("Failed to toggle star.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async toggleStar(ev, file) {
        if (ev) ev.stopPropagation();
        if (file.file_type === 'folder') {
            this.notificationService.add("Only files can be starred.", { type: "warning" });
            return;
        }
        const newState = !file.starred;
        try {
            await this.orm.write("google.drive.file", [file.id], { starred: newState });

            if (this.state.activeSection === 'starred' && !newState) {
                // Remove from view immediately
                this.state.files = this.state.files.filter(f => f.id !== file.id);
                this.state.allFiles = this.state.allFiles.filter(f => f.id !== file.id);
            } else {
                file.starred = newState;
            }

            const msg = newState ? `"${file.name}" starred` : `"${file.name}" unstarred`;
            this.notificationService.add(msg, { type: "success" });
        } catch (e) {
            this.notificationService.add("Failed to toggle star.", { type: "danger" });
        }
    }

    async onRestoreSelected() {
        if (this._isActionRestrictedInTrash()) {
            this.dialogService.add(TrashRestrictedDialog, { close: () => {} });
            return;
        }

        const files = this.selectedFilesList;
        if (files.length === 0) return;

        this.state.loading = true;
        try {
            const ids = files.map(f => f.id);
            await this.orm.call("google.drive.file", "action_unarchive", [ids]);

            // Sync tree for restored folders
            const folderParents = [...new Set(files
                .filter(f => f.file_type === 'folder')
                .map(f => f.parent_folder_id ? f.parent_folder_id[0] : null)
            )];

            for (const parentId of folderParents) {
                await this._refreshTreeForParent(parentId, true);
            }

            this.clearSelection();
            await this.onSidebarClick('trash');
            this.notificationService.add("Items restored from trash.", { type: "success" });
        } catch (e) {
            this.notificationService.add("Failed to restore items.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    onPermanentlyDeleteSelected() {
        if (this._isActionRestrictedInTrash()) {
            this.dialogService.add(TrashRestrictedDialog, { close: () => {} });
            return;
        }
        if (this.selectedCount === 0) return;
        this.state.showDeleteConfirm = true;
    }

    cancelPermanentDelete() {
        this.state.showDeleteConfirm = false;
    }

    async confirmPermanentDelete() {
        const files = this.selectedFilesList;
        if (files.length === 0) return;

        const ids = files.map(f => f.id);
        this.state.showDeleteConfirm = false;
        this.state.loaderMessage = 'Deleting items...';
        this.state.showDeleteLoader = true;

        try {
            await this.orm.call("google.drive.file", "delete_on_drive_and_unlink", [ids]);
            this.clearSelection();
            await this.onSidebarClick('trash');
            this.notificationService.add("Items permanently deleted.", { type: "success" });

            // We don't necessarily know all parents here easily, 
            // but usually permanent delete is from trash which has no tree.
            // If it was from a drive view, we might want to refresh current folder tree.
            if (this.state.activeSection === 'my_drive') {
                await this._refreshTreeForParent(this.state.currentFolderId);
            }
        } catch (e) {
            this.notificationService.add("Failed to delete items.", { type: "danger" });
        } finally {
            this.state.showDeleteLoader = false;
        }
    }

    onShareSelected() {
        const files = this.selectedFilesList;
        if (files.length === 0) return;

        // Prevent double clicks
        if (this.state.showShareLoader) return;
        this.state.loaderMessage = 'Loading sharing info...';
        this.state.showShareLoader = true;

        this.dialogService.add(ShareDriveLinkDialog, {
            files: files,
            onReady: () => {
                this.state.showShareLoader = false;
            },
        }, {
            onClose: () => {
                this.state.showShareLoader = false;
            },
        });
    }

    async onGenerateLinkSelected(role = 'reader') {
        const files = this.selectedFilesList;
        if (files.length === 0) return;

        this.state.loading = true;
        let successCount = 0;

        for (const file of files) {
            if (file.file_type === 'folder') {
                this.notificationService.add("Link generation is currently only supported for files.", { type: "warning" });
                continue;
            }

            try {
                const result = await this.orm.call(
                    "google.drive.file",
                    "action_generate_shareable_link",
                    [file.id, role]
                );

                if (result.link) {
                    await navigator.clipboard.writeText(result.link);
                    this.notificationService.add(`Public ${role === 'writer' ? 'Edit' : 'View'} Link generated and copied to clipboard!`, {
                        type: "success",
                        title: "Link Generated"
                    });
                    successCount++;
                } else if (result.error) {
                    this.notificationService.add(result.error, { type: "danger", title: "Error" });
                }
            } catch (err) {
                console.error("Error generating link:", err);
                this.notificationService.add("Failed to generate shareable link.", { type: "danger" });
            }
        }

        this.clearSelection();
        this.state.loading = false;
    }

    async onRevokeLinkSelected() {
        const files = this.selectedFilesList;
        if (files.length === 0) return;

        this.state.loading = true;
        let successCount = 0;

        for (const file of files) {
            if (file.file_type === 'folder') continue;

            try {
                const result = await this.orm.call(
                    "google.drive.file",
                    "action_revoke_shareable_link",
                    [file.id]
                );

                if (result.success) {
                    successCount++;
                } else if (result.error) {
                    this.notificationService.add(result.error, { type: "danger", title: "Error" });
                }
            } catch (err) {
                console.error("Error revoking link:", err);
            }
        }

        if (successCount > 0) {
            this.notificationService.add(`Revoked public access for ${successCount} file(s).`, {
                type: "success",
                title: "Access Revoked"
            });
        }
        this.clearSelection();
        this.state.loading = false;
    }

    onDownloadSelected() {
        const files = this.selectedFilesList;
        if (files.length === 0) return;

        // Prevent double clicks
        if (this.state.showDownloadLoader) return;

        // Show fullscreen loader
        this.state.loaderMessage = 'Preparing download...';
        this.state.showDownloadLoader = true;

        // Condition for ZIP: Multiple items OR at least one folder
        const hasFolder = files.some(f => f.file_type === 'folder');
        const isMulti = files.length > 1;

        if (hasFolder || isMulti) {
            const ids = files.map(f => f.id).join(',');
            const downloadUrl = `/google_drive/download_zip?file_ids=${ids}`;

            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = downloadUrl;
            document.body.appendChild(a);
            a.click();
            setTimeout(() => document.body.removeChild(a), 100);

            // Hide loader after a brief moment (browser handles the download)
            setTimeout(() => {
                this.state.showDownloadLoader = false;
                this.notificationService.add("Your ZIP download has started.", { type: "success" });
            }, 2000);
            return;
        }

        // Single file download
        const file = files[0];
        const downloadUrl = `/google_drive/download/${file.id}`;
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = downloadUrl;
        a.download = file.name;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => document.body.removeChild(a), 100);

        // Hide loader after a brief moment
        setTimeout(() => {
            this.state.showDownloadLoader = false;
            this.notificationService.add(`Downloading "${file.name}"...`, { type: "success" });
        }, 1500);
    }

    async onDeleteSelected() {
        const allSelected = this.selectedFilesList;
        if (allSelected.length === 0) return;

        const ids = allSelected.map(f => f.id);
        const names = allSelected.map(f => f.name);
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
                // Sync tree
                await this._refreshTreeForParent(this.state.currentFolderId);
            }

            // Feed back to user
            this.notificationService.add(
                `Moved to Odoo Trash: ${nameList}. These items will remain active on Google Drive until permanently deleted from the Trash tab.`,
                { type: "info" }
            );
            await this.checkPendingChanges();
        } catch (e) {
            this.notificationService.add("Failed to delete: " + (e.message || "Unknown error"), { type: "danger" });
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

        if (this.checkNameConflict(newName, file.file_type, file.id)) {
            this.notificationService.add(`A ${file.file_type} named "${newName}" already exists here.`, { type: "danger", title: "Name Conflict" });
            return;
        }

        this.state.renamingFileId = null;
        this.state.renameValue = '';

        // Show loader
        this.state.loaderMessage = `Renaming ${file.file_type}...`;
        this.state.showRenameLoader = true;

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

            // Sync tree if it was a folder
            if (file.file_type === 'folder') {
                await this._refreshTreeForParent(file.parent_folder_id ? file.parent_folder_id[0] : null);
            }

            // Step 3: Call Drive rename immediately if it has a Google file ID
            if (file.google_file_id) {
                try {
                    await this.orm.call("google.drive.file", "rename_on_drive_by_id", [], {
                        record_id: fileId,
                        new_name: newName,
                        old_name: file.name,
                    });
                    this.loadFiles(this.state.currentFolderId); // Refresh to clear 'pending'
                    this.notificationService.add(`Renamed to "${newName}" on Google Drive`, { type: "success" });
                } catch (e) {
                    this.notificationService.add(`Failed to rename "${newName}" on Google Drive`, { type: "warning" });
                }
            } else {
                this.notificationService.add("Item renamed successfully locally!", { type: "success" });
                await this.checkPendingChanges();
            }
        } catch (e) {
            this.notificationService.add("Failed to rename item.", { type: "danger" });
        } finally {
            // Hide loader
            this.state.showRenameLoader = false;
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
        if (state === 'synced') return 'fa-check';
        if (state === 'pending') return 'fa-refresh';
        if (state === 'uploading') return 'fa-upload';
        if (state === 'error') return 'fa-exclamation-triangle';
        if (state === 'pending_delete') return 'fa-trash-o';
        return 'fa-circle-o';
    }

    hasSyncBadgeLabel(file) {
        return ['error', 'pending_delete'].includes(file.sync_state);
    }

    getSyncBadgeText(file) {
        if (file.sync_state === 'error') return 'ERROR';
        if (file.sync_state === 'pending_delete') return 'REMOVING';
        return '';
    }

    getSyncProgressText(file) {
        return `${Math.round(file.upload_progress || 0)}%`;
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

    checkNameConflict(name, type, excludeId = null) {
        return this.state.allFiles.some(f => 
            f.name.toLowerCase() === name.toLowerCase() && 
            f.file_type === type && 
            f.id !== excludeId
        );
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
            this.notificationService.add("Please enter a folder name.", { type: "warning" });
            return;
        }

        if (this.checkNameConflict(name, 'folder')) {
            this.notificationService.add(`A folder named "${name}" already exists here.`, { type: "danger", title: "Name Conflict" });
            return;
        }

        const driveConfigId = this.state.activeDriveId;

        if (!driveConfigId) {
            this.notificationService.add("No active drive configured.", { type: "warning" });
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
            this.notificationService.add(`Folder "${name}" created!`, { type: "success" });

            // Sync tree
            await this._refreshTreeForParent(this.state.currentFolderId, true);

            // Auto sync in background (non-blocking) — pass current drive ID
            if (this.state.syncMode === 'auto') {
                this.triggerAutoSync(this.state.activeDriveId);
            } else {
                await this.checkPendingChanges();
            }
        } catch (e) {
            this.notificationService.add("Failed to create folder.", { type: "danger" });
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

            // Capture target folder ID and root folder ID at the time the upload is initiated
            const targetFolderId = this.state.currentFolderId;
            const targetRootId = this.state.activeRootId;

            const validFiles = [];
            const conflictedNames = [];

            for (const file of files) {
                if (this.checkNameConflict(file.name, 'file')) {
                    conflictedNames.push(file.name);
                } else {
                    validFiles.push(file);
                }
            }

            if (conflictedNames.length > 0) {
                this.notificationService.add(
                    `The following files already exist and were skipped: ${conflictedNames.join(', ')}`,
                    { type: "danger", title: "Upload Conflict" }
                );
            }

            if (validFiles.length === 0) {
                this.state.uploading = false;
                return;
            }

            this.state.uploading = true;
            this.state.uploadProgress = { current: 0, total: validFiles.length };
            this.state.loaderMessage = 'Uploading files...';
            this.state.showUploadLoader = true;

            for (const file of validFiles) {
                await this._uploadSingleFile(file, targetFolderId, targetRootId);
                this.state.uploadProgress.current += 1;
            }

            this.state.uploading = false;
            this.state.showUploadLoader = false;

            // Only reload the view if the user is still looking at the folder where the files were uploaded
            if (this.state.currentFolderId === targetFolderId) {
                await this.loadFiles(targetFolderId);
            }
            this.notificationService.add("Files uploaded successfully!", { type: "success" });

            // Auto sync in background (non-blocking) — pass current drive ID
            if (this.state.syncMode === 'auto') {
                this.triggerAutoSync(this.state.activeDriveId);
            } else {
                await this.checkPendingChanges();
            }
        });
        input.click();
    }

    // ─── Upload Folder ───

    onUploadFolder() {
        const input = document.createElement('input');
        input.type = 'file';
        input.webkitdirectory = true;
        input.directory = true;
        input.multiple = true;
        input.addEventListener('change', async (ev) => {
            const files = ev.target.files;
            if (!files || files.length === 0) return;

            // Capture target folder ID and root folder ID at the time the upload is initiated
            const targetFolderId = this.state.currentFolderId;
            const targetRootId = this.state.activeRootId;

            const validFiles = [];
            const conflictedNames = [];

            for (const file of files) {
                if (this.checkNameConflict(file.name, 'file')) {
                    conflictedNames.push(file.name);
                } else {
                    validFiles.push(file);
                }
            }

            if (conflictedNames.length > 0) {
                this.notificationService.add(
                    `The following files already exist and were skipped: ${conflictedNames.join(', ')}`,
                    { type: "danger", title: "Upload Conflict" }
                );
            }

            if (validFiles.length === 0) {
                this.state.uploading = false;
                return;
            }

            this.state.uploading = true;
            this.state.uploadProgress = { current: 0, total: validFiles.length };
            this.state.loaderMessage = 'Uploading folder...';
            this.state.showUploadLoader = true;

            for (const file of validFiles) {
                await this._uploadSingleFile(file, targetFolderId, targetRootId);
                this.state.uploadProgress.current += 1;
            }

            this.state.uploading = false;
            this.state.showUploadLoader = false;

            // Only reload the view if the user is still looking at the folder where the files were uploaded
            if (this.state.currentFolderId === targetFolderId) {
                await this.loadFiles(targetFolderId);
            }
            this.notificationService.add("Folder uploaded successfully!", { type: "success" });

            // Auto sync in background (non-blocking) — pass current drive ID
            if (this.state.syncMode === 'auto') {
                this.triggerAutoSync(this.state.activeDriveId);
            } else {
                await this.checkPendingChanges();
            }
        });
        input.click();
    }

    async _uploadSingleFile(file, targetFolderId, targetRootId) {
        const base64 = await new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = () => {
                const result = reader.result.split(',')[1];
                resolve(result);
            };
            reader.readAsDataURL(file);
        });

        const driveConfigId = this.state.activeDriveId;

        if (!driveConfigId) {
            this.notificationService.add("No active drive configured.", { type: "warning" });
            return;
        }

        await this.orm.call("google.drive.file", "action_upload_from_explorer", [], {
            file_name: file.name,
            file_data: base64,
            mime_type: file.type || 'application/octet-stream',
            drive_config_id: driveConfigId,
            parent_folder_id: targetFolderId || false,
            root_folder_id: targetRootId,
        });
    }

    // ─── Manual Sync ───

    async onManualSync() {
        const driveId = this.state.activeDriveId;
        if (!driveId) return;

        // Validation: Don't start manual sync if auto sync is running for this drive
        if (this.state.autoSyncingDrives[driveId]) {
            this.notificationService.add("Automatic sync is already in progress for this drive. Please wait.", { type: "warning" });
            return;
        }

        // Skip if already manual syncing this drive
        if (this.state.manualSyncingDrives[driveId]) return;

        this.state.manualSyncingDrives[driveId] = true;
        this.state.manualSyncCompleted[driveId] = false;
        
        // Show full screen loader for manual sync
        this.state.showManualSyncLoader = true;
        this.state.loaderMessage = `Syncing ${this.activeDriveName}...`;
        
        this.notificationService.add(`Sync started for ${this.activeDriveName}...`, { type: "info" });

        try {
            // Bi-directional sync for the whole drive or active root
            await this.orm.call("google.drive.config", "action_trigger_sync", [], {
                drive_config_id: driveId,
                root_folder_id: this.state.activeRootId || false
            });

            // If we are still looking at the same drive, refresh view
            if (this.state.activeDriveId === driveId) {
                await this.loadFiles(this.state.currentFolderId);
            }

            this.notificationService.add(`Sync completed for ${this.activeDriveName}!`, { type: "success" });
            this.state.manualSyncCompleted[driveId] = true;
            await this.checkPendingChanges();

            setTimeout(() => {
                this.state.manualSyncCompleted[driveId] = false;
            }, 3000);
        } catch (e) {
            this.notificationService.add(`Sync failed for ${this.activeDriveName}: ` + (e.message || "Unknown error"), { type: "danger" });
        } finally {
            this.state.manualSyncingDrives[driveId] = false;
            this.state.showManualSyncLoader = false;
        }
    }

    // ─── Sync Mode ───

    toggleSyncModeMenu() {
        if (this.isAutoSyncingCurrentDrive) {
            return;
        }
        const target = !this.state.showSyncModeMenu;
        this.closeAllMenus();
        this.state.showSyncModeMenu = target;
    }

    setSyncMode(mode) {
        this.state.syncMode = mode;
        this.state.showSyncModeMenu = false;
        localStorage.setItem('gd_sync_mode', mode);
    }

    /**
     * Trigger a one-way auto-sync (Odoo → Google Drive) for a specific drive.
     *
     * Each drive maintains its own syncing state so that switching drives
     * mid-sync does not cancel or interfere with ongoing uploads.
     *
     * The rotating spinner on the Auto Sync badge is shown while
     * `autoSyncingDrives[driveId]` is true and hidden once sync completes.
     *
     * @param {number|null} driveId  google.drive.config ID to sync.
     *   Defaults to the currently active drive.
     */
    async triggerAutoSync(driveId) {
        const targetDriveId = driveId || this.state.activeDriveId;
        if (!targetDriveId) return;

        // Skip if manual sync is running for this drive (mutual exclusion)
        if (this.state.manualSyncingDrives[targetDriveId]) return;

        // Guard: skip if this drive is already syncing
        if (this.state.autoSyncingDrives[targetDriveId]) return;

        // Mark this drive as syncing — triggers spinner in the template
        this.state.autoSyncingDrives = {
            ...this.state.autoSyncingDrives,
            [targetDriveId]: true,
        };

        try {
            // Get all pending IDs for this drive to process them individually for real-time feedback
            const pendingIds = await this.orm.call(
                "google.drive.file",
                "get_pending_sync_ids",
                [],
                { drive_config_id: targetDriveId }
            );

            if (pendingIds && pendingIds.length > 0) {
                for (const id of pendingIds) {
                    try {
                        const idx = this.state.files.findIndex(f => f.id === id);
                        if (idx !== -1) {
                            this.state.files[idx].sync_state = 'uploading';
                            this.state.files[idx].upload_progress = Math.max(5, this.state.files[idx].upload_progress || 10);
                            this.startFileSyncProgressAnimation(id);
                        }

                        await this.orm.call(
                            "google.drive.file",
                            "action_sync_single_record",
                            [[id]],
                            { context: { sync_type: 'auto' } }
                        );

                        if (this.state.activeDriveId === targetDriveId) {
                            const updated = await this.orm.read("google.drive.file", [id], [
                                "sync_state", "google_file_id", "google_url", "last_synced", "upload_progress"
                            ]);
                            if (updated && updated.length > 0) {
                                const idx2 = this.state.files.findIndex(f => f.id === id);
                                if (idx2 !== -1) {
                                    Object.assign(this.state.files[idx2], updated[0]);
                                }
                            }
                        }
                    } catch (err) {
                        console.error("Single file sync failed", id, err);
                    } finally {
                        this.stopFileSyncProgressAnimation(id);
                    }
                }
            }

            // Briefly mark as completed so the template can react if needed
            this.state.autoSyncCompleted = {
                ...this.state.autoSyncCompleted,
                [targetDriveId]: true,
            };

            // Final safety reload of the file list
            if (this.state.activeDriveId === targetDriveId) {
                await this.loadFiles(this.state.currentFolderId);
            }

            // Remove the completed flag after 2 s (icon disappears)
            setTimeout(() => {
                const completed = { ...this.state.autoSyncCompleted };
                delete completed[targetDriveId];
                this.state.autoSyncCompleted = completed;
            }, 2000);
        } catch (e) {
            console.error("Auto sync failed for drive", targetDriveId, e);
        } finally {
            // Always clear the syncing flag for this drive
            const syncing = { ...this.state.autoSyncingDrives };
            delete syncing[targetDriveId];
            this.state.autoSyncingDrives = syncing;
        }
    }

    /** True when the currently-viewed drive has an auto-sync in progress. */
    get isAutoSyncingCurrentDrive() {
        return !!this.state.autoSyncingDrives[this.state.activeDriveId];
    }

    _isActionRestrictedInTrash() {
        return this.state.activeSection === 'trash' && 
               this.state.currentFolderId !== 'trash_root' && 
               this.state.currentFolderId !== null;
    }
}

FileExplorer.template = "google_drive_odoo_integration.FileExplorer";

registry.category("actions").add("google_drive_odoo_integration.file_explorer", FileExplorer);

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
        // Embedded preview — works for most file types when Drive permissions allow it
        return `https://drive.google.com/file/d/${file.google_file_id}/preview`;
    }

    get openInDriveUrl() {
        const { file } = this.props;
        // Direct /view link — opens in Google Drive, bypasses any iframe embedding restrictions
        return file.google_url || `https://drive.google.com/file/d/${file.google_file_id}/view`;
    }
}

export class ShareDriveLinkDialog extends Component {
    static template = "google_drive_odoo_integration.ShareDriveLinkDialog";
    static components = { Dialog };
    static props = {
        files: Array,
        close: Function,
        onReady: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notificationService = useService("notification");

        this.state = useState({
            copied: false,
            loading: true,
            // Permissions
            permissions: [],
            generalAccess: 'restricted',
            anyoneRole: 'reader',
            // Settings
            writersCanShare: true,
            copyRequiresWriterPermission: false,
            // Add people
            addEmail: '',
            addRole: 'reader',
            addingPerson: false,
            // Contact Picker
            showContactPicker: false,
            contactSearchResults: [],
            contactLoading: false,
            contactSearchQuery: '',
            // Dropdowns
            showGeneralAccessDropdown: false,
            showAnyoneRoleDropdown: false,
            showSettingsPanel: false,
            activePermRoleDropdown: null,
            // Loader
            showActionLoader: false,
            loaderMessage: 'Loading...',
            // Error
            error: null,
        });

        onWillStart(async () => {
            if (this.props.files.length === 1 && this.props.files[0].google_file_id) {
                await this.loadShareInfo();
            } else {
                this.state.loading = false;
            }
            if (this.props.onReady) this.props.onReady();
        });
    }

    // ─── Contact Picker ───

    async toggleContactPicker() {
        // Close all OTHER share menus first, then toggle the picker
        const nextState = !this.state.showContactPicker;
        this.closeAllShareMenus();
        this.state.showContactPicker = nextState;
        if (this.state.showContactPicker && this.state.contactSearchResults.length === 0) {
            await this.searchContacts('');
        }
    }

    async onContactSearchInput(ev) {
        this.state.contactSearchQuery = ev.target.value;
        await this.searchContacts(this.state.contactSearchQuery);
    }

    async searchContacts(query) {
        this.state.contactLoading = true;
        try {
            const domain = [['email', '!=', false]];
            if (query) {
                domain.push('|');
                domain.push(['name', 'ilike', query]);
                domain.push(['email', 'ilike', query]);
            }
            
            const results = await this.orm.searchRead(
                "res.partner",
                domain,
                ["id", "name", "email", "image_128"],
                { limit: 8, order: "name asc" }
            );
            this.state.contactSearchResults = results;
        } catch (e) {
            console.error("Failed to search contacts", e);
        } finally {
            this.state.contactLoading = false;
        }
    }

    selectContact(partner) {
        this.state.addEmail = partner.email;
        this.state.showContactPicker = false;
    }

    onContactKeydown(ev) {
        if (ev.key === 'Escape') {
            this.state.showContactPicker = false;
        }
    }

    get file() {
        return this.props.files[0];
    }

    get isSingleFile() {
        return this.props.files.length === 1;
    }

    get shareableFiles() {
        return this.props.files.filter(f => f.google_url);
    }

    get shareLink() {
        const file = this.file;
        if (!file || !file.google_file_id) return '';
        if (file.file_type === 'folder') {
            return `https://drive.google.com/drive/folders/${file.google_file_id}?usp=sharing`;
        }
        return `https://drive.google.com/file/d/${file.google_file_id}/view?usp=sharing`;
    }

    async loadShareInfo() {
        this.state.loading = true;
        this.state.error = null;
        try {
            const result = await this.orm.call(
                "google.drive.file",
                "action_get_share_info",
                [[this.file.id]]
            );
            if (result.error) {
                this.state.error = result.error;
            } else {
                this.state.permissions = result.permissions || [];
                this.state.generalAccess = result.generalAccess || 'restricted';
                this.state.anyoneRole = result.anyoneRole || 'reader';
                this.state.writersCanShare = result.writersCanShare !== false;
                this.state.copyRequiresWriterPermission = result.copyRequiresWriterPermission || false;
            }
        } catch (e) {
            console.error("Failed to load share info", e);
            this.state.error = "Failed to load sharing information.";
        } finally {
            this.state.loading = false;
        }
    }

    // ─── Add People ───

    onEmailInput(ev) {
        this.state.addEmail = ev.target.value;
    }

    onEmailKeydown(ev) {
        if (ev.key === 'Enter') {
            this.addPerson();
        }
    }

    setAddRole(role) {
        this.state.addRole = role;
    }

    async addPerson() {
        const email = this.state.addEmail.trim();
        if (!email) return;

        // Basic email validation
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
            this.notificationService.add("Please enter a valid email address.", { type: "warning" });
            return;
        }

        this.state.addingPerson = true;
        this.state.loaderMessage = 'Sending invitation...';
        this.state.showActionLoader = true;
        try {
            const result = await this.orm.call(
                "google.drive.file",
                "action_add_permission",
                [[this.file.id]],
                { email: email, role: this.state.addRole, send_notification: true }
            );
            if (result.error) {
                this.notificationService.add(result.error, { type: "danger" });
            } else if (result.success) {
                // Reload share info to get complete permission data including photoLinks
                await this.loadShareInfo();
                this.state.addEmail = '';
                this.notificationService.add(`Shared with ${email}`, { type: "success" });
            }
        } catch (e) {
            this.notificationService.add("Failed to add person.", { type: "danger" });
        } finally {
            this.state.addingPerson = false;
            this.state.showActionLoader = false;
        }
    }

    // ─── Permission Role Dropdown ───

    togglePermRoleDropdown(permId) {
        if (this.state.activePermRoleDropdown === permId) {
            this.state.activePermRoleDropdown = null;
        } else {
            this.state.activePermRoleDropdown = permId;
        }
    }

    async updatePermissionRole(perm, role) {
        this.state.activePermRoleDropdown = null;
        if (perm.role === role) return;

        this.state.loaderMessage = 'Updating permission...';
        this.state.showActionLoader = true;
        try {
            const result = await this.orm.call(
                "google.drive.file",
                "action_update_permission",
                [[this.file.id]],
                { permission_id: perm.id, role: role }
            );
            if (result.error) {
                this.notificationService.add(result.error, { type: "danger" });
            } else {
                perm.role = role;
                this.notificationService.add(`Role updated to ${this.getRoleLabel(role)}`, { type: "success" });
            }
        } catch (e) {
            this.notificationService.add("Failed to update permission.", { type: "danger" });
        } finally {
            this.state.showActionLoader = false;
        }
    }

    async removePermission(perm) {
        this.state.activePermRoleDropdown = null;
        this.state.loaderMessage = 'Removing access...';
        this.state.showActionLoader = true;
        try {
            const result = await this.orm.call(
                "google.drive.file",
                "action_remove_permission",
                [[this.file.id]],
                { permission_id: perm.id }
            );
            if (result.error) {
                this.notificationService.add(result.error, { type: "danger" });
            } else {
                this.state.permissions = this.state.permissions.filter(p => p.id !== perm.id);
                this.notificationService.add("Access removed.", { type: "success" });
            }
        } catch (e) {
            this.notificationService.add("Failed to remove access.", { type: "danger" });
        } finally {
            this.state.showActionLoader = false;
        }
    }

    getRoleLabel(role) {
        const labels = {
            'owner': 'Owner',
            'writer': 'Editor',
            'commenter': 'Commenter',
            'reader': 'Viewer',
        };
        return labels[role] || role;
    }

    getPermInitial(perm) {
        if (perm.displayName) return perm.displayName.charAt(0).toUpperCase();
        if (perm.emailAddress) return perm.emailAddress.charAt(0).toUpperCase();
        return '?';
    }

    onImageError(ev, perm) {
        // Hide the broken image and show initials instead
        ev.target.style.display = 'none';
        // Find the parent avatar div and ensure initials are shown
        const avatarDiv = ev.target.parentElement;
        if (avatarDiv) {
            const initialsSpan = avatarDiv.querySelector('.gd_share_person_initial');
            if (initialsSpan) {
                initialsSpan.classList.remove('gd_hidden');
            }
        }
    }

    // ─── General Access ───

    toggleGeneralAccessDropdown() {
        const tgt = !this.state.showGeneralAccessDropdown;
        this.closeAllShareMenus();
        this.state.showGeneralAccessDropdown = tgt;
    }

    toggleAnyoneRoleDropdown() {
        const tgt = !this.state.showAnyoneRoleDropdown;
        this.closeAllShareMenus();
        this.state.showAnyoneRoleDropdown = tgt;
    }

    closeAllShareMenus() {
        this.state.showGeneralAccessDropdown = false;
        this.state.showAnyoneRoleDropdown = false;
        // Note: showContactPicker is intentionally NOT closed here.
        // It is toggled independently by toggleContactPicker().
        this.state.activePermRoleDropdown = null;
    }

    async setGeneralAccess(accessType) {
        this.state.showGeneralAccessDropdown = false;

        if (accessType === this.state.generalAccess) return;

        this.state.loaderMessage = 'Updating general access...';
        this.state.showActionLoader = true;
        try {
            const result = await this.orm.call(
                "google.drive.file",
                "action_set_general_access",
                [[this.file.id]],
                { access_type: accessType, role: this.state.anyoneRole }
            );
            if (result.error) {
                this.notificationService.add(result.error, { type: "danger" });
            } else {
                this.state.generalAccess = accessType;
                if (accessType === 'anyone') {
                    this.notificationService.add("Anyone with the link can now access this file.", { type: "success" });
                } else {
                    this.notificationService.add("Access restricted to specific people only.", { type: "success" });
                }
            }
        } catch (e) {
            this.notificationService.add("Failed to update general access.", { type: "danger" });
        } finally {
            this.state.showActionLoader = false;
        }
    }

    async setAnyoneRole(role) {
        this.state.showAnyoneRoleDropdown = false;
        if (role === this.state.anyoneRole) return;

        this.state.loaderMessage = 'Updating access role...';
        this.state.showActionLoader = true;
        try {
            // Need to remove old anyone permission and create new one with new role
            const result = await this.orm.call(
                "google.drive.file",
                "action_set_general_access",
                [[this.file.id]],
                { access_type: 'anyone', role: role }
            );
            if (result.error) {
                this.notificationService.add(result.error, { type: "danger" });
            } else {
                this.state.anyoneRole = role;
            }
        } catch (e) {
            this.notificationService.add("Failed to update access role.", { type: "danger" });
        } finally {
            this.state.showActionLoader = false;
        }
    }

    // ─── Settings ───

    toggleSettingsPanel() {
        this.state.showSettingsPanel = !this.state.showSettingsPanel;
    }

    async onToggleWritersCanShare(ev) {
        const newVal = ev.target.checked;
        this.state.loaderMessage = 'Updating sharing permissions...';
        this.state.showActionLoader = true;
        try {
            const result = await this.orm.call(
                "google.drive.file",
                "action_update_sharing_settings",
                [[this.file.id]],
                { writers_can_share: newVal }
            );
            if (result.error) {
                this.notificationService.add(result.error, { type: "danger" });
                ev.target.checked = !newVal; // revert
            } else {
                this.state.writersCanShare = newVal;
                this.notificationService.add("Sharing settings updated", { type: "success" });
            }
        } catch (e) {
            this.notificationService.add("Failed to update setting.", { type: "danger" });
            ev.target.checked = !newVal;
        } finally {
            this.state.showActionLoader = false;
        }
    }

    async onToggleCopyRequiresWriter(ev) {
        const newVal = ev.target.checked;
        this.state.loaderMessage = 'Updating download permissions...';
        this.state.showActionLoader = true;
        try {
            const result = await this.orm.call(
                "google.drive.file",
                "action_update_sharing_settings",
                [[this.file.id]],
                { copy_requires_writer: !newVal }
            );
            if (result.error) {
                this.notificationService.add(result.error, { type: "danger" });
                ev.target.checked = !newVal;
            } else {
                this.state.copyRequiresWriterPermission = !newVal;
                this.notificationService.add("Download settings updated", { type: "success" });
            }
        } catch (e) {
            this.notificationService.add("Failed to update setting.", { type: "danger" });
            ev.target.checked = !newVal;
        } finally {
            this.state.showActionLoader = false;
        }
    }

    // ─── Copy Link ───

    async onCopyLink() {
        const link = this.shareLink || this.shareableFiles.map(f => f.google_url).join('\n');
        if (!link) return;

        try {
            await navigator.clipboard.writeText(link);
            this.state.copied = true;
            setTimeout(() => {
                this.state.copied = false;
            }, 2000);
        } catch {
            prompt("Copy link:", link);
        }
    }
}
