# -*- coding: utf-8 -*-
import os
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AutoBackupHistory(models.Model):
    _name = 'auto.backup.history'
    _description = 'Auto Database Backup History'
    _order = 'backup_date desc'
    _rec_name = 'filename'

    config_id = fields.Many2one(
        'auto.backup.config',
        string='Backup Configuration',
        ondelete='cascade',
        required=True,
        index=True,
    )

    backup_date = fields.Datetime(
        string='Backup Date/Time',
        required=True,
        default=fields.Datetime.now,
        index=True,
    )

    database_name = fields.Char(string='Database', required=True)
    backup_format = fields.Selection([
        ('zip', 'ZIP'),
        ('dump', 'pg_dump (.dump)'),
        ('sql', 'Plain SQL'),
    ], string='Format')

    backup_path = fields.Char(string='Backup Directory')
    filename = fields.Char(string='Filename')

    status = fields.Selection([
        ('running', 'Running'),
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], string='Status', default='running', index=True)

    size_mb = fields.Float(string='File Size (MB)', digits=(16, 2))
    duration_seconds = fields.Float(string='Duration (s)', digits=(16, 2))
    notes = fields.Text(string='Notes / Error Detail')

    # Full path (computed)
    full_path = fields.Char(
        string='Full File Path', compute='_compute_full_path', store=False,
    )
    file_exists = fields.Boolean(
        string='File Exists on Disk', compute='_compute_full_path', store=False,
    )

    @api.depends('backup_path', 'filename')
    def _compute_full_path(self):
        for rec in self:
            if rec.backup_path and rec.filename:
                fp = os.path.join(rec.backup_path, rec.filename)
                rec.full_path = fp
                rec.file_exists = os.path.isfile(fp)
            else:
                rec.full_path = False
                rec.file_exists = False

    def action_delete_backup_file(self):
        """Delete the physical backup file from disk."""
        self.ensure_one()
        if not self.file_exists:
            raise UserError(_('Backup file does not exist on disk.'))
        try:
            os.remove(self.full_path)
            _logger.info('[AutoBackup] Deleted backup file: %s', self.full_path)
        except OSError as e:
            raise UserError(_('Could not delete file: %s') % str(e))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('File Deleted'),
                'message': _('Backup file "%s" has been removed.') % self.filename,
                'type': 'warning',
                'sticky': False,
            },
        }

    def action_open_config(self):
        """Navigate to the parent backup configuration."""
        self.ensure_one()
        return {
            'name': _('Backup Configuration'),
            'type': 'ir.actions.act_window',
            'res_model': 'auto.backup.config',
            'res_id': self.config_id.id,
            'view_mode': 'form',
        }

    def action_download_backup(self):
        """Redirect the user to the download controller."""
        self.ensure_one()
        if not self.file_exists:
            raise UserError(_('Backup file does not exist on disk.'))
        return {
            'type': 'ir.actions.act_url',
            'url': '/auto_backup/download/%s' % self.id,
            'target': 'new',
        }
