# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.http import request
import requests
import json
import io
from datetime import datetime
import pytz
from odoo.service import db
import logging

_logger = logging.getLogger(__name__)

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dropbox_app_key = fields.Char(
        string='Dropbox App Key', 
        config_parameter='auto_backup_db_dropbox.app_key'
    )
    dropbox_app_secret = fields.Char(
        string='Dropbox App Secret', 
        config_parameter='auto_backup_db_dropbox.app_secret'
    )
    dropbox_folder_path = fields.Char(
        string='Dropbox Folder Path', 
        config_parameter='auto_backup_db_dropbox.folder_path',
        default='/Odoo_Backups'
    )
    dropbox_refresh_token = fields.Char(
        string='Refresh Token', 
        config_parameter='auto_backup_db_dropbox.refresh_token',
        readonly=True
    )
    dropbox_redirect_uri = fields.Char(
        string='Redirect URI', 
        compute='_compute_dropbox_redirect_uri'
    )
    
    # Auto Backup Configuration
    dropbox_backup_active = fields.Boolean(
        string='Enable Auto Backup',
        config_parameter='auto_backup_db_dropbox.backup_active'
    )
    dropbox_backup_interval_number = fields.Integer(
        string='Backup Interval',
        config_parameter='auto_backup_db_dropbox.backup_interval_number',
        default=1
    )
    dropbox_backup_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], string='Backup Interval Unit', config_parameter='auto_backup_db_dropbox.backup_interval_type', default='days')
    
    dropbox_backup_last_run = fields.Datetime(
        string='Last Backup Run',
        config_parameter='auto_backup_db_dropbox.backup_last_run',
        readonly=True
    )

    # Retention Policy
    dropbox_retention_type = fields.Selection([
        ('none', 'Keep All'),
        ('count', 'Keep Last X Backups'),
        ('days', 'Keep for X Days'),
    ], string='Retention Policy', config_parameter='auto_backup_db_dropbox.retention_type', default='none')
    
    dropbox_retention_count = fields.Integer(
        string='Number of Backups to Keep',
        config_parameter='auto_backup_db_dropbox.retention_count',
        default=10
    )
    
    dropbox_retention_days = fields.Integer(
        string='Number of Days to Keep',
        config_parameter='auto_backup_db_dropbox.retention_days',
        default=30
    )
   
    dropbox_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Sync Scope', config_parameter='auto_backup_db_dropbox.sync_scope', default='all', required=True)

    dropbox_selected_db_ids = fields.Many2many(
        'dropbox.backup.db', 
        'dropbox_auto_db_rel',
        string='Select Databases'
    )

    # Manual Backup Sync Rules
    dropbox_manual_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Manual Sync Scope', config_parameter='auto_backup_db_dropbox.manual_sync_scope', default='all', required=True)

    dropbox_manual_selected_db_ids = fields.Many2many(
        'dropbox.backup.db', 
        'dropbox_manual_db_rel',
        string='Manual Select Databases'
    )

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        ICP = self.env['ir.config_parameter'].sudo()
        
        # Save Auto DB IDs
        auto_db_ids = self.dropbox_selected_db_ids.ids
        ICP.set_param('auto_backup_db_dropbox.selected_db_ids', json.dumps(auto_db_ids))
        
        # Save Manual DB IDs
        manual_db_ids = self.dropbox_manual_selected_db_ids.ids
        ICP.set_param('auto_backup_db_dropbox.manual_selected_db_ids', json.dumps(manual_db_ids))
        
        # Sync with Cron Job
        cron = self.env.ref('auto_backup_db_dropbox.ir_cron_auto_backup_db_dropbox', raise_if_not_found=False)
        if cron:
            vals = {
                'active': self.dropbox_backup_active,
                'interval_number': self.dropbox_backup_interval_number,
                'interval_type': self.dropbox_backup_interval_type,
            }
            if self.dropbox_backup_active:
                vals['nextcall'] = fields.Datetime.now()
            cron.write(vals)

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env['ir.config_parameter'].sudo()
        
        # Get Auto DB IDs
        auto_db_ids_str = ICP.get_param('auto_backup_db_dropbox.selected_db_ids', '[]')
        try:
            auto_db_ids = json.loads(auto_db_ids_str)
        except Exception:
            auto_db_ids = []
            
        # Get Manual DB IDs
        manual_db_ids_str = ICP.get_param('auto_backup_db_dropbox.manual_selected_db_ids', '[]')
        try:
            manual_db_ids = json.loads(manual_db_ids_str)
        except Exception:
            manual_db_ids = []

        # Filter out non-existent IDs
        if auto_db_ids:
            auto_db_ids = self.env['dropbox.backup.db'].sudo().browse(auto_db_ids).exists().ids
        if manual_db_ids:
            manual_db_ids = self.env['dropbox.backup.db'].sudo().browse(manual_db_ids).exists().ids

        res.update(
            dropbox_selected_db_ids=[(6, 0, auto_db_ids)],
            dropbox_manual_selected_db_ids=[(6, 0, manual_db_ids)],
        )
        return res

    @api.onchange('dropbox_sync_scope', 'dropbox_manual_sync_scope')
    def _onchange_dropbox_sync_scope(self):
        if self.dropbox_sync_scope in ['all', 'selective'] or self.dropbox_manual_sync_scope in ['all', 'selective']:
            try:
                available_dbs = db.list_dbs()
                existing_dbs = self.env['dropbox.backup.db'].search([])
                existing_names = existing_dbs.mapped('name')
                
                # Add new ones
                for db_name in available_dbs:
                    if db_name not in existing_names:
                        self.env['dropbox.backup.db'].create({'name': db_name})
            except Exception:
                pass

    @api.depends('dropbox_app_key')
    def _compute_dropbox_redirect_uri(self):
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
            config.dropbox_redirect_uri = f"{host_url}/dropbox_account/authentication"

    def action_dropbox_authenticate(self):
        self.ensure_one()
        if not self.dropbox_app_key:
            return False
            
        params = {
            'client_id': self.dropbox_app_key,
            'redirect_uri': self.dropbox_redirect_uri,
            'response_type': 'code',
            'token_access_type': 'offline',
        }
        url = "https://www.dropbox.com/oauth2/authorize?" + "&".join([f"{k}={v}" for k, v in params.items()])
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def action_dropbox_disconnect(self):
        self.ensure_one()
        self.dropbox_app_key = False
        self.dropbox_app_secret = False
        self.dropbox_refresh_token = False
        self.dropbox_backup_active = False
        
        ICP = self.env['ir.config_parameter'].sudo()
        params_to_clear = [
            'auto_backup_db_dropbox.app_key',
            'auto_backup_db_dropbox.app_secret',
            'auto_backup_db_dropbox.refresh_token',
            'auto_backup_db_dropbox.backup_active',
            'auto_backup_db_dropbox.selected_db_ids',
        ]
        for param in params_to_clear:
            ICP.set_param(param, False)
            
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def _get_dropbox_access_token(self):
        ICP = self.env['ir.config_parameter'].sudo()
        app_key = ICP.get_param('auto_backup_db_dropbox.app_key')
        app_secret = ICP.get_param('auto_backup_db_dropbox.app_secret')
        refresh_token = ICP.get_param('auto_backup_db_dropbox.refresh_token')
        
        if not (app_key and app_secret and refresh_token):
            return False
            
        data = {
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token',
            'client_id': app_key,
            'client_secret': app_secret,
        }
        response = requests.post("https://api.dropbox.com/oauth2/token", data=data)
        if response.status_code == 200:
            return response.json().get('access_token')
        return False

    def _upload_to_dropbox(self, access_token, file_content, dropbox_path):
        # Dropbox paths must be valid (no double slashes, etc)
        clean_path = dropbox_path.replace('//', '/')
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Dropbox-API-Arg": json.dumps({
                "path": clean_path,
                "mode": "add",
                "autorename": True,
                "mute": False,
                "strict_conflict": False
            }),
            "Content-Type": "application/octet-stream"
        }
        try:
            response = requests.post(
                "https://content.dropboxapi.com/2/files/upload",
                headers=headers,
                data=file_content,
                timeout=300 # 5 minutes timeout for large files
            )
            if response.status_code != 200:
                error_data = response.json() if response.content else {}
                _logger.error("Dropbox Upload Error: %s - %s. Path: %s. Response: %s", 
                             response.status_code, response.reason, clean_path, error_data)
            return response.status_code == 200
        except Exception as e:
            _logger.error("Dropbox Upload Exception: %s", str(e))
            return False

    def action_manual_sync(self, backup_type='Manual'):
        self.ensure_one()
        access_token = self._get_dropbox_access_token()
        if not access_token:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': _('Failed to authenticate with Dropbox. Please reconnect your account.'),
                    'type': 'danger',
                    'sticky': False,
                }
            }
            
        user_tz = self.env.user.tz or self.env.company.partner_id.tz or 'UTC'
        tz = pytz.timezone(user_tz)
        utc_now = fields.Datetime.now()
        local_dt = pytz.utc.localize(utc_now).astimezone(tz)
        timestamp = local_dt.strftime("%d-%m-%Y_%H-%M-%S")
        
        ICP = self.env['ir.config_parameter'].sudo()
        root_folder_path = ICP.get_param('auto_backup_db_dropbox.folder_path') or '/Odoo_Backups'
        if not root_folder_path.startswith('/'):
            root_folder_path = '/' + root_folder_path
        
        backup_folder_name = f"{backup_type}_{timestamp}"
        
        try:
            if backup_type == 'Auto':
                sync_scope = ICP.get_param('auto_backup_db_dropbox.sync_scope') or 'all'
                db_ids_field = 'auto_backup_db_dropbox.selected_db_ids'
            else:
                sync_scope = ICP.get_param('auto_backup_db_dropbox.manual_sync_scope') or 'all'
                db_ids_field = 'auto_backup_db_dropbox.manual_selected_db_ids'

            if sync_scope == 'selective':
                selected_db_ids_str = ICP.get_param(db_ids_field, '[]')
                selected_db_ids = json.loads(selected_db_ids_str)
                selected_dbs = self.env['dropbox.backup.db'].browse(selected_db_ids)
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

            success_count = 0
            error_details = []

            for db_name in db_list:
                try:
                    # SQL Dump
                    sql_stream = io.BytesIO()
                    db.dump_db(db_name, sql_stream, backup_format='sql')
                    sql_stream.seek(0)
                    sql_path = f"{root_folder_path}/{backup_folder_name}/{db_name}/{db_name}.sql"
                    sql_uploaded = self._upload_to_dropbox(access_token, sql_stream.read(), sql_path)
                    
                    # ZIP (with filestore)
                    zip_stream = io.BytesIO()
                    db.dump_db(db_name, zip_stream, backup_format='zip')
                    zip_stream.seek(0)
                    zip_path = f"{root_folder_path}/{backup_folder_name}/{db_name}/{db_name}.zip"
                    zip_uploaded = self._upload_to_dropbox(access_token, zip_stream.read(), zip_path)
                    
                    if sql_uploaded and zip_uploaded:
                        success_count += 1
                    else:
                        error_msg = f"Upload failed for {db_name}: "
                        if not sql_uploaded: error_msg += " SQL failed."
                        if not zip_uploaded: error_msg += " ZIP failed."
                        error_details.append(error_msg)
                except Exception as e:
                    _logger.exception("Backup failed for %s", db_name)
                    error_details.append(f"Backup failed for {db_name}: {str(e)}")

            # Apply Retention Policy
            try:
                self._apply_retention_policy(access_token)
            except Exception as e:
                error_details.append(f"Retention policy failed: {str(e)}")
            
            last_run = fields.Datetime.now()
            self.dropbox_backup_last_run = last_run
            ICP.set_param('auto_backup_db_dropbox.backup_last_run', last_run)
            
            message = _('Successfully backed up %s databases to Dropbox.') % success_count
            if error_details:
                message += "\n" + "\n".join(error_details)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Dropbox Backup Summary'),
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
        if not ICP.get_param('auto_backup_db_dropbox.backup_active'):
            return
            
        config = self.create({})
        config.action_manual_sync(backup_type='Auto')

    def _apply_retention_policy(self, access_token):
        ICP = self.env['ir.config_parameter'].sudo()
        retention_type = ICP.get_param('auto_backup_db_dropbox.retention_type')
        if not retention_type or retention_type == 'none' or retention_type == 'False':
            return

        root_folder_path = ICP.get_param('auto_backup_db_dropbox.folder_path') or '/Odoo_Backups'
        if not root_folder_path.startswith('/'):
            root_folder_path = '/' + root_folder_path
        
        # List folders in root
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        data = {"path": root_folder_path, "recursive": False, "include_media_info": False, "include_deleted": False}
        
        response = requests.post("https://api.dropboxapi.com/2/files/list_folder", headers=headers, data=json.dumps(data))
        if response.status_code != 200:
            _logger.error("Failed to list Dropbox folders: %s", response.text)
            return
            
        entries = response.json().get('entries', [])
        backup_folders = []
        for entry in entries:
            if entry.get('.tag') == 'folder' and (entry['name'].startswith('Auto_') or entry['name'].startswith('Manual_')):
                # We need the creation time. Dropbox doesn't give folder creation time easily in list_folder for all types.
                # However, our folder names have timestamps: Type_DD-MM-YYYY_HH-MM-SS
                try:
                    ts_str = entry['name'].split('_', 1)[1]
                    dt = datetime.strptime(ts_str, "%d-%m-%Y_%H-%M-%S")
                    backup_folders.append({
                        'path': entry['path_lower'],
                        'time': dt,
                        'name': entry['name']
                    })
                except Exception:
                    continue
        
        backup_folders.sort(key=lambda x: x['time']) # Oldest first

        to_delete_paths = []
        if retention_type == 'count':
            try:
                raw_count = ICP.get_param('auto_backup_db_dropbox.retention_count')
                retention_count = int(raw_count) if raw_count and raw_count != 'False' else 10
            except (ValueError, TypeError):
                retention_count = 10
                
            if len(backup_folders) > retention_count:
                to_delete = backup_folders[:len(backup_folders) - retention_count]
                to_delete_paths = [info['path'] for info in to_delete]
                    
        elif retention_type == 'days':
            try:
                raw_days = ICP.get_param('auto_backup_db_dropbox.retention_days')
                retention_days = int(raw_days) if raw_days and raw_days != 'False' else 30
            except (ValueError, TypeError):
                retention_days = 30
                
            from datetime import timedelta
            limit_date = datetime.now() - timedelta(days=retention_days)
            
            for info in backup_folders:
                if info['time'] < limit_date:
                    to_delete_paths.append(info['path'])

        if to_delete_paths:
            # Delete folders
            for path in to_delete_paths:
                delete_data = {"path": path}
                requests.post("https://api.dropboxapi.com/2/files/delete_v2", headers=headers, data=json.dumps(delete_data))
                _logger.info("Deleted Dropbox backup folder: %s", path)
