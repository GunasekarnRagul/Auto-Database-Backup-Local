# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.http import request
import requests
import json
import io
import logging

_logger = logging.getLogger(__name__)


class ResConfigSettingsGDrive(models.TransientModel):
    _inherit = 'res.config.settings'

    # ─── Google Drive Credentials ───────────────────────────────────
    gdrive_client_id = fields.Char(
        string='Client ID',
        config_parameter='auto_backup_db_cloud.gdrive_client_id'
    )
    gdrive_client_secret = fields.Char(
        string='Client Secret',
        config_parameter='auto_backup_db_cloud.gdrive_client_secret'
    )
    gdrive_folder_id = fields.Char(
        string='G Drive Folder ID',
        config_parameter='auto_backup_db_cloud.gdrive_folder_id'
    )
    gdrive_refresh_token = fields.Char(
        string='Refresh Token',
        config_parameter='auto_backup_db_cloud.gdrive_refresh_token',
        readonly=True
    )
    gdrive_redirect_uri = fields.Char(
        string='Redirect URI',
        compute='_compute_gdrive_redirect_uri'
    )

    # ─── Google Drive Backup Settings ───────────────────────────────
    gdrive_backup_active = fields.Boolean(
        string='Enable GDrive Auto Backup',
        config_parameter='auto_backup_db_cloud.gdrive_backup_active'
    )
    gdrive_backup_interval_number = fields.Integer(
        string='Backup Interval',
        config_parameter='auto_backup_db_cloud.gdrive_backup_interval_number',
        default=1
    )
    gdrive_backup_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], string='Backup Interval Unit',
        config_parameter='auto_backup_db_cloud.gdrive_backup_interval_type',
        default='days')

    gdrive_backup_last_run = fields.Datetime(
        string='Last GDrive Backup Run',
        config_parameter='auto_backup_db_cloud.gdrive_backup_last_run',
        readonly=True
    )

    gdrive_retention_type = fields.Selection([
        ('none', 'Keep All'),
        ('count', 'Keep Last X Backups'),
        ('days', 'Keep for X Days'),
    ], string='Retention Policy',
        config_parameter='auto_backup_db_cloud.gdrive_retention_type',
        default='none')

    gdrive_retention_count = fields.Integer(
        string='Number of Backups to Keep',
        config_parameter='auto_backup_db_cloud.gdrive_retention_count',
        default=10
    )

    gdrive_retention_days = fields.Integer(
        string='Number of Days to Keep',
        config_parameter='auto_backup_db_cloud.gdrive_retention_days',
        default=30
    )

    gdrive_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Sync Scope',
        config_parameter='auto_backup_db_cloud.gdrive_sync_scope',
        default='all', required=True)

    gdrive_selected_db_ids = fields.Many2many(
        'cloud.backup.db',
        'cloud_gdrive_auto_db_rel',
        string='Select Databases'
    )

    gdrive_manual_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Manual Sync Scope',
        config_parameter='auto_backup_db_cloud.gdrive_manual_sync_scope',
        default='all', required=True)

    gdrive_manual_selected_db_ids = fields.Many2many(
        'cloud.backup.db',
        'cloud_gdrive_manual_db_rel',
        string='Manual Select Databases'
    )

    # ═══════════════════════════════════════════════════════════════
    # Compute / Auth
    # ═══════════════════════════════════════════════════════════════

    @api.depends('gdrive_client_id')
    def _compute_gdrive_redirect_uri(self):
        base_url_param = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for config in self:
            host_url = base_url_param or ''
            try:
                if request and request.httprequest:
                    req_root = request.httprequest.url_root.rstrip('/')
                    if req_root:
                        host_url = req_root
            except Exception:
                pass
            config.gdrive_redirect_uri = f"{host_url}/google_account/authentication"

    def action_gdrive_authenticate(self):
        self.ensure_one()
        if not self.gdrive_client_id:
            return False

        params = {
            'client_id': self.gdrive_client_id,
            'redirect_uri': self.gdrive_redirect_uri,
            'response_type': 'code',
            'scope': 'https://www.googleapis.com/auth/drive.file',
            'access_type': 'offline',
            'prompt': 'consent',
            'state': 'cloud_gdrive_auth_callback',
        }
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + "&".join([f"{k}={v}" for k, v in params.items()])
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def action_gdrive_disconnect(self):
        self.ensure_one()
        self.gdrive_client_id = False
        self.gdrive_client_secret = False
        self.gdrive_folder_id = False
        self.gdrive_refresh_token = False
        self.gdrive_backup_active = False

        ICP = self.env['ir.config_parameter'].sudo()
        params_to_clear = [
            'auto_backup_db_cloud.gdrive_client_id',
            'auto_backup_db_cloud.gdrive_client_secret',
            'auto_backup_db_cloud.gdrive_folder_id',
            'auto_backup_db_cloud.gdrive_refresh_token',
            'auto_backup_db_cloud.gdrive_backup_active',
        ]
        for param in params_to_clear:
            ICP.set_param(param, False)

        return {'type': 'ir.actions.client', 'tag': 'reload'}

    # ═══════════════════════════════════════════════════════════════
    # GDrive Backup Logic
    # ═══════════════════════════════════════════════════════════════

    def _get_gdrive_access_token(self):
        ICP = self.env['ir.config_parameter'].sudo()
        client_id = ICP.get_param('auto_backup_db_cloud.gdrive_client_id')
        client_secret = ICP.get_param('auto_backup_db_cloud.gdrive_client_secret')
        refresh_token = ICP.get_param('auto_backup_db_cloud.gdrive_refresh_token')

        if not (client_id and client_secret and refresh_token):
            return False

        data = {
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
        }
        response = requests.post("https://oauth2.googleapis.com/token", data=data)
        if response.status_code == 200:
            return response.json().get('access_token')
        return False

    def _create_gdrive_folder(self, access_token, folder_name, parent_id=None):
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder"
        }
        if parent_id:
            metadata["parents"] = [parent_id]

        response = requests.post(
            "https://www.googleapis.com/drive/v3/files?fields=id,createdTime",
            headers=headers,
            data=json.dumps(metadata)
        )
        if response.status_code == 200:
            return response.json()
        return False

    def _upload_to_gdrive(self, access_token, file_content, file_name, mime_type, parent_id=None):
        metadata = {"name": file_name}
        if parent_id:
            metadata["parents"] = [parent_id]

        files = {
            'data': ('metadata', json.dumps(metadata), 'application/json'),
            'file': (file_name, file_content, mime_type)
        }
        headers = {"Authorization": f"Bearer {access_token}"}

        response = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            headers=headers,
            files=files
        )
        return response.status_code == 200

    def _gdrive_manual_sync(self, backup_type='Manual'):
        self.ensure_one()
        from odoo.service import db

        access_token = self._get_gdrive_access_token()
        if not access_token:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': _('Failed to authenticate with Google Drive. Please reconnect your account.'),
                    'type': 'danger',
                    'sticky': False,
                }
            }

        timestamp = self._get_backup_timestamp()
        root_folder_name = f"{backup_type}_{timestamp}"

        try:
            db_list = self._get_db_list(backup_type)
            if isinstance(db_list, dict):
                return db_list

            parent_folder_id = self.env['ir.config_parameter'].sudo().get_param('auto_backup_db_cloud.gdrive_folder_id')
            folder_data = self._create_gdrive_folder(access_token, root_folder_name, parent_folder_id)
            if not folder_data:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Error'),
                        'message': _('Failed to create root folder in Google Drive.'),
                        'type': 'danger',
                        'sticky': False,
                    }
                }
            root_folder_id = folder_data.get('id')
            gdrive_created_time = folder_data.get('createdTime')

            success_count = 0
            error_details = []

            for db_name in db_list:
                folder_result = self._create_gdrive_folder(access_token, db_name, root_folder_id)
                if not folder_result:
                    error_details.append(f"Could not create folder for {db_name}")
                    continue
                db_folder_id = folder_result.get('id')

                try:
                    sql_stream = io.BytesIO()
                    db.dump_db(db_name, sql_stream, backup_format='sql')
                    sql_stream.seek(0)
                    sql_uploaded = self._upload_to_gdrive(access_token, sql_stream, f"{db_name}.sql", "application/sql", db_folder_id)

                    zip_stream = io.BytesIO()
                    db.dump_db(db_name, zip_stream, backup_format='zip')
                    zip_stream.seek(0)
                    zip_uploaded = self._upload_to_gdrive(access_token, zip_stream, f"{db_name}.zip", "application/zip", db_folder_id)

                    if sql_uploaded and zip_uploaded:
                        success_count += 1
                    else:
                        error_details.append(f"Upload failed for {db_name}")
                except Exception as e:
                    error_details.append(f"Backup failed for {db_name}: {str(e)}")

            try:
                self._apply_gdrive_retention_policy(access_token)
            except Exception as e:
                error_details.append(f"Retention policy failed: {str(e)}")

            if gdrive_created_time:
                from odoo.fields import Datetime
                try:
                    clean_time = gdrive_created_time.split('.')[0].replace('T', ' ').replace('Z', '')
                    last_run = Datetime.to_datetime(clean_time)
                except Exception:
                    last_run = fields.Datetime.now()
            else:
                last_run = fields.Datetime.now()

            self._save_backup_last_run(last_run)
            return self._build_summary_notification('Google Drive', success_count, error_details)

        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': _('Process failed: %s') % str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            }

    def _apply_gdrive_retention_policy(self, access_token):
        ICP = self.env['ir.config_parameter'].sudo()
        retention_type = ICP.get_param('auto_backup_db_cloud.gdrive_retention_type')
        if not retention_type or retention_type == 'False':
            retention_type = 'none'

        if retention_type == 'none':
            return

        parent_id = ICP.get_param('auto_backup_db_cloud.gdrive_folder_id')
        if not parent_id or parent_id == 'False':
            return

        headers = {"Authorization": f"Bearer {access_token}"}
        query = f"(name contains 'Auto_' or name contains 'Manual_') and mimeType = 'application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed = false"

        params = {
            "q": query,
            "fields": "files(id, name, createdTime)",
            "orderBy": "createdTime asc",
            "pageSize": 1000
        }

        response = requests.get("https://www.googleapis.com/drive/v3/files", headers=headers, params=params)
        if response.status_code != 200:
            return

        files = response.json().get('files', [])
        if not files:
            return

        if retention_type == 'count':
            try:
                raw_count = ICP.get_param('auto_backup_db_cloud.gdrive_retention_count')
                retention_count = int(raw_count) if raw_count and raw_count != 'False' else 10
            except (ValueError, TypeError):
                retention_count = 10

            if len(files) > retention_count:
                to_delete = files[:len(files) - retention_count]
                for f in to_delete:
                    requests.delete(f"https://www.googleapis.com/drive/v3/files/{f['id']}", headers=headers)

        elif retention_type == 'days':
            try:
                raw_days = ICP.get_param('auto_backup_db_cloud.gdrive_retention_days')
                retention_days = int(raw_days) if raw_days and raw_days != 'False' else 30
            except (ValueError, TypeError):
                retention_days = 30

            from datetime import timedelta
            limit_date = fields.Datetime.now() - timedelta(days=retention_days)

            for f in files:
                try:
                    f_date_str = f['createdTime'].split('.')[0].replace('T', ' ').replace('Z', '')
                    f_date = fields.Datetime.to_datetime(f_date_str)
                    if f_date < limit_date:
                        requests.delete(f"https://www.googleapis.com/drive/v3/files/{f['id']}", headers=headers)
                except Exception as e:
                    _logger.error("Error parsing date or deleting for %s: %s", f['name'], str(e))
