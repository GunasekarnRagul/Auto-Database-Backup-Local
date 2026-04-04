# -*- coding: utf-8 -*-
from odoo import models, fields, api
import base64


class GoogleDriveFile(models.Model):
    _name = 'google.drive.file'
    _description = 'Google Drive File'
    _order = 'file_type desc, name asc'

    name = fields.Char('File Name', required=True)
    drive_config_id = fields.Many2one('google.drive.config', string='Drive', required=True, ondelete='cascade')
    root_folder_id = fields.Many2one('google.drive.root.folder', string='Root Folder', ondelete='cascade')
    file_type = fields.Selection([
        ('file', 'File'),
        ('folder', 'Folder'),
    ], string='Type', default='file', required=True)
    mime_type = fields.Char('MIME Type')
    file_size = fields.Float('Size (KB)')
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
        ('error', 'Error'),
        ('pending_delete', 'Pending Delete'),
    ], string='Sync Status', default='pending')
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

    def action_archive_recursive(self):
        """Archive records and their children, marking them for background Drive deletion."""
        for record in self:
            record.child_ids.action_archive_recursive()
            record.write({
                'active': False,
                'sync_state': 'pending_delete',
            })
        return True

    def action_unarchive(self):
        """Restore archived records and their children."""
        for record in self.with_context(active_test=False):
            record.write({
                'active': True,
                'sync_state': 'synced'
            })
            record.child_ids.action_unarchive()
        return True

    @api.model
    def delete_on_drive_and_unlink(self, record_ids):
        """Delete records from Google Drive and then unlink them from Odoo.
        Called from JS after records are archived.
        """
        records = self.browse(record_ids).with_context(active_test=False)
        if not records:
            return True
        
        # We need the drive config - assume they all share one or use the first
        config = records[0].drive_config_id
        sync = self.env['google.drive.sync'].sudo()
        access_token = sync._get_access_token(config)
        
        if not access_token:
            return False
            
        import requests as http_requests
        headers = {"Authorization": f"Bearer {access_token}"}
        
        success_ids = []
        for record in records:
            if not record.google_file_id:
                success_ids.append(record.id)
                continue
            
            try:
                url = f"https://www.googleapis.com/drive/v3/files/{record.google_file_id}"
                response = http_requests.delete(url, headers=headers)
                # If deleted or not found (404), it's a success for us
                if response.status_code in [200, 204, 404]:
                    success_ids.append(record.id)
            except Exception:
                # If network fails, we'll try again during next sync
                pass
                
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

    def write(self, vals):
        # We handle Drive rename asynchronously from JS to keep UI instant
        return super(GoogleDriveFile, self).write(vals)

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
    def rename_on_drive_by_id(self, record_id, new_name):
        """Rename a file/folder on Google Drive. 
        Called asynchronously from JS after Odoo UI updates instantly.
        """
        record = self.browse(record_id)
        if not record.exists() or not record.google_file_id:
            return False
            
        success = self.env['google.drive.sync'].sudo().rename_file(record, new_name)
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
        })

        # Link attachment to file explorer record properly
        attachment.write({'res_id': explorer_record.id})

        return explorer_record.id

    def sync_pending_to_drive(self):
        """Push all pending folders and files to Google Drive.
        Called during the sync process. Folders are synced first (depth-first)
        so that child items can be placed in the correct parent.
        """
        sync = self.env['google.drive.sync'].sudo()

        # 1. Sync pending folders (parents first, then children)
        pending_folders = self.sudo().search([
            ('sync_state', '=', 'pending'),
            ('file_type', '=', 'folder'),
            ('google_file_id', '=', False),
        ], order='id asc')

        for folder in pending_folders:
            parent_gdrive_id = folder._resolve_parent_gdrive_id(
                folder.parent_folder_id.id if folder.parent_folder_id else False,
                folder.root_folder_id.id if folder.root_folder_id else False,
            )
            try:
                result = sync.create_folder_in_drive(
                    folder.name, folder.drive_config_id, parent_gdrive_id=parent_gdrive_id
                )
                if result:
                    folder.write({
                        'google_file_id': result['google_file_id'],
                        'google_url': result['google_url'],
                        'sync_state': 'synced',
                        'last_synced': fields.Datetime.now(),
                    })
            except Exception:
                folder.write({'sync_state': 'error'})

        # 2. Sync pending files
        pending_files = self.sudo().search([
            ('sync_state', '=', 'pending'),
            ('file_type', '=', 'file'),
            ('google_file_id', '=', False),
        ], order='id asc')

        for file_rec in pending_files:
            parent_gdrive_id = file_rec._resolve_parent_gdrive_id(
                file_rec.parent_folder_id.id if file_rec.parent_folder_id else False,
                file_rec.root_folder_id.id if file_rec.root_folder_id else False,
            )
            # Find the corresponding ir.attachment using strong res_id matching
            attachment = self.env['ir.attachment'].sudo().search([
                ('res_model', '=', 'google.drive.file'),
                ('res_id', '=', file_rec.id),
                ('google_file_id', '=', False),
            ], limit=1)

            # Fallback for old records or unexpectedly created attachments
            if not attachment:
                attachment = self.env['ir.attachment'].sudo().search([
                    ('name', '=', file_rec.name),
                    ('google_file_id', '=', False),
                ], limit=1)

            if not attachment or not attachment.raw:
                continue

            try:
                result = sync.upload_file_to_drive(
                    file_rec.name, attachment.raw,
                    file_rec.mime_type or 'application/octet-stream',
                    file_rec.drive_config_id, parent_gdrive_id=parent_gdrive_id
                )
                if result:
                    file_rec.write({
                        'google_file_id': result['google_file_id'],
                        'google_url': result['google_url'],
                        'sync_state': 'synced',
                        'last_synced': fields.Datetime.now(),
                    })
                    attachment.with_context(skip_gdrive_sync=True).write({
                        'google_file_id': result['google_file_id'],
                    })
            except Exception:
                file_rec.write({'sync_state': 'error'})

        # 3. Sync pending renames (where google_file_id already exists)
        pending_renames = self.sudo().search([
            ('sync_state', '=', 'pending'),
            ('google_file_id', '!=', False),
        ])
        for record in pending_renames:
            try:
                success = sync.rename_file(record, record.name)
                if success:
                    record.write({
                        'sync_state': 'synced',
                        'last_synced': fields.Datetime.now(),
                    })
            except Exception:
                record.write({'sync_state': 'error'})

        # 4. Sync pending deletions
        pending_deletions = self.sudo().with_context(active_test=False).search([
            ('sync_state', '=', 'pending_delete'),
            ('active', '=', False),
        ])
        if pending_deletions:
            # Group by config to avoid repeated token fetches
            configs = pending_deletions.mapped('drive_config_id')
            for config in configs:
                config_deletions = pending_deletions.filtered(lambda r: r.drive_config_id == config)
                self.delete_on_drive_and_unlink(config_deletions.ids)
