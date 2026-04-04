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
            sidebarCollapsed: false,
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
            uploadProgress: { current: 0, total: 0 }
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

    get activeDriveName() {
        const drive = this.state.drives.find(d => d.id === this.state.activeDriveId);
        return drive ? drive.name : 'No Drive';
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
            "owner_name", "last_modified", "sync_state", "starred",
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
                const recentIds = JSON.parse(localStorage.getItem('gd_recent_file_ids') || '[]');
                if (recentIds.length > 0) {
                    files = await this.orm.searchRead("google.drive.file",
                        [["id", "in", recentIds], ["file_type", "!=", "folder"], ["active", "=", true]],
                        commonFields
                    );
                    files.sort((a, b) => recentIds.indexOf(a.id) - recentIds.indexOf(b.id));
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
                if (this.state.isDriveOverview) {
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
                "owner_name", "last_modified", "sync_state", "starred",
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

        await this._updateNavigationState(null, this._sectionLabel(section));

        if (section === 'my_drive') {
            this.state.isRootTreeExpanded = !this.state.isRootTreeExpanded;
            await this.loadFiles(null);
        } else {
            this.state.isRootTreeExpanded = false;
            if (section === 'trash') {
                await this.loadFiles('trash_root');
            } else {
                await this.loadFiles(null);
            }
        }
    }

    toggleSidebar() {
        this.state.sidebarCollapsed = !this.state.sidebarCollapsed;
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
            if (this.state.activeSection !== 'trash') {
                this.state.activeSection = 'my_drive';
            }
            await this._updateNavigationState(file.id, file.name);
            await this.loadFiles(file.id);
        }
    }

    async onBreadcrumbClick(index) {
        const bc = this.state.breadcrumbs[index];
        if (bc.id === 'section') {
            this.state.activeRootId = null;
            this.state.isDriveOverview = true;
            await this._updateNavigationState(null, bc.name);
            await this.loadFiles(null);
        } else {
            const folderId = (bc.id === false) ? null : bc.id;
            await this._updateNavigationState(folderId, bc.name);
            await this.loadFiles(folderId);
        }
    }

    async onBackClick() {
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
        // Single click now opens folders or previews files
        this.addToRecent(file);
        if (file.file_type === 'folder') {
            this.onFolderClick(file);
        } else {
            // Google Drive preview supports a wide array of formats including office files.
            const previewableTypes = [
                'image', 'pdf', 'video', 'audio', 'text', 'document', 'spreadsheet', 'presentation',
                'msword', 'excel', 'powerpoint', 'officedocument' // Add MS Office formats
            ];
            const mime = file.mime_type || '';
            const isPreviewable = previewableTypes.some(t => mime.includes(t));

            if (isPreviewable && file.google_file_id) {
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

    async _loadTreeChildren(parentId) {
        if (!this.state.activeRootId && String(parentId).startsWith("root_")) return;

        try {
            const domain = [
                ["file_type", "=", "folder"],
                ["drive_config_id", "=", this.state.activeDriveId],
                ["active", "=", true]
            ];

            if (this.state.activeRootId) {
                domain.push(["root_folder_id", "=", this.state.activeRootId]);
            }

            let cacheKey = parentId;
            if (String(parentId).startsWith("root_") || parentId === false || parentId === null) {
                cacheKey = `root_${this.state.activeRootId}`;
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

    async _refreshTreeForParent(parentId) {
        const id = (parentId === false || parentId === null) ? `root_${this.state.activeRootId}` : parentId;
        // Only refresh if already loaded or if it's the root being updated
        if (this.state.folderTree[id] || String(id).startsWith("root_")) {
            await this._loadTreeChildren(id);
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
        const isAlreadySelected = this.state.activeFolderTreeId === folderId &&
            this.state.activeSection === 'my_drive';

        this.state.activeSection = 'my_drive';

        if (isAlreadySelected) {
            await this.toggleFolderTree(folderId);
        } else {
            if (!this.isTreeNodeExpanded(folderId)) {
                await this.toggleFolderTree(folderId);
            }
        }

        await this._updateNavigationState(folderId, folderName);
        await this.loadFiles(folderId);
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
                await this._refreshTreeForParent(parentId);
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
        this.state.loading = true;

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
            this.state.loading = false;
        }
    }

    onShareSelected() {
        const files = this.selectedFilesList;
        if (files.length === 0) return;

        this.dialogService.add(ShareDriveLinkDialog, {
            files: files,
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

        let downloadCount = 0;
        for (const file of files) {
            if (file.file_type !== 'folder') {
                const downloadUrl = `/google_drive/download/${file.id}`;

                // Create a temporary anchor element to trigger download without opening a new tab
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = downloadUrl;
                // Adding the download attribute prompts a file download prompt rather than navigation
                a.download = file.name;

                document.body.appendChild(a);
                a.click();

                // Clean up the DOM afterwards
                setTimeout(() => {
                    document.body.removeChild(a);
                }, 100);

                downloadCount++;
            }
        }

        if (downloadCount > 0) {
            this.notificationService.add(`Downloading ${downloadCount} file(s)...`, { type: "info" });
        } else {
            this.notificationService.add("Folders cannot be downloaded.", { type: "warning" });
        }
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

            // Step 2: Try background delete ONLY in Auto Sync mode
            if (this.state.syncMode === 'auto') {
                this.orm.call("google.drive.file", "delete_on_drive_and_unlink", [ids])
                    .then((success) => {
                        if (success) {
                            this.notificationService.add(
                                `Deleted from Google Drive: ${nameList}`,
                                { type: "success" }
                            );
                        } else {
                            this.notificationService.add(
                                "Odoo items hidden. Some items will be deleted from Google Drive during the next sync.",
                                { type: "info" }
                            );
                        }
                    }).catch(() => {
                        console.log("Background Drive deletion deferred to next sync.");
                    });
            } else {
                // Manual Sync Mode
                this.notificationService.add(
                    "Item(s) archived locally. Please click 'Sync' to remove from Google Drive.",
                    { type: "info" }
                );
            }
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

            // Sync tree if it was a folder
            if (file.file_type === 'folder') {
                await this._refreshTreeForParent(file.parent_folder_id ? file.parent_folder_id[0] : null);
            }

            // Step 3: Call Drive rename in background only if in Auto Sync mode
            if (file.google_file_id && this.state.syncMode === 'auto') {
                this.orm.call("google.drive.file", "rename_on_drive_by_id", [], {
                    record_id: fileId,
                    new_name: newName
                }).then(() => {
                    this.loadFiles(this.state.currentFolderId); // Refresh to clear 'pending'
                    this.notificationService.add(`Renamed to "${newName}" on Google Drive`, { type: "success" });
                }).catch(() => {
                    this.notificationService.add(`Failed to rename "${newName}" on Google Drive`, { type: "warning" });
                });
            } else {
                this.notificationService.add("Item renamed successfully locally!", { type: "success" });
                await this.checkPendingChanges();
            }
        } catch (e) {
            this.notificationService.add("Failed to rename item.", { type: "danger" });
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
        if (state === 'error') return 'fa-exclamation-triangle';
        if (state === 'pending_delete') return 'fa-trash-o';
        return 'fa-circle-o';
    }

    hasSyncBadgeLabel(file) {
        return ['error', 'pending', 'pending_delete'].includes(file.sync_state);
    }

    getSyncBadgeText(file) {
        if (file.sync_state === 'error') return 'ERROR';
        if (file.sync_state === 'pending') return 'PUSHING';
        if (file.sync_state === 'pending_delete') return 'REMOVING';
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
            this.notificationService.add("Please enter a folder name.", { type: "warning" });
            return;
        }

        let driveConfigId = false;
        if (this.state.drives.length > 0) {
            driveConfigId = this.state.drives[0].id;
        }

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
            await this._refreshTreeForParent(this.state.currentFolderId);

            // Auto sync in background (non-blocking)
            if (this.state.syncMode === 'auto') {
                this.triggerAutoSync();
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

            this.state.uploading = true;
            this.state.uploadProgress = { current: 0, total: files.length };

            for (const file of files) {
                await this._uploadSingleFile(file, targetFolderId, targetRootId);
                this.state.uploadProgress.current += 1;
            }

            this.state.uploading = false;

            // Only reload the view if the user is still looking at the folder where the files were uploaded
            if (this.state.currentFolderId === targetFolderId) {
                await this.loadFiles(targetFolderId);
            }
            this.notificationService.add("Files uploaded successfully!", { type: "success" });

            // Auto sync in background (non-blocking)
            if (this.state.syncMode === 'auto') {
                this.triggerAutoSync();
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

        let driveConfigId = false;
        if (this.state.drives.length > 0) {
            driveConfigId = this.state.drives[0].id;
        }

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
        this.state.syncing = true;
        this.state.syncCompleted = false;
        this.notificationService.add("Sync started...", { type: "info" });
        try {
            // Only sync the active root folder as requested
            await this.orm.call("google.drive.config", "action_trigger_sync", [], {
                root_folder_id: this.state.activeRootId || false
            });
            await this.loadFiles(this.state.currentFolderId);
            this.notificationService.add("Sync completed!", { type: "success" });
            this.state.syncCompleted = true;
            await this.checkPendingChanges();
            setTimeout(() => {
                this.state.syncCompleted = false;
            }, 3000);
        } catch (e) {
            this.notificationService.add("Sync failed: " + (e.message || "Unknown error"), { type: "danger" });
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
            this.notificationService.add("Auto sync failed: " + (e.message || "Unknown error"), { type: "danger" });
        }
    }

    _isActionRestrictedInTrash() {
        return this.state.activeSection === 'trash' && 
               this.state.currentFolderId !== 'trash_root' && 
               this.state.currentFolderId !== null;
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

class ShareDriveLinkDialog extends Component {
    static template = "google_drive_odoo_integration.ShareDriveLinkDialog";
    static components = { Dialog };
    static props = {
        files: Array,
        close: Function,
    };

    setup() {
        this.state = useState({
            copied: false,
        });
    }

    get shareableFiles() {
        return this.props.files.filter(f => f.google_url);
    }

    async onCopyLink() {
        const links = this.shareableFiles.map(f => f.google_url);
        if (links.length === 0) return;

        const linkText = links.join('\n');
        try {
            await navigator.clipboard.writeText(linkText);
            this.state.copied = true;
            setTimeout(() => {
                this.state.copied = false;
            }, 2000);
        } catch {
            // Fallback for older browsers
            prompt("Copy link:", linkText);
        }
    }
}
