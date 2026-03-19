# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
import json
import io
from datetime import datetime
import pytz
from odoo.service import db
from dateutil.relativedelta import relativedelta
import logging

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # ─── Cloud Provider Selector ────────────────────────────────────
    cloud_provider = fields.Selection([
        ('aws_s3', 'AWS S3'),
        ('gdrive', 'Google Drive'),
        ('dropbox', 'Dropbox'),
        ('onedrive', 'OneDrive'),
        ('nextcloud', 'Nextcloud'),
    ], string='Cloud Provider', config_parameter='auto_backup_db_cloud.provider')

    cloud_is_connected = fields.Boolean(
        string='Cloud Connected',
        compute='_compute_cloud_is_connected'
    )

    # ─── Legacy Fields (To prevent KeyError during upgrade) ──────────
    # These fields can be removed once the module is successfully upgraded in the UI.
    cloud_backup_active = fields.Boolean("Legacy Active")
    cloud_backup_interval_number = fields.Integer("Legacy Interval")
    cloud_backup_interval_type = fields.Selection([('minutes','min')], "Legacy Int Type")
    cloud_backup_last_run = fields.Datetime("Legacy Last Run")
    cloud_retention_type = fields.Selection([('none','None')], "Legacy Ret Type")
    cloud_retention_count = fields.Integer("Legacy Ret Count")
    cloud_retention_days = fields.Integer("Legacy Ret Days")
    cloud_sync_scope = fields.Selection([('all','All')], "Legacy Scope")
    cloud_selected_db_ids = fields.Many2many('cloud.backup.db', 'legacy_auto_rel', string="Legacy DBs")
    cloud_manual_sync_scope = fields.Selection([('all','All')], "Legacy Manual Scope")
    cloud_manual_selected_db_ids = fields.Many2many('cloud.backup.db', 'legacy_manual_rel', string="Legacy Manual DBs")

    # ═══════════════════════════════════════════════════════════════
    # set_values / get_values
    # ═══════════════════════════════════════════════════════════════

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        ICP = self.env['ir.config_parameter'].sudo()

        providers = ['s3', 'gdrive', 'dropbox', 'onedrive', 'nextcloud']
        for p in providers:
            # Save Auto DB IDs
            auto_field = f'{p}_selected_db_ids'
            if hasattr(self, auto_field):
                auto_db_ids = getattr(self, auto_field).ids
                ICP.set_param(f'auto_backup_db_cloud.{p}_selected_db_ids', json.dumps(auto_db_ids))

            # Save Manual DB IDs
            manual_field = f'{p}_manual_selected_db_ids'
            if hasattr(self, manual_field):
                manual_db_ids = getattr(self, manual_field).ids
                ICP.set_param(f'auto_backup_db_cloud.{p}_manual_selected_db_ids', json.dumps(manual_db_ids))

            # Sync with Cron Job (Optional: Handle per-provider cron if needed, 
            # but here we use a single cron that loops)
            pass

        # Enable/Disable Unified Cron Job
        cron = self.env.ref('auto_backup_db_cloud.ir_cron_auto_backup_db_cloud', raise_if_not_found=False)
        if cron:
            any_active = False
            smallest_interval_minutes = float('inf')

            for p in providers:
                active_param = f"auto_backup_db_cloud.{p}_backup_active"
                if ICP.get_param(active_param) == 'True':
                    any_active = True

                    # Calculate this provider's interval in minutes
                    raw_number = ICP.get_param(f'auto_backup_db_cloud.{p}_backup_interval_number', '1')
                    raw_type = ICP.get_param(f'auto_backup_db_cloud.{p}_backup_interval_type', 'days')
                    try:
                        number = int(raw_number) if raw_number else 1
                    except (ValueError, TypeError):
                        number = 1

                    # Convert to minutes for comparison
                    multiplier = {'minutes': 1, 'hours': 60, 'days': 1440, 'weeks': 10080, 'months': 43200}
                    interval_in_minutes = number * multiplier.get(raw_type, 1440)
                    if interval_in_minutes < smallest_interval_minutes:
                        smallest_interval_minutes = interval_in_minutes

            if cron.active != any_active:
                cron.active = any_active

            if any_active:
                # Set cron to run at the smallest provider interval
                # Use minutes for precision when interval is small
                if smallest_interval_minutes <= 60:
                    new_number = max(1, int(smallest_interval_minutes))
                    new_type = 'minutes'
                elif smallest_interval_minutes <= 1440:
                    new_number = max(1, int(smallest_interval_minutes // 60))
                    new_type = 'hours'
                else:
                    new_number = max(1, int(smallest_interval_minutes // 1440))
                    new_type = 'days'

                # Always reset nextcall so the cron picks up the new interval immediately
                cron.write({
                    'interval_number': new_number,
                    'interval_type': new_type,
                    'nextcall': fields.Datetime.now(),
                })

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env['ir.config_parameter'].sudo()

        providers = ['s3', 'gdrive', 'dropbox', 'onedrive', 'nextcloud']
        for p in providers:
            # Get Auto DB IDs
            auto_db_ids_str = ICP.get_param(f'auto_backup_db_cloud.{p}_selected_db_ids', '[]')
            try:
                auto_db_ids = json.loads(auto_db_ids_str)
            except Exception:
                auto_db_ids = []

            # Get Manual DB IDs
            manual_db_ids_str = ICP.get_param(f'auto_backup_db_cloud.{p}_manual_selected_db_ids', '[]')
            try:
                manual_db_ids = json.loads(manual_db_ids_str)
            except Exception:
                manual_db_ids = []

            # Filter out non-existent IDs
            if auto_db_ids:
                auto_db_ids = self.env['cloud.backup.db'].sudo().browse(auto_db_ids).exists().ids
            if manual_db_ids:
                manual_db_ids = self.env['cloud.backup.db'].sudo().browse(manual_db_ids).exists().ids

            res.update({
                f'{p}_selected_db_ids': [(6, 0, auto_db_ids)],
                f'{p}_manual_selected_db_ids': [(6, 0, manual_db_ids)],
            })

        res.update(
            s3_is_connection_tested=ICP.get_param('auto_backup_db_cloud.s3_is_connection_tested') == 'True',
            nextcloud_is_authorized=ICP.get_param('auto_backup_db_cloud.nextcloud_is_authorized') == 'True',
        )
        return res

    @api.depends('cloud_provider', 's3_is_connection_tested', 'gdrive_refresh_token',
                 'dropbox_refresh_token', 'onedrive_refresh_token', 'nextcloud_is_authorized')
    def _compute_cloud_is_connected(self):
        for rec in self:
            connected = False
            if rec.cloud_provider == 'aws_s3':
                connected = rec.s3_is_connection_tested
            elif rec.cloud_provider == 'gdrive':
                connected = bool(rec.gdrive_refresh_token)
            elif rec.cloud_provider == 'dropbox':
                connected = bool(rec.dropbox_refresh_token)
            elif rec.cloud_provider == 'onedrive':
                connected = bool(rec.onedrive_refresh_token)
            elif rec.cloud_provider == 'nextcloud':
                connected = rec.nextcloud_is_authorized
            rec.cloud_is_connected = connected

    # ═══════════════════════════════════════════════════════════════
    # Helpers: refresh DB list
    # ═══════════════════════════════════════════════════════════════

    def action_refresh_db_list(self):
        """Action for users to manually refresh the list of available databases."""
        try:
            available_dbs = db.list_dbs()
            existing_dbs = self.env['cloud.backup.db'].sudo().search([])
            existing_names = existing_dbs.mapped('name')

            for db_name in available_dbs:
                if db_name not in existing_names:
                    self.env['cloud.backup.db'].sudo().create({'name': db_name})
        except Exception as e:
            _logger.error("Failed to refresh DB list: %s", str(e))
        return True

    # ═══════════════════════════════════════════════════════════════
    # Dispatch: Manual Sync
    # ═══════════════════════════════════════════════════════════════

    def action_manual_sync(self, backup_type='Manual'):
        """Dispatch to the correct provider-specific sync method."""
        self.ensure_one()
        provider = self.cloud_provider

        if not provider:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': _('No cloud provider selected. Please select a provider first.'),
                    'type': 'danger',
                    'sticky': False,
                }
            }

        dispatch = {
            'aws_s3': self._s3_manual_sync,
            'gdrive': self._gdrive_manual_sync,
            'dropbox': self._dropbox_manual_sync,
            'onedrive': self._onedrive_manual_sync,
            'nextcloud': self._nextcloud_manual_sync,
        }

        method = dispatch.get(provider)
        if method:
            return method(backup_type=backup_type)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Error'),
                'message': _('Unknown cloud provider: %s') % provider,
                'type': 'danger',
                'sticky': False,
            }
        }

    @api.model
    def _cron_auto_backup(self):
        """Unified cron job that loops through all providers and checks their independent active flags and intervals."""
        providers = ['aws_s3', 'gdrive', 'dropbox', 'onedrive', 'nextcloud']
        ICP = self.env['ir.config_parameter'].sudo()
        now = fields.Datetime.now()
        
        for provider in providers:
            prefix = 's3' if provider == 'aws_s3' else provider
            active_field = f"auto_backup_db_cloud.{prefix}_backup_active"
            is_active = ICP.get_param(active_field) == 'True'
            
            if is_active:
                # Check interval
                interval_number = int(ICP.get_param(f'auto_backup_db_cloud.{prefix}_backup_interval_number', '1'))
                interval_type = ICP.get_param(f'auto_backup_db_cloud.{prefix}_backup_interval_type', 'days')
                last_run_str = ICP.get_param(f'auto_backup_db_cloud.{prefix}_backup_last_run')
                
                should_run = False
                if not last_run_str:
                    should_run = True
                else:
                    last_run = fields.Datetime.from_string(last_run_str)
                    delta_args = {interval_type: interval_number}
                    next_run = last_run + relativedelta(**delta_args)
                    if now >= next_run:
                        should_run = True
                
                if should_run:
                    _logger.info("Starting scheduled auto-backup for provider: %s", provider)
                    # Create a transient record with the specific provider to handle dispatch and settings
                    config = self.sudo().create({'cloud_provider': provider})
                    try:
                        result = config.action_manual_sync(backup_type='Auto')
                        if result and isinstance(result, dict):
                            msg = result.get('params', {}).get('message', '')
                            msg_type = result.get('params', {}).get('type', '')
                            if msg_type in ('danger', 'warning'):
                                _logger.warning("Auto backup for %s completed with issues: %s", provider, msg)
                            else:
                                _logger.info("Auto backup for %s completed successfully: %s", provider, msg)
                    except Exception as e:
                        _logger.error("Auto backup failed for %s: %s", provider, str(e))

    def _get_db_list(self, backup_type='Manual'):
        """Returns the list of database names to back up for the CURRENT provider."""
        ICP = self.env['ir.config_parameter'].sudo()
        p = self.cloud_provider
        if p == 'aws_s3': p = 's3'

        if backup_type == 'Auto':
            sync_scope = ICP.get_param(f'auto_backup_db_cloud.{p}_sync_scope') or 'all'
            db_ids_field = f'auto_backup_db_cloud.{p}_selected_db_ids'
        else:
            sync_scope = ICP.get_param(f'auto_backup_db_cloud.{p}_manual_sync_scope') or 'all'
            db_ids_field = f'auto_backup_db_cloud.{p}_manual_selected_db_ids'

        if sync_scope == 'selective':
            selected_db_ids_str = ICP.get_param(db_ids_field, '[]')
            selected_db_ids = json.loads(selected_db_ids_str)
            selected_dbs = self.env['cloud.backup.db'].browse(selected_db_ids)
            db_list = selected_dbs.mapped('name')
            if not db_list:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Warning'),
                        'message': _('No databases selected for sync.'),
                        'type': 'warning',
                        'sticky': False,
                    }
                }
        else:
            db_list = db.list_dbs()

        return db_list

    def _get_backup_timestamp(self):
        """Returns a formatted timestamp string for folder naming."""
        user_tz = self.env.user.tz or self.env.company.partner_id.tz or 'UTC'
        tz = pytz.timezone(user_tz)
        utc_now = fields.Datetime.now()
        local_dt = pytz.utc.localize(utc_now).astimezone(tz)
        return local_dt.strftime("%d-%m-%Y_%H-%M-%S")

    def _save_backup_last_run(self, last_run=None):
        """Persist the last run datetime to ICP for the current provider."""
        if not last_run:
            last_run = fields.Datetime.now()
        
        p = self.cloud_provider
        if p == 'aws_s3': p = 's3'
        
        field_name = f"{p}_backup_last_run"
        if hasattr(self, field_name):
            setattr(self, field_name, last_run)
            
        self.env['ir.config_parameter'].sudo().set_param(
            f'auto_backup_db_cloud.{field_name}', fields.Datetime.to_string(last_run)
        )

    def _build_summary_notification(self, provider_name, success_count, error_details):
        """Build a standard notification dict for backup results."""
        message = _('Successfully backed up %s databases to %s.') % (success_count, provider_name)
        if error_details:
            message += "\n" + "\n".join(error_details)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('%s Backup Summary') % provider_name,
                'message': message,
                'type': 'success' if not error_details else 'warning',
                'sticky': bool(error_details),
            }
        }
