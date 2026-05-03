# -*- coding: utf-8 -*-
import time
from odoo import models, fields, api
import base64


class GoogleDriveFile(models.Model):
    _name = 'google.drive.file'
    _description = 'Google Drive File'
    _order = 'file_type desc, name asc'

    _sql_constraints = [
        ('google_file_id_drive_config_unique', 'unique(google_file_id, drive_config_id)',
         'A file with this Google ID already exists for this drive configuration.')
    ]

    name = fields.Char('File Name', required=True)
    drive_config_id = fields.Many2one('google.drive.config', string='Drive', required=True, ondelete='cascade')
    root_folder_id = fields.Many2one('google.drive.root.folder', string='Root Folder', ondelete='cascade')
    file_type = fields.Selection([
        ('file', 'File'),
        ('folder', 'Folder'),
    ], string='Type', default='file', required=True)
    mime_type = fields.Char('MIME Type')
    file_size = fields.Float('Size (KB)')
    md5_checksum = fields.Char('MD5 Checksum', index=True, help="Google Drive MD5 hash for exact content matching")
    owner_name = fields.Char('Owner', default='Me')
    last_modified = fields.Datetime('Last Modified')
    starred = fields.Boolean('Starred', default=False)
    google_file_id = fields.Char('Google File ID')
    google_url = fields.Char('Google Drive URL')
    parent_folder_id = fields.Many2one('google.drive.file', string='Parent Folder',
                                       domain="[('file_type', '=', 'folder')]")
    child_ids = fields.One2many('google.drive.file', 'parent_folder_id', string='Contents')
    last_synced = fields.Datetime('Last Synced')
    sync_state = fields.Selection([
        ('synced', 'Synced'),
        ('pending', 'Pending'),
        ('uploading', 'Uploading'),
        ('error', 'Error'),
        ('pending_delete', 'Pending Delete'),
    ], string='Sync Status', default='pending')
    upload_progress = fields.Float('Upload Progress', default=0.0, help='Upload progress as percentage (0-100)')
    active = fields.Boolean('Active', default=True)
    display_path = fields.Char('Location', compute='_compute_display_path')
    attachment_id = fields.Many2one('ir.attachment', string='Related Attachment', compute='_compute_attachment_id')

    def _compute_attachment_id(self):
        for record in self:
            if record.google_file_id:
                attachment = self.env['ir.attachment'].sudo().search([
                    ('google_file_id', '=', record.google_file_id)
                ], limit=1)
                record.attachment_id = attachment.id if attachment else False
            else:
                record.attachment_id = False

    @api.depends('drive_config_id', 'root_folder_id', 'parent_folder_id')
    def _compute_display_path(self):
        for record in self:
            path_parts = []
            if record.drive_config_id:
                path_parts.append(record.drive_config_id.name)
            if record.root_folder_id:
                path_parts.append(record.root_folder_id.name)
            
            # Navigate parents
            parent = record.parent_folder_id
            inner_parts = []
            while parent:
                inner_parts.append(parent.name)
                parent = parent.parent_folder_id
            
            if inner_parts:
                path_parts.extend(reversed(inner_parts))
            
            record.display_path = " / ".join(path_parts) if path_parts else ""

    def _resolve_parent_gdrive_id(self, parent_folder_id=False, root_folder_id=False):
        """Resolve the Google Drive parent folder ID from Odoo records."""
        if parent_folder_id:
            parent_rec = self.browse(parent_folder_id)
            if parent_rec.exists() and parent_rec.google_file_id:
                return parent_rec.google_file_id
        if root_folder_id:
            root_rec = self.env['google.drive.root.folder'].browse(root_folder_id)
            if root_rec.exists():
                return root_rec.root_id
        return False

    @api.model
    def get_folder_breadcrumbs(self, folder_id):
        """Recursively build breadcrumbs for a folder."""
        breadcrumbs = []
        folder = self.with_context(active_test=False).browse(folder_id)
        
        curr = folder
        while curr:
            breadcrumbs.insert(0, {'id': curr.id, 'name': curr.name})
            curr = curr.parent_folder_id
            
        # Add root and section information
        if folder.exists():
            if not folder.active:
                # Trashed item - prefix with Trash
                breadcrumbs.insert(0, {'id': 'section', 'name': 'Trash'})
            elif folder.root_folder_id:
                breadcrumbs.insert(0, {'id': None, 'name': folder.root_folder_id.name})
                if folder.drive_config_id:
                    breadcrumbs.insert(0, {'id': 'section', 'name': folder.drive_config_id.name})
                
        return breadcrumbs

    def unlink(self):
        # Standard Odoo unlink 
        return super(GoogleDriveFile, self).unlink()

    def action_prompt_delete(self):
        """Open a confirmation wizard before deleting the folder/file."""
        self.ensure_one()
        # Find if this is configured in any sync
        configs = self.env['attachment.sync.config'].search([('google_folder_id', '=', self.id)])
        
        return {
            'name': 'Confirm Deletion',
            'type': 'ir.actions.act_window',
            'res_model': 'google.drive.file.delete.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_folder_id': self.id,
                'default_config_ids': [(6, 0, configs.ids)] if configs else [],
            }
        }

    def action_archive_recursive(self):
        """Archive records locally only. No changes made to Google Drive."""
        for record in self:
            record.child_ids.action_archive_recursive()
            record.write({
                'active': False,
                'sync_state': 'synced',
            })
        return True

    def action_unarchive(self):
        """Restore archived records locally. No changes made to Google Drive."""
        for record in self.with_context(active_test=False):
            record.write({
                'active': True,
                'sync_state': 'synced',
                'last_synced': fields.Datetime.now(),
            })
            record.child_ids.action_unarchive()
        return True


    @api.model
    def delete_on_drive_and_unlink(self, record_ids):
        """Move records to Google Drive Trash and then unlink them from Odoo.
        Called from Odoo's Trash Tab (Manual selection).
        """
        records = self.browse(record_ids).with_context(active_test=False)
        if not records:
            return True
        
        sync = self.env['google.drive.sync'].sudo()
        success_ids = []

        for record in records:
            # If no Drive ID, it's already "deleted" from Drive's perspective
            if not record.google_file_id:
                success_ids.append(record.id)
                continue
            
            # Use trash_file instead of hard delete per user request
            if sync.trash_file(record, trashed=True):
                success_ids.append(record.id)
                
        if success_ids:
            records_to_unlink = self.browse(success_ids).with_context(active_test=False)
            
            # Find all children recursively so we don't leave orphaned records in Odoo
            all_to_unlink = self.env['google.drive.file']
            
            def get_all_children(record):
                children = record.child_ids.with_context(active_test=False)
                result = children
                for child in children:
                    if child.file_type == 'folder':
                        result |= get_all_children(child)
                return result
                
            for rec in records_to_unlink:
                all_to_unlink |= rec
                if rec.file_type == 'folder':
                    all_to_unlink |= get_all_children(rec)
            
            all_to_unlink.unlink()
        
        return len(success_ids) == len(record_ids)
    def get_recursive_files_for_zip(self, record_ids):
        """Recursively collect all files under the given IDs, with their relative paths for a ZIP archive."""
        records = self.browse(record_ids)
        all_files = [] # List of tuples: (file_record, relative_path)
        
        def collect(recs, current_path=""):
            for rec in recs:
                # Sanitize name for ZIP paths (remove leading/trailing slashes)
                name = rec.name.strip('/')
                path = f"{current_path}/{name}" if current_path else name
                
                if rec.file_type == 'file':
                    all_files.append((rec, path))
                else:
                    # Empty folder? We could add it, but usually ZIPs care about files.
                    # Recurse into children
                    collect(rec.child_ids, path)
        
        collect(records)
        return all_files

    def write(self, vals):
        # We handle Drive rename asynchronously from JS to keep UI instant
        return super(GoogleDriveFile, self).write(vals)

    @api.model
    def action_move_items(self, item_ids, target_parent_id=None, target_root_id=None):
        """Move multiple files or folders to a new location."""
        if not item_ids:
            return False
            
        items = self.browse(item_ids)
        if not items:
            return False
            
        # Resolve target GDrive ID
        new_parent_gdrive_id = False
        if target_parent_id:
            target_parent = self.browse(target_parent_id)
            new_parent_gdrive_id = target_parent.google_file_id
        elif target_root_id:
            target_root = self.env['google.drive.root.folder'].browse(target_root_id)
            new_parent_gdrive_id = target_root.root_id
            
        if not new_parent_gdrive_id:
            return False
            
        sync_service = self.env['google.drive.sync'].sudo()
        
        for item in items:
            # Get current GDrive parent ID
            old_parent_gdrive_id = self._resolve_parent_gdrive_id(
                parent_folder_id=item.parent_folder_id.id,
                root_folder_id=item.root_folder_id.id
            )
            
            # Sync to Drive
            success = sync_service.move_file(
                item, old_parent_gdrive_id, new_parent_gdrive_id,
                target_parent_id=target_parent_id,
                target_root_id=target_root_id
            )
            
            if success:
                # Update Odoo record
                item.write({
                    'parent_folder_id': target_parent_id,
                    'root_folder_id': target_root_id if not target_parent_id else False,
                    'sync_state': 'synced',
                    'last_synced': fields.Datetime.now(),
                })
            else:
                item.write({'sync_state': 'error'})
                
        return True

    @api.model
    def get_trash_roots(self):
        """Return only the top-level archived items for the Trash tab.
        An item is a trash root if it is inactive AND:
        1. It has no parent, OR
        2. Its parent is active (not in trash).
        """
        # Get all inactive records
        inactive_records = self.with_context(active_test=False).search([('active', '=', False)])
        if not inactive_records:
            return []

        # Find the roots
        trash_roots = self.env['google.drive.file']
        for record in inactive_records:
            if not record.parent_folder_id or record.parent_folder_id.active:
                trash_roots |= record

        # Return the data in the format expected by the JS file explorer
        result = trash_roots.read([
            "name", "file_type", "mime_type", "google_url", "file_size",
            "owner_name", "last_modified", "sync_state", "starred", 
            "drive_config_id", "google_file_id", "attachment_id", "display_path"
        ])
        return result

    @api.model
    def rename_on_drive_by_id(self, record_id, new_name, old_name=None):
        """Rename a file/folder on Google Drive. 
        Called asynchronously from JS after Odoo UI updates instantly.
        """
        record = self.browse(record_id)
        if not record.exists() or not record.google_file_id:
            return False

        # Use old_name from JS (captured before the write), fall back to record.name
        rename_old = old_name or record.name
        success = self.env['google.drive.sync'].sudo().with_context(
            rename_old_name=rename_old
        ).rename_file(record, new_name)
        if success:
            super(GoogleDriveFile, record).write({
                'sync_state': 'synced',
                'last_synced': fields.Datetime.now(),
            })
        else:
            super(GoogleDriveFile, record).write({
                'sync_state': 'error',
            })
        return success

    def action_open_in_drive(self):
        """Open the file in Google Drive."""
        self.ensure_one()
        if self.google_url:
            return {
                'type': 'ir.actions.act_url',
                'url': self.google_url,
                'target': 'new',
            }

    @api.model
    def action_upload_from_explorer(self, file_name, file_data, mime_type,
                                     drive_config_id, root_folder_id=False, parent_folder_id=False):
        """Upload a file from the file explorer UI.

        Creates a local google.drive.file record with 'pending' status.
        The actual upload to Google Drive happens when the user clicks Sync.
        """
        config = self.env['google.drive.config'].browse(drive_config_id)
        if not config.exists():
            return False

        raw_data = base64.b64decode(file_data)
        file_size_bytes = len(raw_data)

        # Handle versioning - append _v2, _v3 etc if file name already exists 
        base_name = file_name
        extension = ""
        if '.' in file_name:
            parts = file_name.rsplit('.', 1)
            base_name = parts[0]
            extension = "." + parts[1]

        domain = [
            ('name', '=', file_name),
            ('drive_config_id', '=', config.id),
            ('parent_folder_id', '=', parent_folder_id or False),
            ('file_type', '=', 'file'),
        ]
        counter = 1
        while self.search_count(domain) > 0:
            counter += 1
            file_name = f"{base_name}_v{counter}{extension}"
            domain[0] = ('name', '=', file_name)

        # Store the attachment locally (skip Drive sync — will happen on Sync click)
        attachment = self.env['ir.attachment'].with_context(skip_gdrive_sync=True).create({
            'name': file_name,
            'raw': raw_data,
            'mimetype': mime_type,
            'res_model': 'google.drive.file',
            'res_id': 0, # Will be set below
        })

        # Create a record in the file explorer with pending status
        explorer_record = self.create({
            'name': file_name,
            'drive_config_id': config.id,
            'root_folder_id': root_folder_id or False,
            'file_type': 'file',
            'mime_type': mime_type,
            'file_size': file_size_bytes,
            'parent_folder_id': parent_folder_id or False,
            'owner_name': self.env.user.name,
            'last_modified': fields.Datetime.now(),
            'sync_state': 'pending',
            'upload_progress': 0.0,
        })

        # Link attachment to file explorer record properly
        attachment.write({'res_id': explorer_record.id})

        return explorer_record.id


    @api.model
    def get_pending_sync_ids(self, drive_config_id=None):
        """Returns a list of IDs for records that need syncing (push) for a drive."""
        domain = [('sync_state', 'in', ['pending', 'error']), ('active', '=', True)]
        if drive_config_id:
            domain.append(('drive_config_id', '=', drive_config_id))
        # Important: Order folders first so they exist on Drive before files are uploaded to them
        records = self.sudo().search(domain, order='file_type desc, id asc')
        return records.ids

    def action_sync_single_record(self):
        """Frontend entry point to sync a single record."""
        self.ensure_one()
        return self._sync_single_record(self)

    def _sync_single_record(self, record):
        """Internal helper to sync one record. Logic extracted from sync_pending_to_drive."""
        sync = self.env['google.drive.sync'].sudo()
        log_model = self.env['google.drive.sync.log']
        sync_type = self.env.context.get('sync_type', 'manual')
        
        # Resolve root folder name and folder path for logging
        root_name = False
        folder_path = False
        if record.root_folder_id:
            root_rec = self.env['google.drive.root.folder'].sudo().browse(record.root_folder_id.id)
            root_name = root_rec.name if root_rec.exists() else False
        # Build folder path by walking the parent chain
        try:
            parts = []
            parent = record.parent_folder_id
            while parent:
                parts.append(parent.name or '')
                parent = parent.parent_folder_id
            
            # If it's a root folder item, the parent is the root_folder_id itself
            if not parts and record.root_folder_id:
                parts.insert(0, record.root_folder_id.name or '')
                
            if parts:
                folder_path = ' / '.join(reversed(parts))
        except Exception:
            pass

        # 1. New Folder
        if record.file_type == 'folder' and not record.google_file_id:
            parent_gdrive_id = record._resolve_parent_gdrive_id(
                record.parent_folder_id.id if record.parent_folder_id else False,
                record.root_folder_id.id if record.root_folder_id else False,
            )
            t0 = time.time()
            try:
                result = sync.with_context(sync_type=sync_type).create_folder_in_drive(
                    record.name, record.drive_config_id, parent_gdrive_id=parent_gdrive_id, file_record=record)
                if result:
                    record.write({
                        'google_file_id': result['google_file_id'],
                        'google_url': result['google_url'],
                        'sync_state': 'synced',
                        'last_synced': fields.Datetime.now(),
                    })
                    # Notify explorer about the new folder in its parent
                    self.env['google.drive.sync'].sudo()._notify_folder_sync(record.parent_folder_id.id)
                    return True
                else:
                    record.write({'sync_state': 'error'})
            except Exception as e:
                record.write({'sync_state': 'error'})
                log_model.log_operation(
                    config=record.drive_config_id,
                    file_name=record.name,
                    operation='create_folder',
                    state='fail',
                    error_message=str(e),
                    sync_type=sync_type,
                    file_type='folder',
                    root_folder_name=root_name,
                    folder_path=folder_path,
                    duration=time.time() - t0,
                )
            return False

        # 2. New File
        if record.file_type == 'file' and not record.google_file_id:
            parent_gdrive_id = record._resolve_parent_gdrive_id(
                record.parent_folder_id.id if record.parent_folder_id else False,
                record.root_folder_id.id if record.root_folder_id else False,
            )
            attachment = self.env['ir.attachment'].sudo().search([
                ('res_model', '=', 'google.drive.file'),
                ('res_id', '=', record.id),
                ('google_file_id', '=', False),
            ], limit=1)
            if not attachment:
                attachment = self.env['ir.attachment'].sudo().search([
                    ('name', '=', record.name),
                    ('google_file_id', '=', False),
                ], limit=1)

            if not attachment or not attachment.raw:
                log_model.log_operation(
                    config=record.drive_config_id,
                    file_name=record.name,
                    operation='upload',
                    state='fail',
                    error_message='No attachment data found for upload',
                    sync_type=sync_type,
                    file_type='file',
                    root_folder_name=root_name,
                    folder_path=folder_path,
                )
                return False

            t0 = time.time()
            try:
                # Set uploading state and initial progress
                record.write({
                    'sync_state': 'uploading',
                    'upload_progress': 0.0
                })

                result = sync.with_context(sync_type=sync_type).upload_file_to_drive(
                    record.name, attachment.raw,
                    record.mime_type or 'application/octet-stream',
                    record.drive_config_id, parent_gdrive_id=parent_gdrive_id
                )

                if result:
                    record.write({
                        'google_file_id': result['google_file_id'],
                        'google_url': result['google_url'],
                        'sync_state': 'synced',
                        'upload_progress': 100.0,
                        'last_synced': fields.Datetime.now(),
                    })
                    # Notify explorer about the new file in its parent
                    self.env['google.drive.sync'].sudo()._notify_folder_sync(record.parent_folder_id.id)
                    attachment.with_context(skip_gdrive_sync=True).write({
                        'google_file_id': result['google_file_id'],
                    })
                    log_model.log_operation(
                        config=record.drive_config_id,
                        file_name=record.name,
                        operation='upload',
                        state='success',
                        sync_type=sync_type,
                        file_type='file',
                        root_folder_name=root_name,
                        folder_path=folder_path,
                        google_file_id=result['google_file_id'],
                        file_size=len(attachment.raw) if attachment.raw else 0,
                        duration=time.time() - t0,
                    )
                    return True
                else:
                    record.write({'sync_state': 'error', 'upload_progress': 0.0})
                    log_model.log_operation(
                        config=record.drive_config_id,
                        file_name=record.name,
                        operation='upload',
                        state='fail',
                        error_message='Upload returned no result',
                        sync_type=sync_type,
                        file_type='file',
                        root_folder_name=root_name,
                        folder_path=folder_path,
                        duration=time.time() - t0,
                    )
            except Exception as e:
                record.write({'sync_state': 'error', 'upload_progress': 0.0})
                log_model.log_operation(
                    config=record.drive_config_id,
                    file_name=record.name,
                    operation='upload',
                    state='fail',
                    error_message=str(e),
                    sync_type=sync_type,
                    file_type='file',
                    root_folder_name=root_name,
                    folder_path=folder_path,
                    duration=time.time() - t0,
                )
            return False

        # 3. Rename / Existing
        if record.google_file_id:
            try:
                success = sync.rename_file(record, record.name)
                if success:
                    record.write({
                        'sync_state': 'synced',
                        'last_synced': fields.Datetime.now(),
                    })
                    return True
                else:
                    record.write({'sync_state': 'error'})
            except Exception:
                record.write({'sync_state': 'error'})
            return False
            
        return False

    def sync_pending_to_drive(self, drive_config_id=None):
        """Unified entry point for background/bulk sync.
        Now uses the per-record helper for consistency.
        """
        drive_domain = [('drive_config_id', '=', drive_config_id)] if drive_config_id else []
        pending_ids = self.get_pending_sync_ids(drive_config_id=drive_config_id)
        pending_records = self.sudo().browse(pending_ids)
        
        success_count = 0
        error_count = 0
        
        for record in pending_records:
            if self._sync_single_record(record):
                success_count += 1
            else:
                error_count += 1
                
        return {'success': success_count, 'error': error_count}

    # ─── Sharing / Permissions ───

    def action_get_share_info(self):
        """Get sharing permissions info for a file. Called from the share dialog."""
        self.ensure_one()
        sync = self.env['google.drive.sync'].sudo()
        return sync.get_file_permissions(self)

    def action_add_permission(self, email, role='reader', send_notification=True):
        """Add a person to a file's sharing. Called from the share dialog."""
        self.ensure_one()
        sync = self.env['google.drive.sync'].sudo()
        return sync.create_permission(self, email, role=role, send_notification=send_notification)

    def action_update_permission(self, permission_id, role):
        """Update a person's role on a shared file."""
        self.ensure_one()
        sync = self.env['google.drive.sync'].sudo()
        return sync.update_permission(self, permission_id, role)

    def action_remove_permission(self, permission_id):
        """Remove a person's access from a shared file."""
        self.ensure_one()
        sync = self.env['google.drive.sync'].sudo()
        return sync.delete_permission(self, permission_id)

    def action_set_general_access(self, access_type, role='reader'):
        """Set general access for a file (anyone / restricted)."""
        self.ensure_one()
        sync = self.env['google.drive.sync'].sudo()
        return sync.set_general_access(self, access_type, role=role)

    def action_update_sharing_settings(self, writers_can_share=None, copy_requires_writer=None):
        """Update sharing settings for a file."""
        self.ensure_one()
        sync = self.env['google.drive.sync'].sudo()
        return sync.update_file_sharing_settings(
            self,
            writers_can_share=writers_can_share,
            copy_requires_writer=copy_requires_writer
        )

    # ─── Duplicate Detection and Pruning ───

    @api.model
    def get_duplicate_groups(self, config_id=None):
        """Find duplicate files using a production-grade strategy.
        Allows filtering by a specific Google Drive config.
        """
        domain = [('file_type', '=', 'file'), ('active', '=', True)]
        if config_id:
            domain.append(('drive_config_id', '=', config_id))
            
        files = self.search_read(
            domain, 
            ['id', 'name', 'md5_checksum', 'file_size', 'mime_type', 'create_date']
        )
        
        name_groups = {}
        for f in files:
            name = f['name']
            if name not in name_groups:
                name_groups[name] = []
            name_groups[name].append(f)
            
        result = []
        for name, file_list in name_groups.items():
            if len(file_list) > 1:
                content_groups = {}
                for f in file_list:
                    content_hash = f['md5_checksum'] if f['md5_checksum'] else f"{f['file_size']}_{f['mime_type']}"
                    if content_hash not in content_groups:
                        content_groups[content_hash] = []
                    content_groups[content_hash].append(f['id'])
                
                # Determine type
                if len(content_groups) == 1:
                    dup_type = 'exact'
                    group_name = f"Exact Match: {name}"
                else:
                    dup_type = 'conflict'
                    group_name = f"Name Conflict: {name}"
                    
                all_ids = [f['id'] for f in file_list]
                records = self.browse(all_ids).sorted(key=lambda r: r.create_date, reverse=True)
                
                result.append({
                    'name': name,
                    'type': dup_type,
                    'group_name': group_name,
                    'count': len(records),
                    'duplicate_ids': records.ids,
                    'records': records
                })
        return result

    @api.model
    def prune_duplicates(self, keep_newest=True):
        """Automatically keep the newest (or oldest) and delete the rest."""
        duplicate_groups = self.get_duplicate_groups()
        files_to_delete = self.env['google.drive.file']
        
        for group in duplicate_groups:
            records = group['records']
            if len(records) > 1:
                if keep_newest:
                    # Records are ordered by create_date desc, so [0] is newest
                    for rec in records[1:]:
                        files_to_delete |= rec
                else:
                    for rec in records[:-1]:
                        files_to_delete |= rec
                
        if files_to_delete:
            return self.delete_on_drive_and_unlink(files_to_delete.ids)
        return True
