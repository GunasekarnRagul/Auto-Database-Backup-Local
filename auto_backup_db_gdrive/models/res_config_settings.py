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

class GDriveBackupDb(models.Model):
    _name = 'gdrive.backup.db'
    _description = 'Google Drive Backup Database'

    name = fields.Char(string='Database Name', required=True)

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gdrive_client_id = fields.Char(
        string='Client ID', 
        config_parameter='auto_backup_db_gdrive.client_id'
    )
    gdrive_client_secret = fields.Char(
        string='Client Secret', 
        config_parameter='auto_backup_db_gdrive.client_secret'
    )
    gdrive_folder_id = fields.Char(
        string='G Drive Folder ID', 
        config_parameter='auto_backup_db_gdrive.folder_id'
    )
    gdrive_refresh_token = fields.Char(
        string='Refresh Token', 
        config_parameter='auto_backup_db_gdrive.refresh_token',
        readonly=True
    )
    gdrive_redirect_uri = fields.Char(
        string='Redirect URI', 
        compute='_compute_gdrive_redirect_uri'
    )
    
    # Auto Backup Configuration
    gdrive_backup_active = fields.Boolean(
        string='Enable Auto Backup',
        config_parameter='auto_backup_db_gdrive.backup_active'
    )
    gdrive_backup_interval_number = fields.Integer(
        string='Backup Interval',
        config_parameter='auto_backup_db_gdrive.backup_interval_number',
        default=1
    )
    gdrive_backup_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], string='Backup Interval Unit', config_parameter='auto_backup_db_gdrive.backup_interval_type', default='days')
    
    gdrive_backup_last_run = fields.Datetime(
        string='Last Backup Run',
        config_parameter='auto_backup_db_gdrive.backup_last_run',
        readonly=True
    )

    # Retention Policy
    gdrive_retention_type = fields.Selection([
        ('none', 'Keep All'),
        ('count', 'Keep Last X Backups'),
        ('days', 'Keep for X Days'),
    ], string='Retention Policy', config_parameter='auto_backup_db_gdrive.retention_type', default='none')
    
    gdrive_retention_count = fields.Integer(
        string='Number of Backups to Keep',
        config_parameter='auto_backup_db_gdrive.retention_count',
        default=10
    )
    
    gdrive_retention_days = fields.Integer(
        string='Number of Days to Keep',
        config_parameter='auto_backup_db_gdrive.retention_days',
        default=30
    )
   
    gdrive_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Sync Scope', config_parameter='auto_backup_db_gdrive.sync_scope', default='all', required=True)

    gdrive_selected_db_ids = fields.Many2many(
        'gdrive.backup.db', 
        'gdrive_auto_db_rel',
        string='Select Databases'
    )

    # Manual Backup Sync Rules
    gdrive_manual_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Manual Sync Scope', config_parameter='auto_backup_db_gdrive.manual_sync_scope', default='all', required=True)

    gdrive_manual_selected_db_ids = fields.Many2many(
        'gdrive.backup.db', 
        'gdrive_manual_db_rel',
        string='Manual Select Databases'
    )

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        ICP = self.env['ir.config_parameter'].sudo()
        
        # Save Auto DB IDs
        auto_db_ids = self.gdrive_selected_db_ids.ids
        ICP.set_param('auto_backup_db_gdrive.selected_db_ids', json.dumps(auto_db_ids))
        
        # Save Manual DB IDs
        manual_db_ids = self.gdrive_manual_selected_db_ids.ids
        ICP.set_param('auto_backup_db_gdrive.manual_selected_db_ids', json.dumps(manual_db_ids))
        
        # Sync with Cron Job
        cron = self.env.ref('auto_backup_db_gdrive.ir_cron_auto_backup_db_gdrive', raise_if_not_found=False)
        if cron:
            vals = {
                'active': self.gdrive_backup_active,
                'interval_number': self.gdrive_backup_interval_number,
                'interval_type': self.gdrive_backup_interval_type,
            }
            cron.write(vals)

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env['ir.config_parameter'].sudo()
        
        # Get Auto DB IDs
        auto_db_ids_str = ICP.get_param('auto_backup_db_gdrive.selected_db_ids', '[]')
        try:
            auto_db_ids = json.loads(auto_db_ids_str)
        except Exception:
            auto_db_ids = []
            
        # Get Manual DB IDs
        manual_db_ids_str = ICP.get_param('auto_backup_db_gdrive.manual_selected_db_ids', '[]')
        try:
            manual_db_ids = json.loads(manual_db_ids_str)
        except Exception:
            manual_db_ids = []

        res.update(
            gdrive_selected_db_ids=[(6, 0, auto_db_ids)],
            gdrive_manual_selected_db_ids=[(6, 0, manual_db_ids)],
        )
        return res

    @api.onchange('gdrive_sync_scope')
    def _onchange_gdrive_sync_scope(self):
        if self.gdrive_sync_scope in ['all', 'selective']:
            try:
                available_dbs = db.list_dbs()
                existing_dbs = self.env['gdrive.backup.db'].search([])
                existing_names = existing_dbs.mapped('name')
                
                # Add new ones
                for db_name in available_dbs:
                    if db_name not in existing_names:
                        self.env['gdrive.backup.db'].create({'name': db_name})
            except Exception:
                pass

    @api.depends('gdrive_client_id')
    def _compute_gdrive_redirect_uri(self):
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
            config.gdrive_redirect_uri = f"{host_url}/google_account/authentication"

    def action_gdrive_authenticate(self):
        self.ensure_one()
        if not self.gdrive_client_id:
            return False
            
        params = {
            'client_id': self.gdrive_client_id,
            'redirect_uri': self.gdrive_redirect_uri,
            'response_type': 'code',
            'scope': 'https://www.googleapis.com/auth/drive.file', # or 'https://www.googleapis.com/auth/drive'
            'access_type': 'offline',
            'prompt': 'consent',
            'state': 'gdrive_auth_callback', # Fixed state for global settings
        }
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + "&".join([f"{k}={v}" for k, v in params.items()])
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def action_gdrive_disconnect(self):
        self.ensure_one()
        # Reset all credentials and folder info
        self.gdrive_client_id = False
        self.gdrive_client_secret = False
        self.gdrive_folder_id = False
        self.gdrive_refresh_token = False
        
        # Disable auto-backup settings
        self.gdrive_backup_active = False
        
        # Clear system parameters explicitly to ensure persistence
        ICP = self.env['ir.config_parameter'].sudo()
        params_to_clear = [
            'auto_backup_db_gdrive.client_id',
            'auto_backup_db_gdrive.client_secret',
            'auto_backup_db_gdrive.folder_id',
            'auto_backup_db_gdrive.refresh_token',
            'auto_backup_db_gdrive.backup_active',
            'auto_backup_db_gdrive.backup_interval_number',
            'auto_backup_db_gdrive.backup_interval_type',
            'auto_backup_db_gdrive.sync_scope',
            'auto_backup_db_gdrive.selected_db_ids',
        ]
        for param in params_to_clear:
            ICP.set_param(param, False)
            
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def _get_gdrive_access_token(self):
        ICP = self.env['ir.config_parameter'].sudo()
        client_id = ICP.get_param('auto_backup_db_gdrive.client_id')
        client_secret = ICP.get_param('auto_backup_db_gdrive.client_secret')
        refresh_token = ICP.get_param('auto_backup_db_gdrive.refresh_token')
        
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

    def action_manual_sync(self, backup_type='Manual'):
        self.ensure_one()
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
            
        # Root Folder Name: [Type]_DD-MM-YYYY_HH-MM (using local time)
        # Try to get timezone: User -> Company -> UTC
        user_tz = self.env.user.tz or self.env.company.partner_id.tz or 'UTC'
        tz = pytz.timezone(user_tz)
        
        # Get UTC now and convert to local
        utc_now = fields.Datetime.now()
        local_dt = pytz.utc.localize(utc_now).astimezone(tz)
        
        timestamp = local_dt.strftime("%d-%m-%Y")
        root_folder_name = f"{backup_type}_{timestamp}"
        
        # 2. Get databases based on sync scope and validate
        try:
            ICP = self.env['ir.config_parameter'].sudo()
            if backup_type == 'Auto':
                sync_scope = ICP.get_param('auto_backup_db_gdrive.sync_scope') or 'all'
                db_ids_field = 'auto_backup_db_gdrive.selected_db_ids'
            else:
                sync_scope = ICP.get_param('auto_backup_db_gdrive.manual_sync_scope') or 'all'
                db_ids_field = 'auto_backup_db_gdrive.manual_selected_db_ids'

            if sync_scope == 'selective':
                selected_db_ids_str = ICP.get_param(db_ids_field, '[]')
                selected_db_ids = json.loads(selected_db_ids_str)
                selected_dbs = self.env['gdrive.backup.db'].browse(selected_db_ids)
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

            # 3. Create Root Folder
            parent_folder_id = self.env['ir.config_parameter'].sudo().get_param('auto_backup_db_gdrive.folder_id')
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

            # 4. Create "overall" folder NOT REQUIRED - removed because it was empty

            success_count = 0
            error_details = []

            for db_name in db_list:
                # Create DB-specific subfolder
                folder_result = self._create_gdrive_folder(access_token, db_name, root_folder_id)
                if not folder_result:
                    error_details.append(f"Could not create folder for {db_name}")
                    continue
                db_folder_id = folder_result.get('id')

                try:
                    # SQL Dump
                    sql_stream = io.BytesIO()
                    db.dump_db(db_name, sql_stream, backup_format='sql')
                    sql_stream.seek(0)
                    sql_uploaded = self._upload_to_gdrive(access_token, sql_stream, f"{db_name}.sql", "application/sql", db_folder_id)
                    
                    # ZIP (with filestore)
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

            # 5. Apply Retention Policy
            try:
                self._apply_retention_policy(access_token, backup_type)
            except Exception as e:
                error_details.append(f"Retention policy failed: {str(e)}")
            
            if gdrive_created_time:
                # GDrive time is typically "2026-03-09T06:44:28.000Z"
                from odoo.fields import Datetime
                try:
                    clean_time = gdrive_created_time.split('.')[0].replace('T', ' ').replace('Z', '')
                    last_run = Datetime.to_datetime(clean_time)
                except Exception:
                    last_run = fields.Datetime.now()
            else:
                last_run = fields.Datetime.now()
            
            self.gdrive_backup_last_run = last_run
            # Explicitly save to Config Parameter because this is a TransientModel
            self.env['ir.config_parameter'].sudo().set_param('auto_backup_db_gdrive.backup_last_run', last_run)
            
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
        """ Method called by the cron job to perform automated backups. """
        # We need to find the settings to get the sync scope and other params
        # Since it's a TransientModel, we use get_values or read from ICP
        ICP = self.env['ir.config_parameter'].sudo()
        if not ICP.get_param('auto_backup_db_gdrive.backup_active'):
            return
            
        # Create a dummy record to call action_manual_sync
        config = self.create({})
        config.action_manual_sync(backup_type='Auto')

    def _apply_retention_policy(self, access_token, backup_type):
        """ List folders with the same prefix and delete based on policy. """
        ICP = self.env['ir.config_parameter'].sudo()
        retention_type = ICP.get_param('auto_backup_db_gdrive.retention_type') or 'none'
        if retention_type == 'none':
            return
            
        parent_id = ICP.get_param('auto_backup_db_gdrive.folder_id')
        if not parent_id:
            return

        headers = {"Authorization": f"Bearer {access_token}"}
        # Search for folders with prefix (Auto_ or Manual_)
        query = f"name contains '{backup_type}_' and mimeType = 'application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed = false"
        params = {
            "q": query,
            "fields": "files(id, name, createdTime)",
            "orderBy": "createdTime asc" # Oldest first
        }
        
        response = requests.get("https://www.googleapis.com/drive/v3/files", headers=headers, params=params)
        if response.status_code != 200:
            return
            
        files = response.json().get('files', [])
        
        if retention_type == 'count':
            retention_count = int(ICP.get_param('auto_backup_db_gdrive.retention_count') or 10)
            if len(files) > retention_count:
                # Delete oldest files
                to_delete = files[:len(files) - retention_count]
                for f in to_delete:
                    requests.delete(f"https://www.googleapis.com/drive/v3/files/{f['id']}", headers=headers)
                    
        elif retention_type == 'days':
            retention_days = int(ICP.get_param('auto_backup_db_gdrive.retention_days') or 30)
            from datetime import timedelta, datetime
            limit_date = datetime.now() - timedelta(days=retention_days)
            
            for f in files:
                # GDrive: "2026-03-09T06:44:28.000Z"
                try:
                    f_date = datetime.strptime(f['createdTime'].split('.')[0], '%Y-%m-%dT%H:%M:%S')
                    if f_date < limit_date:
                        requests.delete(f"https://www.googleapis.com/drive/v3/files/{f['id']}", headers=headers)
                except Exception:
                    continue
