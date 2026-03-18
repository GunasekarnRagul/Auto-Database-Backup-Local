# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.http import request
import requests
import json
import base64
import io
import zipfile
from datetime import datetime
import pytz
from odoo.service import db
import logging
_logger = logging.getLogger(__name__)

class OneDriveBackupDb(models.Model):
    _name = 'onedrive.backup.db'
    _description = 'OneDrive Backup Database'

    name = fields.Char(string='Database Name', required=True)

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    onedrive_client_id = fields.Char(
        string='Client ID', 
        config_parameter='auto_backup_db_onedrive.client_id'
    )
    onedrive_client_secret = fields.Char(
        string='Client Secret', 
        config_parameter='auto_backup_db_onedrive.client_secret'
    )
    onedrive_folder_path = fields.Char(
        string='Target Folder Path', 
        config_parameter='auto_backup_db_onedrive.folder_path'
    )
    onedrive_refresh_token = fields.Char(
        string='Refresh Token', 
        config_parameter='auto_backup_db_onedrive.refresh_token',
        readonly=True
    )
    onedrive_redirect_uri = fields.Char(
        string='Redirect URI', 
        compute='_compute_onedrive_redirect_uri'
    )
    
    # Auto Backup Configuration
    onedrive_backup_active = fields.Boolean(
        string='Enable Auto Backup',
        config_parameter='auto_backup_db_onedrive.backup_active'
    )
    onedrive_backup_interval_number = fields.Integer(
        string='Backup Interval',
        config_parameter='auto_backup_db_onedrive.backup_interval_number',
        default=1
    )
    onedrive_backup_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], string='Backup Interval Unit', config_parameter='auto_backup_db_onedrive.backup_interval_type', default='days')
    
    onedrive_backup_last_run = fields.Datetime(
        string='Last Backup Run',
        config_parameter='auto_backup_db_onedrive.backup_last_run',
        readonly=True
    )

    # Retention Policy
    onedrive_retention_type = fields.Selection([
        ('none', 'Keep All'),
        ('count', 'Keep Last X Backups'),
        ('days', 'Keep for X Days'),
    ], string='Retention Policy', config_parameter='auto_backup_db_onedrive.retention_type', default='none')
    
    onedrive_retention_count = fields.Integer(
        string='Number of Backups to Keep',
        config_parameter='auto_backup_db_onedrive.retention_count',
        default=10
    )
    
    onedrive_retention_days = fields.Integer(
        string='Number of Days to Keep',
        config_parameter='auto_backup_db_onedrive.retention_days',
        default=30
    )
   
    onedrive_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Sync Scope', config_parameter='auto_backup_db_onedrive.sync_scope', default='all', required=True)

    onedrive_selected_db_ids = fields.Many2many(
        'onedrive.backup.db', 
        'onedrive_auto_db_rel',
        string='Select Databases'
    )

    # Manual Backup Sync Rules
    onedrive_manual_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Manual Sync Scope', config_parameter='auto_backup_db_onedrive.manual_sync_scope', default='all', required=True)

    onedrive_manual_selected_db_ids = fields.Many2many(
        'onedrive.backup.db', 
        'onedrive_manual_db_rel',
        string='Manual Select Databases'
    )

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        ICP = self.env['ir.config_parameter'].sudo()
        
        # Save Auto DB IDs
        auto_db_ids = self.onedrive_selected_db_ids.ids
        ICP.set_param('auto_backup_db_onedrive.selected_db_ids', json.dumps(auto_db_ids))
        
        # Save Manual DB IDs
        manual_db_ids = self.onedrive_manual_selected_db_ids.ids
        ICP.set_param('auto_backup_db_onedrive.manual_selected_db_ids', json.dumps(manual_db_ids))
        
        # Sync with Cron Job
        cron = self.env.ref('auto_backup_db_onedrive.ir_cron_auto_backup_db_onedrive', raise_if_not_found=False)
        if cron:
            vals = {
                'active': self.onedrive_backup_active,
                'interval_number': self.onedrive_backup_interval_number,
                'interval_type': self.onedrive_backup_interval_type,
            }
            cron.write(vals)

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env['ir.config_parameter'].sudo()
        
        # Get Auto DB IDs
        auto_db_ids_str = ICP.get_param('auto_backup_db_onedrive.selected_db_ids', '[]')
        try:
            auto_db_ids = json.loads(auto_db_ids_str)
        except Exception:
            auto_db_ids = []
            
        # Get Manual DB IDs
        manual_db_ids_str = ICP.get_param('auto_backup_db_onedrive.manual_selected_db_ids', '[]')
        try:
            manual_db_ids = json.loads(manual_db_ids_str)
        except Exception:
            manual_db_ids = []

        # Filter out non-existent IDs to prevent "Missing Record" error in UI
        if auto_db_ids:
            auto_db_ids = self.env['onedrive.backup.db'].sudo().browse(auto_db_ids).exists().ids
        if manual_db_ids:
            manual_db_ids = self.env['onedrive.backup.db'].sudo().browse(manual_db_ids).exists().ids

        res.update(
            onedrive_selected_db_ids=[(6, 0, auto_db_ids)],
            onedrive_manual_selected_db_ids=[(6, 0, manual_db_ids)],
        )
        return res

    @api.onchange('onedrive_sync_scope')
    def _onchange_onedrive_sync_scope(self):
        if self.onedrive_sync_scope in ['all', 'selective']:
            try:
                available_dbs = db.list_dbs()
                existing_dbs = self.env['onedrive.backup.db'].search([])
                existing_names = existing_dbs.mapped('name')
                
                # Add new ones
                for db_name in available_dbs:
                    if db_name not in existing_names:
                        self.env['onedrive.backup.db'].create({'name': db_name})
            except Exception:
                pass

    @api.depends('onedrive_client_id')
    def _compute_onedrive_redirect_uri(self):
        base_url_param = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for config in self:
            host_url = base_url_param or ''
            try:
                # Detect the actual host from the current request context
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
            'state': 'onedrive_auth_callback',
        }
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + "&".join([f"{k}={v}" for k, v in params.items()])
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def action_onedrive_disconnect(self):
        self.ensure_one()
        # Reset all credentials and folder info
        self.onedrive_client_id = False
        self.onedrive_client_secret = False
        self.onedrive_folder_path = False
        self.onedrive_refresh_token = False
        
        # Disable auto-backup settings
        self.onedrive_backup_active = False
        
        # Clear system parameters explicitly to ensure persistence
        ICP = self.env['ir.config_parameter'].sudo()
        params_to_clear = [
            'auto_backup_db_onedrive.client_id',
            'auto_backup_db_onedrive.client_secret',
            'auto_backup_db_onedrive.folder_path',
            'auto_backup_db_onedrive.refresh_token',
            'auto_backup_db_onedrive.backup_active',
            'auto_backup_db_onedrive.backup_interval_number',
            'auto_backup_db_onedrive.backup_interval_type',
            'auto_backup_db_onedrive.sync_scope',
            'auto_backup_db_onedrive.selected_db_ids',
        ]
        for param in params_to_clear:
            ICP.set_param(param, False)
            
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def _get_onedrive_access_token(self):
        ICP = self.env['ir.config_parameter'].sudo()
        client_id = ICP.get_param('auto_backup_db_onedrive.client_id')
        client_secret = ICP.get_param('auto_backup_db_onedrive.client_secret')
        refresh_token = ICP.get_param('auto_backup_db_onedrive.refresh_token')
        
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
        """Ensures that each folder in the path exists, returning the final folder's metadata."""
        if not path:
            return None
        
        # Clean path: remove trailing slashes and split
        segments = [s for s in path.strip('/').split('/') if s]
        if not segments:
            return None
            
        parent_id = None
        current_folder = None
        
        for segment in segments:
            # Note: _create_onedrive_folder already reuses if exists
            current_folder = self._create_onedrive_folder(access_token, segment, parent_id)
            if not current_folder:
                return False
            parent_id = current_folder.get('id')
            
        return current_folder

    def _create_onedrive_folder(self, access_token, folder_name, parent_id=None):
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        
        # URL-decode in case it was stored encoded
        from urllib.parse import unquote
        
        # Determine the base URLs for creating and checking existing items
        if parent_id:
            decoded = unquote(parent_id)
            if decoded.startswith('/'):
                # Path-based parent
                children_url = f"https://graph.microsoft.com/v1.0/me/drive/root:{decoded}:/children"
                check_url = f"https://graph.microsoft.com/v1.0/me/drive/root:{decoded}/{folder_name}:"
            else:
                # ID-based parent
                children_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{decoded}/children"
                check_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{decoded}:/{folder_name}:"
        else:
            # Root drive
            children_url = "https://graph.microsoft.com/v1.0/me/drive/root/children"
            check_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder_name}:"
            
        # Try to find if folder already exists first to satisfy UNLESS "DONT CREATE A NEW FOLDER"
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
            "@microsoft.graph.conflictBehavior": "fail" # Use 'fail' to avoid "Folder 1" renaming
        }
            
        response = requests.post(children_url, headers=headers, json=metadata)
        if response.status_code in [200, 201]:
            result = response.json()
            return {
                'id': result.get('id'),
                'createdTime': result.get('createdDateTime')
            }
        elif response.status_code == 409:
            # Conflict occurred (another check just in case of race condition)
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
        
        # Simple upload for small files (limit is 4MB for simple PUT in Graph API)
        if file_size <= 4 * 1024 * 1024:
            url = f"{url_base}/content"
            response = requests.put(url, headers=headers, data=file_content)
            return response.status_code in [200, 201]
            
        # Large file upload (Upload Session)
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
                _logger.error("Failed to create OneDrive upload session for %s at URL %s: Status %s, Response %s", 
                              file_name, session_url, session_request.status_code, session_request.text)
                return False
                
            upload_url = session_request.json().get('uploadUrl')
            # Chunk size must be multiple of 320KB
            chunk_size = 320 * 1024 * 16 # ~5.2 MB chunks
            
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

    def action_manual_sync(self, backup_type='Manual'):
        self.ensure_one()
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
            
        # Determine the timezone, prioritizing context, then user, then company
        tz_name = self.env.context.get('tz') or self.env.user.tz or self.env.company.partner_id.tz or 'UTC'
        tz = pytz.timezone(tz_name)
        
        # Get current time in that timezone (for folder naming)
        local_dt = datetime.now(tz)
        
        timestamp = local_dt.strftime("%d-%m-%Y_%H-%M-%S")
        root_folder_name = f"{backup_type}_{timestamp}"
        
        try:
            ICP = self.env['ir.config_parameter'].sudo()
            if backup_type == 'Auto':
                sync_scope = ICP.get_param('auto_backup_db_onedrive.sync_scope') or 'all'
                db_ids_field = 'auto_backup_db_onedrive.selected_db_ids'
            else:
                sync_scope = ICP.get_param('auto_backup_db_onedrive.manual_sync_scope') or 'all'
                db_ids_field = 'auto_backup_db_onedrive.manual_selected_db_ids'

            if sync_scope == 'selective':
                selected_db_ids_str = ICP.get_param(db_ids_field, '[]')
                selected_db_ids = json.loads(selected_db_ids_str)
                selected_dbs = self.env['onedrive.backup.db'].browse(selected_db_ids)
                db_list = selected_dbs.mapped('name')
                if not db_list:
                     return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Warning'),
                            'message': _('No databases selected for sync. Please select at least one database in the Sync Rules.'),
                            'type': 'warning',
                            'sticky': False,
                        }
                    }
            else:
                db_list = db.list_dbs()

            parent_folder_path = ICP.get_param('auto_backup_db_onedrive.folder_path')
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
                    # SQL Dump
                    sql_stream = io.BytesIO()
                    db.dump_db(db_name, sql_stream, backup_format='sql')
                    sql_stream.seek(0)
                    sql_uploaded = self._upload_to_onedrive(access_token, sql_stream.read(), f"{db_name}.sql", "application/sql", db_folder_id)
                    
                    # ZIP (with filestore)
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
                self._apply_retention_policy(access_token, backup_type)
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
            
            self.onedrive_backup_last_run = last_run
            ICP.set_param('auto_backup_db_onedrive.backup_last_run', last_run)
            
            message = _('Successfully backed up %s databases.') % success_count
            if error_details:
                message += "\n" + "\n".join(error_details)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Backup Summary'),
                    'message': message,
                    'type': 'success' if not error_details else 'warning',
                    'sticky': bool(error_details),
                }
            }
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

    @api.model
    def _cron_auto_backup(self):
        ICP = self.env['ir.config_parameter'].sudo()
        if not ICP.get_param('auto_backup_db_onedrive.backup_active'):
            return
            
        config = self.create({})
        config.action_manual_sync(backup_type='Auto')

    def _apply_retention_policy(self, access_token, backup_type):
        ICP = self.env['ir.config_parameter'].sudo()
        retention_type = ICP.get_param('auto_backup_db_onedrive.retention_type')
        if not retention_type or retention_type == 'False':
            retention_type = 'none'
            
        if retention_type == 'none':
            return
            
        parent_path = ICP.get_param('auto_backup_db_onedrive.folder_path')
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Resolve URL for listing children
        if parent_path:
            from urllib.parse import unquote
            decoded = unquote(parent_path)
            if decoded.startswith('/'):
                url = f"https://graph.microsoft.com/v1.0/me/drive/root:{decoded}:/children"
            else:
                url = f"https://graph.microsoft.com/v1.0/me/drive/items/{decoded}/children"
        else:
            url = "https://graph.microsoft.com/v1.0/me/drive/root/children"
            
        params = {
            "$select": "id,name,createdDateTime,folder",
        }
        
        try:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code != 200:
                _logger.warning("Retention Policy: Failed to list children at %s: %s", url, response.text)
                return
                
            items = response.json().get('value', [])
            # Filter for backup folders (any item starting with Auto_ or Manual_ that is a folder)
            backup_folders = [f for f in items if f.get('folder') is not None and 
                              (f['name'].startswith('Auto_') or f['name'].startswith('Manual_'))]
            
            if not backup_folders:
                _logger.debug("Retention Policy: No backup folders found to evaluate.")
                return
                
            # Explicitly sort by creation date (oldest first)
            backup_folders.sort(key=lambda x: x.get('createdDateTime', ''))
            
            if retention_type == 'count':
                try:
                    raw_count = ICP.get_param('auto_backup_db_onedrive.retention_count')
                    retention_count = int(raw_count) if raw_count and raw_count != 'False' else 10
                except (ValueError, TypeError):
                    retention_count = 10
                    
                if len(backup_folders) > retention_count:
                    to_delete = backup_folders[:len(backup_folders) - retention_count]
                    _logger.info("Retention Policy (Count): Pruning %s of %s backup folders.", len(to_delete), len(backup_folders))
                    for f in to_delete:
                        del_res = requests.delete(f"https://graph.microsoft.com/v1.0/me/drive/items/{f['id']}", headers=headers)
                        if del_res.status_code not in [200, 204]:
                             _logger.warning("Retention Policy: Failed to delete folder %s (%s): %s", f['name'], f['id'], del_res.text)
                        
            elif retention_type == 'days':
                try:
                    raw_days = ICP.get_param('auto_backup_db_onedrive.retention_days')
                    retention_days = int(raw_days) if raw_days and raw_days != 'False' else 30
                except (ValueError, TypeError):
                    retention_days = 30
                    
                from datetime import timedelta
                # Datetime.now() is naive UTC in Odoo
                limit_date = fields.Datetime.now() - timedelta(days=retention_days)
                
                deleted_count = 0
                for f in backup_folders:
                    try:
                        # createdDateTime is UTC
                        f_date_str = f['createdDateTime'].split('.')[0].replace('T', ' ').replace('Z', '')
                        f_date = fields.Datetime.to_datetime(f_date_str)
                        if f_date < limit_date:
                            del_res = requests.delete(f"https://graph.microsoft.com/v1.0/me/drive/items/{f['id']}", headers=headers)
                            if del_res.status_code in [200, 204]:
                                deleted_count += 1
                            else:
                                _logger.warning("Retention Policy: Failed to delete folder %s (%s): %s", f['name'], f['id'], del_res.text)
                    except Exception as e:
                        _logger.error("Retention Policy: Error processing folder %s: %s", f['name'], str(e))
                
                if deleted_count:
                    _logger.info("Retention Policy (Days): Pruned %s older backup folders.", deleted_count)
        except Exception as e:
            _logger.error("Retention Policy: Unexpected error: %s", str(e))
