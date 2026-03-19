# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
import requests
import json
import io
import logging
from datetime import datetime
import xml.etree.ElementTree as ET

_logger = logging.getLogger(__name__)


class ResConfigSettingsNextcloud(models.TransientModel):
    _inherit = 'res.config.settings'

    # ─── Nextcloud Credentials ──────────────────────────────────────
    nextcloud_url = fields.Char(
        string='Nextcloud URL',
        config_parameter='auto_backup_db_cloud.nextcloud_url'
    )
    nextcloud_username = fields.Char(
        string='Nextcloud Username',
        config_parameter='auto_backup_db_cloud.nextcloud_username'
    )
    nextcloud_password = fields.Char(
        string='App Password',
        config_parameter='auto_backup_db_cloud.nextcloud_password'
    )
    nextcloud_folder_path = fields.Char(
        string='Nextcloud Folder Path',
        config_parameter='auto_backup_db_cloud.nextcloud_folder_path',
        default='/Odoo_Backups'
    )
    nextcloud_is_authorized = fields.Boolean(
        string='Nextcloud Authorized',
        config_parameter='auto_backup_db_cloud.nextcloud_is_authorized',
        readonly=True
    )

    # ─── Nextcloud Backup Settings ──────────────────────────────────
    nextcloud_backup_active = fields.Boolean(
        string='Enable Nextcloud Auto Backup',
        config_parameter='auto_backup_db_cloud.nextcloud_backup_active'
    )
    nextcloud_backup_interval_number = fields.Integer(
        string='Backup Interval',
        config_parameter='auto_backup_db_cloud.nextcloud_backup_interval_number',
        default=1
    )
    nextcloud_backup_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], string='Backup Interval Unit',
        config_parameter='auto_backup_db_cloud.nextcloud_backup_interval_type',
        default='days')

    nextcloud_backup_last_run = fields.Datetime(
        string='Last Nextcloud Backup Run',
        config_parameter='auto_backup_db_cloud.nextcloud_backup_last_run',
        readonly=True
    )

    nextcloud_retention_type = fields.Selection([
        ('none', 'Keep All'),
        ('count', 'Keep Last X Backups'),
        ('days', 'Keep for X Days'),
    ], string='Retention Policy',
        config_parameter='auto_backup_db_cloud.nextcloud_retention_type',
        default='none')

    nextcloud_retention_count = fields.Integer(
        string='Number of Backups to Keep',
        config_parameter='auto_backup_db_cloud.nextcloud_retention_count',
        default=10
    )

    nextcloud_retention_days = fields.Integer(
        string='Number of Days to Keep',
        config_parameter='auto_backup_db_cloud.nextcloud_retention_days',
        default=30
    )

    nextcloud_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Sync Scope',
        config_parameter='auto_backup_db_cloud.nextcloud_sync_scope',
        default='all', required=True)

    nextcloud_selected_db_ids = fields.Many2many(
        'cloud.backup.db',
        'cloud_nextcloud_auto_db_rel',
        string='Select Databases'
    )

    nextcloud_manual_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Manual Sync Scope',
        config_parameter='auto_backup_db_cloud.nextcloud_manual_sync_scope',
        default='all', required=True)

    nextcloud_manual_selected_db_ids = fields.Many2many(
        'cloud.backup.db',
        'cloud_nextcloud_manual_db_rel',
        string='Manual Select Databases'
    )

    # ═══════════════════════════════════════════════════════════════
    # Auth
    # ═══════════════════════════════════════════════════════════════

    def action_nextcloud_authenticate(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        url = self.nextcloud_url
        user = self.nextcloud_username
        pwd = self.nextcloud_password

        if not (url and user and pwd):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Incomplete Data'),
                    'message': _('Please fill in URL, Username and App Password.'),
                    'type': 'danger',
                }
            }

        webdav_url = f"{url.rstrip('/')}/remote.php/dav/files/{user}/"
        try:
            response = requests.request("PROPFIND", webdav_url, auth=(user, pwd), timeout=10)
            if response.status_code in [200, 207]:
                # Save credentials AND authorization status to ICP
                ICP.set_param('auto_backup_db_cloud.nextcloud_url', url)
                ICP.set_param('auto_backup_db_cloud.nextcloud_username', user)
                ICP.set_param('auto_backup_db_cloud.nextcloud_password', pwd)
                ICP.set_param('auto_backup_db_cloud.nextcloud_is_authorized', 'True')
                self.nextcloud_is_authorized = True
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Connected Successfully'),
                        'message': _('Nextcloud connection established. Your credentials have been saved.'),
                        'type': 'success',
                        'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                    }
                }
            else:
                ICP.set_param('auto_backup_db_cloud.nextcloud_is_authorized', 'False')
                self.nextcloud_is_authorized = False
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Connection Failed'),
                        'message': _('Status Code: %s. Please check your credentials.') % response.status_code,
                        'type': 'danger',
                    }
                }
        except Exception as e:
            ICP.set_param('auto_backup_db_cloud.nextcloud_is_authorized', 'False')
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Error'),
                    'message': str(e),
                    'type': 'danger',
                }
            }

    def action_nextcloud_disconnect(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        params = [
            'auto_backup_db_cloud.nextcloud_url',
            'auto_backup_db_cloud.nextcloud_username',
            'auto_backup_db_cloud.nextcloud_password',
            'auto_backup_db_cloud.nextcloud_is_authorized',
            'auto_backup_db_cloud.nextcloud_backup_active',
        ]
        for p in params:
            ICP.set_param(p, False)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    # ═══════════════════════════════════════════════════════════════
    # Nextcloud Backup Logic
    # ═══════════════════════════════════════════════════════════════

    def _get_webdav_auth(self):
        ICP = self.env['ir.config_parameter'].sudo()
        url = ICP.get_param('auto_backup_db_cloud.nextcloud_url')
        user = ICP.get_param('auto_backup_db_cloud.nextcloud_username')
        pwd = ICP.get_param('auto_backup_db_cloud.nextcloud_password')
        if not (url and user and pwd):
            return None, None, None
        return url, user, pwd

    def _create_nextcloud_folder(self, base_url, user, pwd, path):
        parts = path.strip('/').split('/')
        current_path = ""
        for part in parts:
            current_path += "/" + part
            folder_url = f"{base_url.rstrip('/')}/remote.php/dav/files/{user}{current_path}"
            requests.request("MKCOL", folder_url, auth=(user, pwd))
        return True

    def _upload_to_nextcloud(self, base_url, user, pwd, folder_path, file_name, content):
        target_url = f"{base_url.rstrip('/')}/remote.php/dav/files/{user}/{folder_path.strip('/')}/{file_name}"
        try:
            self._create_nextcloud_folder(base_url, user, pwd, folder_path)
            response = requests.put(target_url, auth=(user, pwd), data=content, timeout=600)
            return response.status_code in [200, 201, 204]
        except Exception as e:
            _logger.error("Nextcloud Upload Error: %s", str(e))
            return False

    def _nextcloud_manual_sync(self, backup_type='Manual'):
        self.ensure_one()
        from odoo.service import db

        url, user, pwd = self._get_webdav_auth()
        if not (url and user and pwd):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': _('Nextcloud not configured or authorized.'),
                    'type': 'danger',
                }
            }

        timestamp = self._get_backup_timestamp()

        ICP = self.env['ir.config_parameter'].sudo()
        root_path = ICP.get_param('auto_backup_db_cloud.nextcloud_folder_path') or '/Odoo_Backups'
        backup_folder = f"{backup_type}_{timestamp}"

        try:
            db_list = self._get_db_list(backup_type)
            if isinstance(db_list, dict):
                return db_list

            success_count = 0
            error_details = []

            for db_name in db_list:
                try:
                    sql_stream = io.BytesIO()
                    db.dump_db(db_name, sql_stream, backup_format='sql')
                    sql_stream.seek(0)

                    dest_folder = f"{root_path}/{backup_folder}/{db_name}"
                    sql_uploaded = self._upload_to_nextcloud(url, user, pwd, dest_folder, f"{db_name}.sql", sql_stream.read())

                    zip_stream = io.BytesIO()
                    db.dump_db(db_name, zip_stream, backup_format='zip')
                    zip_stream.seek(0)

                    zip_uploaded = self._upload_to_nextcloud(url, user, pwd, dest_folder, f"{db_name}.zip", zip_stream.read())

                    if sql_uploaded and zip_uploaded:
                        success_count += 1
                    else:
                        error_msg = f"Upload failed for {db_name}: "
                        if not sql_uploaded:
                            error_msg += " SQL failed."
                        if not zip_uploaded:
                            error_msg += " ZIP failed."
                        error_details.append(error_msg)
                except Exception as e:
                    _logger.exception("Backup failed for %s", db_name)
                    error_details.append(f"Backup failed for {db_name}: {str(e)}")

            self._apply_nextcloud_retention_policy(url, user, pwd)
            self._save_backup_last_run()
            return self._build_summary_notification('Nextcloud', success_count, error_details)

        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': str(e),
                    'type': 'danger',
                }
            }

    def _apply_nextcloud_retention_policy(self, url, user, pwd):
        ICP = self.env['ir.config_parameter'].sudo()
        retention_type = ICP.get_param('auto_backup_db_cloud.nextcloud_retention_type')
        if not retention_type or retention_type == 'none' or retention_type == 'False':
            return

        root_path = ICP.get_param('auto_backup_db_cloud.nextcloud_folder_path') or '/Odoo_Backups'
        webdav_url = f"{url.rstrip('/')}/remote.php/dav/files/{user}/{root_path.strip('/')}/"

        try:
            headers = {'Depth': '1'}
            response = requests.request("PROPFIND", webdav_url, auth=(user, pwd), headers=headers)
            if response.status_code not in [200, 207]:
                return

            tree = ET.fromstring(response.content)
            namespaces = {'d': 'DAV:'}
            backups = []

            for resp in tree.findall('d:response', namespaces)[1:]:
                href = resp.find('d:href', namespaces).text
                name = href.rstrip('/').split('/')[-1]
                if name.startswith(('Auto_', 'Manual_')):
                    try:
                        ts_str = name.split('_', 1)[1]
                        dt = datetime.strptime(ts_str, "%d-%m-%Y_%H-%M-%S")
                        backups.append({'href': href, 'time': dt})
                    except Exception:
                        continue

            backups.sort(key=lambda x: x['time'])

            to_delete = []
            if retention_type == 'count':
                try:
                    raw_count = ICP.get_param('auto_backup_db_cloud.nextcloud_retention_count')
                    count = int(raw_count) if raw_count and raw_count != 'False' else 10
                except (ValueError, TypeError):
                    count = 10

                if len(backups) > count:
                    to_delete = backups[:len(backups) - count]
            elif retention_type == 'days':
                try:
                    raw_days = ICP.get_param('auto_backup_db_cloud.nextcloud_retention_days')
                    days = int(raw_days) if raw_days and raw_days != 'False' else 30
                except (ValueError, TypeError):
                    days = 30

                from datetime import timedelta
                limit = datetime.now() - timedelta(days=days)
                to_delete = [b for b in backups if b['time'] < limit]

            for b in to_delete:
                del_url = f"{url.rstrip('/')}{b['href']}"
                requests.delete(del_url, auth=(user, pwd))
                _logger.info("Deleted Nextcloud backup: %s", b['href'])
        except Exception as e:
            _logger.error("Retention policy error: %s", str(e))
