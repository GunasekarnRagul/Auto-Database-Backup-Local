# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.http import request
import requests
import json
import io
import logging
from datetime import datetime

_logger = logging.getLogger(__name__)


class ResConfigSettingsOneDrive(models.TransientModel):
    _inherit = 'res.config.settings'

    # ─── OneDrive Credentials ───────────────────────────────────────
    onedrive_client_id = fields.Char(
        string='Client ID',
        config_parameter='auto_backup_db_cloud.onedrive_client_id'
    )
    onedrive_client_secret = fields.Char(
        string='Client Secret',
        config_parameter='auto_backup_db_cloud.onedrive_client_secret'
    )
    onedrive_folder_path = fields.Char(
        string='Target Folder Path',
        config_parameter='auto_backup_db_cloud.onedrive_folder_path'
    )
    onedrive_refresh_token = fields.Char(
        string='Refresh Token',
        config_parameter='auto_backup_db_cloud.onedrive_refresh_token',
        readonly=True
    )
    onedrive_redirect_uri = fields.Char(
        string='Redirect URI',
        compute='_compute_onedrive_redirect_uri'
    )

    # ─── OneDrive Backup Settings ───────────────────────────────────
    onedrive_backup_active = fields.Boolean(
        string='Enable OneDrive Auto Backup',
        config_parameter='auto_backup_db_cloud.onedrive_backup_active'
    )
    onedrive_backup_interval_number = fields.Integer(
        string='Backup Interval',
        config_parameter='auto_backup_db_cloud.onedrive_backup_interval_number',
        default=1
    )
    onedrive_backup_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], string='Backup Interval Unit',
        config_parameter='auto_backup_db_cloud.onedrive_backup_interval_type',
        default='days')

    onedrive_backup_last_run = fields.Datetime(
        string='Last OneDrive Backup Run',
        config_parameter='auto_backup_db_cloud.onedrive_backup_last_run',
        readonly=True
    )

    onedrive_retention_type = fields.Selection([
        ('none', 'Keep All'),
        ('count', 'Keep Last X Backups'),
        ('days', 'Keep for X Days'),
    ], string='Retention Policy',
        config_parameter='auto_backup_db_cloud.onedrive_retention_type',
        default='none')

    onedrive_retention_count = fields.Integer(
        string='Number of Backups to Keep',
        config_parameter='auto_backup_db_cloud.onedrive_retention_count',
        default=10
    )

    onedrive_retention_days = fields.Integer(
        string='Number of Days to Keep',
        config_parameter='auto_backup_db_cloud.onedrive_retention_days',
        default=30
    )

    onedrive_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Sync Scope',
        config_parameter='auto_backup_db_cloud.onedrive_sync_scope',
        default='all', required=True)

    onedrive_selected_db_ids = fields.Many2many(
        'cloud.backup.db',
        'cloud_onedrive_auto_db_rel',
        string='Select Databases'
    )

    onedrive_manual_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Manual Sync Scope',
        config_parameter='auto_backup_db_cloud.onedrive_manual_sync_scope',
        default='all', required=True)

    onedrive_manual_selected_db_ids = fields.Many2many(
        'cloud.backup.db',
        'cloud_onedrive_manual_db_rel',
        string='Manual Select Databases'
    )

    # ═══════════════════════════════════════════════════════════════
    # Compute / Auth
    # ═══════════════════════════════════════════════════════════════

    @api.depends('onedrive_client_id')
    def _compute_onedrive_redirect_uri(self):
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
            config.onedrive_redirect_uri = f"{host_url}/onedrive_account/authentication"

    def action_onedrive_authenticate(self):
        self.ensure_one()
        if not self.onedrive_client_id:
            return False

        params = {
            'client_id': self.onedrive_client_id,
            'redirect_uri': self.onedrive_redirect_uri,
            'response_type': 'code',
            'scope': 'Files.ReadWrite.All offline_access',
            'response_mode': 'query',
            'state': 'cloud_onedrive_auth_callback',
        }
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + "&".join([f"{k}={v}" for k, v in params.items()])
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def action_onedrive_disconnect(self):
        self.ensure_one()
        self.onedrive_client_id = False
        self.onedrive_client_secret = False
        self.onedrive_folder_path = False
        self.onedrive_refresh_token = False
        self.onedrive_backup_active = False

        ICP = self.env['ir.config_parameter'].sudo()
        params_to_clear = [
            'auto_backup_db_cloud.onedrive_client_id',
            'auto_backup_db_cloud.onedrive_client_secret',
            'auto_backup_db_cloud.onedrive_folder_path',
            'auto_backup_db_cloud.onedrive_refresh_token',
            'auto_backup_db_cloud.onedrive_backup_active',
        ]
        for param in params_to_clear:
            ICP.set_param(param, False)

        return {'type': 'ir.actions.client', 'tag': 'reload'}

    # ═══════════════════════════════════════════════════════════════
    # OneDrive Backup Logic
    # ═══════════════════════════════════════════════════════════════

    def _get_onedrive_access_token(self):
        ICP = self.env['ir.config_parameter'].sudo()
        client_id = ICP.get_param('auto_backup_db_cloud.onedrive_client_id')
        client_secret = ICP.get_param('auto_backup_db_cloud.onedrive_client_secret')
        refresh_token = ICP.get_param('auto_backup_db_cloud.onedrive_refresh_token')

        if not (client_id and client_secret and refresh_token):
            return False

        data = {
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
            'scope': 'Files.ReadWrite.All offline_access',
        }
        response = requests.post("https://login.microsoftonline.com/common/oauth2/v2.0/token", data=data)
        if response.status_code == 200:
            return response.json().get('access_token')
        return False

    def _ensure_onedrive_path(self, access_token, path):
        if not path:
            return None

        segments = [s for s in path.strip('/').split('/') if s]
        if not segments:
            return None

        parent_id = None
        current_folder = None

        for segment in segments:
            current_folder = self._create_onedrive_folder(access_token, segment, parent_id)
            if not current_folder:
                return False
            parent_id = current_folder.get('id')

        return current_folder

    def _create_onedrive_folder(self, access_token, folder_name, parent_id=None):
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        from urllib.parse import unquote

        if parent_id:
            decoded = unquote(parent_id)
            if decoded.startswith('/'):
                children_url = f"https://graph.microsoft.com/v1.0/me/drive/root:{decoded}:/children"
                check_url = f"https://graph.microsoft.com/v1.0/me/drive/root:{decoded}/{folder_name}:"
            else:
                children_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{decoded}/children"
                check_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{decoded}:/{folder_name}:"
        else:
            children_url = "https://graph.microsoft.com/v1.0/me/drive/root/children"
            check_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder_name}:"

        try:
            check_response = requests.get(check_url, headers=headers)
            if check_response.status_code == 200:
                result = check_response.json()
                if 'folder' in result:
                    return {
                        'id': result.get('id'),
                        'createdTime': result.get('createdDateTime')
                    }
        except Exception as e:
            _logger.debug("OneDrive check folder failed (expected if new): %s", str(e))

        metadata = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail"
        }

        response = requests.post(children_url, headers=headers, json=metadata)
        if response.status_code in [200, 201]:
            result = response.json()
            return {
                'id': result.get('id'),
                'createdTime': result.get('createdDateTime')
            }
        elif response.status_code == 409:
            check_response = requests.get(check_url, headers=headers)
            if check_response.status_code == 200:
                result = check_response.json()
                return {
                    'id': result.get('id'),
                    'createdTime': result.get('createdDateTime')
                }

        _logger.warning("Failed to create OneDrive folder '%s': %s %s", folder_name, response.status_code, response.text)
        return False

    def _upload_to_onedrive(self, access_token, file_content, file_name, mime_type, parent_id=None):
        headers = {"Authorization": f"Bearer {access_token}"}
        file_size = len(file_content)

        from urllib.parse import unquote

        if parent_id:
            decoded = unquote(str(parent_id))
            if decoded.startswith('/'):
                item_path = f"root:{decoded}/{file_name}:"
            else:
                item_path = f"items/{decoded}:/{file_name}:"
        else:
            item_path = f"root:/{file_name}:"

        url_base = f"https://graph.microsoft.com/v1.0/me/drive/{item_path}"

        if file_size <= 4 * 1024 * 1024:
            url = f"{url_base}/content"
            response = requests.put(url, headers=headers, data=file_content)
            return response.status_code in [200, 201]

        try:
            session_url = f"{url_base}/createUploadSession"
            session_payload = {
                "item": {
                    "@microsoft.graph.conflictBehavior": "replace",
                    "name": file_name
                }
            }
            session_request = requests.post(session_url, headers=headers, json=session_payload)

            if session_request.status_code != 200:
                _logger.error("Failed to create OneDrive upload session for %s: %s %s",
                              file_name, session_request.status_code, session_request.text)
                return False

            upload_url = session_request.json().get('uploadUrl')
            chunk_size = 320 * 1024 * 16

            for i in range(0, file_size, chunk_size):
                chunk = file_content[i:i + chunk_size]
                content_range = f"bytes {i}-{i + len(chunk) - 1}/{file_size}"
                put_headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": content_range
                }

                put_response = requests.put(upload_url, headers=put_headers, data=chunk)
                if put_response.status_code not in [200, 201, 202]:
                    _logger.error("OneDrive chunk upload failed at %s for %s: %s", content_range, file_name, put_response.text)
                    return False

            return True
        except Exception as e:
            _logger.error("OneDrive large upload exception for %s: %s", file_name, str(e))
            return False

    def _onedrive_manual_sync(self, backup_type='Manual'):
        self.ensure_one()
        from odoo.service import db
        import pytz

        access_token = self._get_onedrive_access_token()
        if not access_token:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': _('Failed to authenticate with OneDrive. Please reconnect your account.'),
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

            ICP = self.env['ir.config_parameter'].sudo()
            parent_folder_path = ICP.get_param('auto_backup_db_cloud.onedrive_folder_path')
            actual_parent_id = None

            if parent_folder_path:
                parent_data = self._ensure_onedrive_path(access_token, parent_folder_path)
                if not parent_data:
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Error'),
                            'message': _('Failed to resolve or create target path: %s') % parent_folder_path,
                            'type': 'danger',
                            'sticky': False,
                        }
                    }
                actual_parent_id = parent_data.get('id')

            folder_data = self._create_onedrive_folder(access_token, root_folder_name, actual_parent_id)
            if not folder_data:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Error'),
                        'message': _('Failed to create root folder in OneDrive.'),
                        'type': 'danger',
                        'sticky': False,
                    }
                }
            root_folder_id = folder_data.get('id')
            onedrive_created_time = folder_data.get('createdTime')

            success_count = 0
            error_details = []

            for db_name in db_list:
                folder_result = self._create_onedrive_folder(access_token, db_name, root_folder_id)
                if not folder_result:
                    error_details.append(f"Could not create folder for {db_name}")
                    continue
                db_folder_id = folder_result.get('id')

                try:
                    sql_stream = io.BytesIO()
                    db.dump_db(db_name, sql_stream, backup_format='sql')
                    sql_stream.seek(0)
                    sql_uploaded = self._upload_to_onedrive(access_token, sql_stream.read(), f"{db_name}.sql", "application/sql", db_folder_id)

                    zip_stream = io.BytesIO()
                    db.dump_db(db_name, zip_stream, backup_format='zip')
                    zip_stream.seek(0)
                    zip_uploaded = self._upload_to_onedrive(access_token, zip_stream.read(), f"{db_name}.zip", "application/zip", db_folder_id)

                    if sql_uploaded and zip_uploaded:
                        success_count += 1
                    else:
                        error_details.append(f"Upload failed for {db_name}")
                except Exception as e:
                    error_details.append(f"Backup failed for {db_name}: {str(e)}")

            try:
                self._apply_onedrive_retention_policy(access_token)
            except Exception as e:
                error_details.append(f"Retention policy failed: {str(e)}")

            if onedrive_created_time:
                from odoo.fields import Datetime
                try:
                    clean_time = onedrive_created_time.split('.')[0].replace('T', ' ').replace('Z', '')
                    last_run = Datetime.to_datetime(clean_time)
                except Exception:
                    last_run = fields.Datetime.now()
            else:
                last_run = fields.Datetime.now()

            self._save_backup_last_run(last_run)
            return self._build_summary_notification('OneDrive', success_count, error_details)

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

    def _apply_onedrive_retention_policy(self, access_token):
        ICP = self.env['ir.config_parameter'].sudo()
        retention_type = ICP.get_param('auto_backup_db_cloud.onedrive_retention_type')
        if not retention_type or retention_type == 'False':
            retention_type = 'none'

        if retention_type == 'none':
            return

        parent_path = ICP.get_param('auto_backup_db_cloud.onedrive_folder_path')
        headers = {"Authorization": f"Bearer {access_token}"}

        if parent_path:
            from urllib.parse import unquote
            decoded = unquote(parent_path)
            if decoded.startswith('/'):
                url = f"https://graph.microsoft.com/v1.0/me/drive/root:{decoded}:/children"
            else:
                url = f"https://graph.microsoft.com/v1.0/me/drive/items/{decoded}/children"
        else:
            url = "https://graph.microsoft.com/v1.0/me/drive/root/children"

        params = {"$select": "id,name,createdDateTime,folder"}

        try:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code != 200:
                return

            items = response.json().get('value', [])
            backup_folders = [f for f in items if f.get('folder') is not None and
                              (f['name'].startswith('Auto_') or f['name'].startswith('Manual_'))]

            if not backup_folders:
                return

            backup_folders.sort(key=lambda x: x.get('createdDateTime', ''))

            if retention_type == 'count':
                try:
                    raw_count = ICP.get_param('auto_backup_db_cloud.onedrive_retention_count')
                    retention_count = int(raw_count) if raw_count and raw_count != 'False' else 10
                except (ValueError, TypeError):
                    retention_count = 10

                if len(backup_folders) > retention_count:
                    to_delete = backup_folders[:len(backup_folders) - retention_count]
                    for f in to_delete:
                        requests.delete(f"https://graph.microsoft.com/v1.0/me/drive/items/{f['id']}", headers=headers)

            elif retention_type == 'days':
                try:
                    raw_days = ICP.get_param('auto_backup_db_cloud.onedrive_retention_days')
                    retention_days = int(raw_days) if raw_days and raw_days != 'False' else 30
                except (ValueError, TypeError):
                    retention_days = 30

                from datetime import timedelta
                limit_date = fields.Datetime.now() - timedelta(days=retention_days)

                for f in backup_folders:
                    try:
                        f_date_str = f['createdDateTime'].split('.')[0].replace('T', ' ').replace('Z', '')
                        f_date = fields.Datetime.to_datetime(f_date_str)
                        if f_date < limit_date:
                            requests.delete(f"https://graph.microsoft.com/v1.0/me/drive/items/{f['id']}", headers=headers)
                    except Exception as e:
                        _logger.error("Retention Policy: Error processing folder %s: %s", f['name'], str(e))

        except Exception as e:
            _logger.error("Retention Policy: Unexpected error: %s", str(e))
