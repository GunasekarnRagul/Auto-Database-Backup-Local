# -*- coding: utf-8 -*-
import os
import time
import logging
import tempfile
import shutil
import zipfile
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import config

_logger = logging.getLogger(__name__)


class AutoBackupConfig(models.Model):
    _name = 'auto.backup.config'
    _inherit = ['mail.thread']
    _description = 'Auto Database Backup Configuration'
    _rec_name = 'name'

    # ─── Basic Info ────────────────────────────────────────────────────────────
    name = fields.Char(
        string='Configuration Name',
        required=True,
        default='Odoo Database Backup',
    )
    active = fields.Boolean(string='Active', default=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('running', 'Running'),
        ('paused', 'Paused'),
    ], string='Status', default='running', required=True)

    # ─── Target Database ───────────────────────────────────────────────────────
    database_name = fields.Char(
        string='Database Name',
        required=True,
        default=lambda self: self.env.cr.dbname,
        help='The PostgreSQL database name to back up. Defaults to the current database.',
    )

    # ─── Storage Path ──────────────────────────────────────────────────────────
    backup_path = fields.Char(
        string='Backup Directory Path',
        required=True,
        default='/var/lib/odoo/backups',
        help=(
            'Absolute path on the server where backups will be stored.\n'
            'This can be a local folder (e.g. /var/odoo/backups) or any\n'
            'network-mounted drive path (NFS/SMB) (e.g. /mnt/nas/odoo-backups).'
        ),
    )

    # ─── Backup Format ─────────────────────────────────────────────────────────
    backup_format = fields.Selection([
        ('zip', 'ZIP (Database + Filestore)'),
        ('dump', 'pg_dump (.dump — Database Only)'),
        ('sql', 'pg_dump (.sql — Plain SQL)'),
    ], string='Backup Format', default='zip', required=True,
        help=(
            'ZIP: Full backup including the database dump and the Odoo filestore.\n'
            'dump: PostgreSQL custom-format dump (faster restore, smaller size).\n'
            'sql: Plain SQL text dump (human-readable, portable).'
        ),
    )

    # ─── Schedule ──────────────────────────────────────────────────────────────
    backup_frequency = fields.Selection([
        ('hourly', 'Every Hour'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
    ], string='Backup Frequency', default='daily', required=True)

    # ─── Retention ─────────────────────────────────────────────────────────────
    retention_enabled = fields.Boolean(
        string='Enable Retention Policy',
        default=True,
        help='If enabled, backups older than the specified number of days will be deleted automatically.',
    )
    retention_days = fields.Integer(
        string='Keep Backups For (Days)',
        default=30,
        help='Backups older than this many days will be deleted during the next scheduled run.',
    )

    # ─── Notifications ─────────────────────────────────────────────────────────
    notify_on_failure = fields.Boolean(
        string='Email Notification on Failure',
        default=True,
    )
    notify_email = fields.Char(
        string='Notification Email',
        help='Email address to receive failure alerts. Leave blank to use the admin email.',
    )

    # ─── Stats (computed) ──────────────────────────────────────────────────────
    history_ids = fields.One2many(
        'auto.backup.history', 'config_id', string='Backup History',
    )
    total_backups = fields.Integer(
        string='Total Backups', compute='_compute_stats', store=True,
    )
    last_backup_date = fields.Datetime(
        string='Last Backup', compute='_compute_stats', store=True,
    )
    last_backup_status = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed'),
    ], string='Last Status', compute='_compute_stats', store=True)
    disk_usage_mb = fields.Float(
        string='Disk Usage (MB)', compute='_compute_disk_usage', store=False,
    )

    # ──────────────────────────────────────────────────────────────────────────
    # Computed helpers
    # ──────────────────────────────────────────────────────────────────────────

    @api.depends('history_ids', 'history_ids.status', 'history_ids.backup_date')
    def _compute_stats(self):
        for rec in self:
            rec.total_backups = len(rec.history_ids)
            last = rec.history_ids.sorted('backup_date', reverse=True)[:1]
            if last:
                rec.last_backup_date = last.backup_date
                rec.last_backup_status = last.status
            else:
                rec.last_backup_date = False
                rec.last_backup_status = False

    @api.depends('backup_path')
    def _compute_disk_usage(self):
        for rec in self:
            total = 0.0
            path = rec.backup_path
            if path and os.path.isdir(path):
                for f in Path(path).glob('*'):
                    if f.is_file():
                        try:
                            total += f.stat().st_size
                        except OSError:
                            pass
            rec.disk_usage_mb = round(total / (1024 * 1024), 2)

    # ──────────────────────────────────────────────────────────────────────────
    # Validation
    # ──────────────────────────────────────────────────────────────────────────

    @api.constrains('retention_days')
    def _check_retention_days(self):
        for rec in self:
            if rec.retention_enabled and rec.retention_days < 1:
                raise ValidationError(_('Retention days must be at least 1.'))

    @api.constrains('backup_path')
    def _check_backup_path(self):
        for rec in self:
            if rec.backup_path and not os.path.isabs(rec.backup_path):
                raise ValidationError(_('Backup path must be an absolute path (e.g. /var/odoo/backups).'))

    # ──────────────────────────────────────────────────────────────────────────
    # Core Backup Logic
    # ──────────────────────────────────────────────────────────────────────────

    def _ensure_backup_directory(self):
        """Create backup directory if it does not exist."""
        path = self.backup_path
        if not path:
            raise UserError(_('Backup directory path is not configured.'))
        try:
            os.makedirs(path, exist_ok=True)
        except PermissionError as e:
            raise UserError(_(
                'Cannot create backup directory "%s": Permission denied.\n%s'
            ) % (path, str(e)))
        except OSError as e:
            raise UserError(_(
                'Cannot create backup directory "%s": %s'
            ) % (path, str(e)))
        return path

    def _generate_filename(self):
        """Build a timestamped filename for the backup."""
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        db = self.database_name or self.env.cr.dbname
        ext = {'zip': 'zip', 'dump': 'dump', 'sql': 'sql'}.get(self.backup_format, 'zip')
        return f'{db}_{ts}.{ext}'

    def _do_backup(self):
        """
        Perform the actual database backup and return a dict with result info.
        Returns: {'filename': str, 'filepath': str, 'size_mb': float, 'duration_seconds': float}
        """
        self.ensure_one()
        backup_dir = self._ensure_backup_directory()
        filename = self._generate_filename()
        filepath = os.path.join(backup_dir, filename)
        db_name = self.database_name or self.env.cr.dbname

        start_time = time.time()

        if self.backup_format == 'zip':
            self._backup_zip(db_name, filepath)
        elif self.backup_format == 'dump':
            self._backup_pgdump(db_name, filepath, fmt='custom')
        elif self.backup_format == 'sql':
            self._backup_pgdump(db_name, filepath, fmt='plain')

        duration = round(time.time() - start_time, 2)
        size_mb = round(os.path.getsize(filepath) / (1024 * 1024), 2) if os.path.exists(filepath) else 0.0

        return {
            'filename': filename,
            'filepath': filepath,
            'size_mb': size_mb,
            'duration_seconds': duration,
        }

    def _backup_zip(self, db_name, filepath):
        """
        Create a ZIP backup: database dump (pg_dump plain SQL) + filestore.
        This mirrors Odoo's own built-in backup mechanism.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            # 1) Dump database as plain SQL inside temp dir
            dump_path = os.path.join(tmp_dir, 'dump.sql')
            self._run_pgdump(db_name, dump_path, fmt='plain')

            # 2) Copy filestore into temp dir
            data_dir = config.get('data_dir', '/var/lib/odoo')
            filestore_src = os.path.join(data_dir, 'filestore', db_name)
            filestore_dst = os.path.join(tmp_dir, 'filestore')
            if os.path.isdir(filestore_src):
                shutil.copytree(filestore_src, filestore_dst)

            # 3) Zip everything
            with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for root, dirs, files in os.walk(tmp_dir):
                    for fname in files:
                        full_path = os.path.join(root, fname)
                        arcname = os.path.relpath(full_path, tmp_dir)
                        zf.write(full_path, arcname)

    def _backup_pgdump(self, db_name, filepath, fmt='custom'):
        """Run pg_dump and write output directly to filepath."""
        self._run_pgdump(db_name, filepath, fmt=fmt)

    def _run_pgdump(self, db_name, output_path, fmt='plain'):
        """Execute pg_dump via subprocess using Odoo's DB connection parameters."""
        db_host = config.get('db_host') or os.environ.get('PGHOST', 'localhost')
        db_port = str(config.get('db_port') or os.environ.get('PGPORT', '5432'))
        db_user = config.get('db_user') or os.environ.get('PGUSER', 'odoo')
        db_password = config.get('db_password') or os.environ.get('PGPASSWORD', '')

        fmt_flag = {'plain': 'plain', 'custom': 'custom'}.get(fmt, 'plain')

        cmd = [
            'pg_dump',
            '--host', db_host,
            '--port', db_port,
            '--username', db_user,
            '--no-password',
            '--format', fmt_flag,
            '--file', output_path,
            db_name,
        ]

        env = os.environ.copy()
        if db_password:
            env['PGPASSWORD'] = db_password

        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise UserError(_(
                'pg_dump failed for database "%s":\n%s'
            ) % (db_name, result.stderr or 'Unknown error'))

    # ──────────────────────────────────────────────────────────────────────────
    # Retention Cleanup
    # ──────────────────────────────────────────────────────────────────────────

    def _apply_retention(self):
        """Delete backup files older than retention_days from backup_path."""
        self.ensure_one()
        if not self.retention_enabled or not self.retention_days:
            return

        backup_dir = self.backup_path
        if not backup_dir or not os.path.isdir(backup_dir):
            return

        cutoff = datetime.now() - timedelta(days=self.retention_days)
        deleted_count = 0

        for f in Path(backup_dir).iterdir():
            if f.is_file():
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime)
                    if mtime < cutoff:
                        f.unlink()
                        deleted_count += 1
                        _logger.info('[AutoBackup] Deleted old backup: %s', f.name)
                except OSError as e:
                    _logger.warning('[AutoBackup] Could not delete %s: %s', f.name, e)

        if deleted_count:
            _logger.info('[AutoBackup] Retention: deleted %d old backup(s) from %s', deleted_count, backup_dir)

    # ──────────────────────────────────────────────────────────────────────────
    # Public Actions
    # ──────────────────────────────────────────────────────────────────────────

    def action_run_backup_now(self):
        """Trigger a manual backup immediately from the UI button."""
        self.ensure_one()
        if self.state == 'paused':
            raise UserError(_('Backup configuration is paused. Activate it first.'))

        history = self.env['auto.backup.history'].create({
            'config_id': self.id,
            'backup_date': fields.Datetime.now(),
            'database_name': self.database_name,
            'backup_format': self.backup_format,
            'backup_path': self.backup_path,
            'status': 'running',
            'notes': 'Manual backup triggered.',
        })

        try:
            result = self._do_backup()
            self._apply_retention()
            history.write({
                'status': 'success',
                'filename': result['filename'],
                'size_mb': result['size_mb'],
                'duration_seconds': result['duration_seconds'],
                'notes': 'Manual backup completed successfully.',
            })
            _logger.info('[AutoBackup] Manual backup OK → %s (%.2f MB)', result['filename'], result['size_mb'])
        except Exception as e:
            history.write({
                'status': 'failed',
                'notes': str(e),
            })
            _logger.error('[AutoBackup] Manual backup FAILED: %s', e)
            if self.notify_on_failure:
                self._send_failure_notification(str(e))
            raise UserError(_('Backup failed: %s') % str(e))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Backup Successful'),
                'message': _(
                    'Database backed up to: %s\nSize: %.2f MB'
                ) % (result['filepath'], result['size_mb']),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_toggle_pause(self):
        """Pause or resume the backup configuration."""
        for rec in self:
            rec.state = 'paused' if rec.state == 'running' else 'running'

    def action_view_history(self):
        """Open the backup history for this config."""
        self.ensure_one()
        return {
            'name': _('Backup History — %s') % self.name,
            'type': 'ir.actions.act_window',
            'res_model': 'auto.backup.history',
            'view_mode': 'list,form',
            'domain': [('config_id', '=', self.id)],
            'context': {'default_config_id': self.id},
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Scheduled Cron Entry Point
    # ──────────────────────────────────────────────────────────────────────────

    @api.model
    def cron_run_all_backups(self):
        """
        Called by the scheduled action (cron).
        Runs backup for every active, non-paused configuration.
        """
        configs = self.search([('active', '=', True), ('state', '=', 'running')])
        _logger.info('[AutoBackup] Cron started — %d active configuration(s) found.', len(configs))

        for cfg in configs:
            history = self.env['auto.backup.history'].create({
                'config_id': cfg.id,
                'backup_date': fields.Datetime.now(),
                'database_name': cfg.database_name,
                'backup_format': cfg.backup_format,
                'backup_path': cfg.backup_path,
                'status': 'running',
                'notes': 'Scheduled cron backup.',
            })
            try:
                result = cfg._do_backup()
                cfg._apply_retention()
                history.write({
                    'status': 'success',
                    'filename': result['filename'],
                    'size_mb': result['size_mb'],
                    'duration_seconds': result['duration_seconds'],
                    'notes': 'Scheduled backup completed successfully.',
                })
                _logger.info(
                    '[AutoBackup] [%s] Cron backup OK → %s (%.2f MB, %.2fs)',
                    cfg.name, result['filename'], result['size_mb'], result['duration_seconds'],
                )
            except Exception as e:
                history.write({'status': 'failed', 'notes': str(e)})
                _logger.error('[AutoBackup] [%s] Cron backup FAILED: %s', cfg.name, e)
                if cfg.notify_on_failure:
                    cfg._send_failure_notification(str(e))

    # ──────────────────────────────────────────────────────────────────────────
    # Notifications
    # ──────────────────────────────────────────────────────────────────────────

    def _send_failure_notification(self, error_message):
        """Post a failure chatter message and optionally send an email."""
        self.ensure_one()
        body = _(
            '<b>⚠️ Auto Database Backup Failed</b><br/>'
            '<b>Config:</b> %s<br/>'
            '<b>Database:</b> %s<br/>'
            '<b>Path:</b> %s<br/>'
            '<b>Error:</b> %s'
        ) % (self.name, self.database_name, self.backup_path, error_message)

        self.message_post(body=body, subject=_('Backup Failure Alert — %s') % self.name)

        email = self.notify_email or self.env.user.email
        if email:
            mail_vals = {
                'subject': _('⚠️ Odoo Backup Failed — %s') % self.database_name,
                'body_html': body,
                'email_to': email,
            }
            try:
                self.env['mail.mail'].create(mail_vals).send()
            except Exception as e:
                _logger.warning('[AutoBackup] Could not send failure email: %s', e)

    # ──────────────────────────────────────────────────────────────────────────
    # Path Test
    # ──────────────────────────────────────────────────────────────────────────

    def action_test_backup_path(self):
        """Verify that the backup path is writable."""
        self.ensure_one()
        path = self.backup_path
        if not path:
            raise UserError(_('Please configure a backup path first.'))

        try:
            os.makedirs(path, exist_ok=True)
            test_file = os.path.join(path, '.write_test')
            with open(test_file, 'w') as f:
                f.write('ok')
            os.remove(test_file)
        except PermissionError:
            raise UserError(_(
                'Path "%s" exists but is NOT writable. Check filesystem permissions.'
            ) % path)
        except OSError as e:
            raise UserError(_('Path test failed: %s') % str(e))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Path OK'),
                'message': _('Directory "%s" is accessible and writable.') % path,
                'type': 'success',
                'sticky': False,
            },
        }
