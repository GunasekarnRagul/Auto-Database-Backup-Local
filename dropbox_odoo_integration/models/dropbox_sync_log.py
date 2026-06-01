# -*- coding: utf-8 -*-
import time
from odoo import models, fields, api


class GoogleDriveSyncLog(models.Model):
    _name = 'one.drive.sync.log'
    _description = 'OneDrive Sync Log'
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    display_name = fields.Char('Summary', compute='_compute_display_name', store=True)

    drive_config_id = fields.Many2one(
        'one.drive.config', string='Drive',
        ondelete='set null', index=True,
    )
    drive_name = fields.Char('Drive Name', related='drive_config_id.name', store=True, readonly=True)
    root_folder_name = fields.Char('Root Folder')
    folder_path = fields.Char('Folder Path', help='Full path to the parent folder')
    file_name = fields.Char('File / Folder Name')
    file_type = fields.Selection([
        ('file', 'File'),
        ('folder', 'Folder'),
    ], string='Item Type')

    sync_type = fields.Selection([
        ('manual', 'Manual Sync'),
        ('auto', 'Auto Sync'),
        ('upload', 'Upload'),
        ('cron', 'Scheduled (Cron)'),
        ('auto_setup', 'Historical Setup'),
    ], string='Sync Type', default='manual', index=True)

    operation = fields.Selection([
        ('upload', 'Upload'),
        ('download', 'Download / Pull'),
        ('rename', 'Rename'),
        ('delete', 'Delete'),
        ('trash', 'Trash'),
        ('move', 'Move'),
        ('sync', 'Sync'),
        ('create_folder', 'Create Folder'),
        ('share_add', 'Share — Add Person'),
        ('share_update', 'Share — Update Role'),
        ('share_remove', 'Share — Remove Person'),
        ('share_general', 'Share — General Access'),
        ('share_settings', 'Share — Settings'),
    ], string='Operation', index=True)

    state = fields.Selection([
        ('success', 'Success'),
        ('fail', 'Failed'),
    ], string='Status', default='success', index=True)

    error_message = fields.Text('Error Details')
    sync_details = fields.Text('Sync Details')
    one_drive_file_id = fields.Char('OneDrive File ID')
    file_size = fields.Float('File Size (bytes)')
    duration = fields.Float('Duration (s)', digits=(10, 3), help='Time taken for the operation in seconds')
    user_id = fields.Many2one('res.users', string='User', default=lambda self: self.env.user, index=True)
    user_name = fields.Char('User Name', help='Name of the user who performed the action')

    # Computed color field for terminal-style display
    state_color = fields.Char(compute='_compute_state_color')

    @api.depends('drive_name', 'root_folder_name', 'folder_path', 'file_name', 'operation')
    def _compute_display_name(self):
        for rec in self:
            parts = []
            if rec.drive_name:
                parts.append(rec.drive_name)
            if rec.root_folder_name:
                parts.append(rec.root_folder_name)
            # Include intermediate folder path segments so two logs for the same
            # file in different subfolders are visually distinguishable.
            if rec.folder_path:
                parts.append(rec.folder_path)
            if rec.file_name:
                parts.append(rec.file_name)
            rec.display_name = ' / '.join(parts) if parts else 'Sync Log'

    @api.depends('state')
    def _compute_state_color(self):
        for rec in self:
            rec.state_color = 'green' if rec.state == 'success' else 'red'

    @api.model
    def create_log(self, vals):
        """Convenience method to create a sync log entry.

        Args:
            vals (dict): Log field values. At minimum should contain:
                - drive_config_id
                - file_name
                - operation
                - state
        """
        try:
            return self.sudo().create(vals)
        except Exception:
            # Never let logging break the actual sync
            return self.env['one.drive.sync.log']

    @api.model
    def log_operation(self, config, file_name, operation, state='success',
                      error_message=False, sync_details=False, sync_type=False, file_type='file',
                      root_folder_name=False, folder_path=False,
                      one_drive_file_id=False, file_size=0, duration=0,
                      user_id=False):
        """High-level helper to log a sync operation.

        Determines sync_type from context if not explicitly supplied.
        """
        if not sync_type:
            sync_type = self.env.context.get('sync_type', 'manual')

        active_uid = user_id or self.env.context.get('active_uid') or self.env.user.id
        active_user = self.env['res.users'].sudo().browse(active_uid)
        u_name = active_user.name if active_user.exists() else 'System'

        vals = {
            'drive_config_id': config.id if config else False,
            'file_name': file_name,
            'file_type': file_type,
            'operation': operation,
            'state': state,
            'error_message': error_message,
            'sync_details': sync_details,
            'sync_type': sync_type,
            'root_folder_name': root_folder_name,
            'folder_path': folder_path,
            'one_drive_file_id': one_drive_file_id,
            'file_size': file_size,
            'duration': duration,
            'user_id': active_uid,
            'user_name': u_name,
        }
        return self.create_log(vals)
