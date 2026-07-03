# -*- coding: utf-8 -*-
import time
from odoo import models, fields, api


class CloudSyncLog(models.Model):
    _name = 'cloud.sync.log'
    _description = 'Cloud Storage Sync Log'
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    display_name = fields.Char('Summary', compute='_compute_display_name', store=True)

    drive_config_id = fields.Many2one('cloud.provider.config', string='Provider', ondelete='set null', index=True)
    drive_name      = fields.Char('Provider Name', related='drive_config_id.name', store=True, readonly=True)
    provider_type   = fields.Selection(string='Provider Type', related='drive_config_id.provider_type', store=True, readonly=True)

    root_folder_name = fields.Char('Root Folder')
    folder_path      = fields.Char('Folder Path')
    file_name        = fields.Char('File / Folder Name')
    file_type = fields.Selection([
        ('file', 'File'), ('folder', 'Folder'),
    ], string='Item Type')

    sync_type = fields.Selection([
        ('manual',     'Manual Sync'),
        ('auto',       'Auto Sync'),
        ('upload',     'Upload'),
        ('cron',       'Scheduled (Cron)'),
        ('auto_setup', 'Historical Setup'),
    ], string='Sync Type', default='manual', index=True)

    operation = fields.Selection([
        ('upload',        'Upload'),
        ('download',      'Download / Pull'),
        ('rename',        'Rename'),
        ('delete',        'Delete'),
        ('trash',         'Trash'),
        ('move',          'Move'),
        ('sync',          'Sync'),
        ('create_folder', 'Create Folder'),
        ('share_add',     'Share — Add Person'),
        ('share_update',  'Share — Update Role'),
        ('share_remove',  'Share — Remove Person'),
        ('share_general', 'Share — General Access'),
        ('share_settings','Share — Settings'),
    ], string='Operation', index=True)

    state = fields.Selection([
        ('success', 'Success'),
        ('fail',    'Failed'),
    ], string='Status', default='success', index=True)

    error_message   = fields.Text('Error Details')
    sync_details    = fields.Text('Sync Details')
    cloud_file_id   = fields.Char('Cloud File / Object ID')
    file_size       = fields.Float('File Size (bytes)')
    duration        = fields.Float('Duration (s)', digits=(10, 3))
    user_id         = fields.Many2one('res.users', string='User', default=lambda self: self.env.user, index=True)
    user_name       = fields.Char('User Name')
    state_color     = fields.Char(compute='_compute_state_color')

    @api.depends('drive_name', 'root_folder_name', 'folder_path', 'file_name', 'operation')
    def _compute_display_name(self):
        for rec in self:
            parts = filter(None, [rec.drive_name, rec.root_folder_name, rec.folder_path, rec.file_name])
            rec.display_name = ' / '.join(parts) or 'Sync Log'

    @api.depends('state')
    def _compute_state_color(self):
        for rec in self:
            rec.state_color = 'green' if rec.state == 'success' else 'red'

    @api.model
    def log_operation(self, config, file_name, operation, state='success',
                      error_message=False, sync_details=False, sync_type=False,
                      file_type='file', root_folder_name=False, folder_path=False,
                      cloud_file_id=False, file_size=0, duration=0, user_id=False):
        """High-level helper to create a sync log entry."""
        if not sync_type:
            sync_type = self.env.context.get('sync_type', 'manual')
        active_uid = user_id or self.env.context.get('active_uid') or self.env.user.id
        user = self.env['res.users'].sudo().browse(active_uid)
        u_name = user.name if user.exists() else 'System'
        try:
            return self.sudo().create({
                'drive_config_id': config.id if config else False,
                'file_name':        file_name,
                'file_type':        file_type,
                'operation':        operation,
                'state':            state,
                'error_message':    error_message,
                'sync_details':     sync_details,
                'sync_type':        sync_type,
                'root_folder_name': root_folder_name,
                'folder_path':      folder_path,
                'cloud_file_id':    cloud_file_id,
                'file_size':        file_size,
                'duration':         duration,
                'user_id':          active_uid,
                'user_name':        u_name,
            })
        except Exception:
            return self.env['cloud.sync.log']
