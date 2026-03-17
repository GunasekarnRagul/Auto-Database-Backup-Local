# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.http import request
import requests
import json
import io
import logging
from datetime import datetime
import pytz
from odoo.service import db
import xml.etree.ElementTree as ET

_logger = logging.getLogger(__name__)

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nextcloud_url = fields.Char(
        string='Nextcloud URL', 
        config_parameter='auto_backup_db_nextcloud.url'
    )
    nextcloud_username = fields.Char(
        string='Nextcloud Username', 
        config_parameter='auto_backup_db_nextcloud.username'
    )
    nextcloud_password = fields.Char(
        string='App Password', 
        config_parameter='auto_backup_db_nextcloud.password'
    )
    nextcloud_folder_path = fields.Char(
        string='Nextcloud Folder Path', 
        config_parameter='auto_backup_db_nextcloud.folder_path',
        default='/Odoo_Backups'
    )
    
    # Internal flag to match Dropbox's "authorized" state
    nextcloud_is_authorized = fields.Boolean(
        string='Nextcloud Authorized',
        config_parameter='auto_backup_db_nextcloud.is_authorized',
        readonly=True
    )
    
    # Auto Backup Configuration
    nextcloud_backup_active = fields.Boolean(
        string='Enable Auto Backup',
        config_parameter='auto_backup_db_nextcloud.backup_active'
    )
    nextcloud_backup_interval_number = fields.Integer(
        string='Backup Interval',
        config_parameter='auto_backup_db_nextcloud.backup_interval_number',
        default=1
    )
    nextcloud_backup_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], string='Backup Interval Unit', config_parameter='auto_backup_db_nextcloud.backup_interval_type', default='days')
    
    nextcloud_backup_last_run = fields.Datetime(
        string='Last Backup Run',
        config_parameter='auto_backup_db_nextcloud.backup_last_run',
        readonly=True
    )

    # Retention Policy
    nextcloud_retention_type = fields.Selection([
        ('none', 'Keep All'),
        ('count', 'Keep Last X Backups'),
        ('days', 'Keep for X Days'),
    ], string='Retention Policy', config_parameter='auto_backup_db_nextcloud.retention_type', default='none')
    
    nextcloud_retention_count = fields.Integer(
        string='Number of Backups to Keep',
        config_parameter='auto_backup_db_nextcloud.retention_count',
        default=10
    )
    
    nextcloud_retention_days = fields.Integer(
        string='Number of Days to Keep',
        config_parameter='auto_backup_db_nextcloud.retention_days',
        default=30
    )
   
    nextcloud_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Sync Scope', config_parameter='auto_backup_db_nextcloud.sync_scope', default='all', required=True)

    nextcloud_selected_db_ids = fields.Many2many(
        'nextcloud.backup.db', 
        'nextcloud_auto_db_rel',
        string='Select Databases'
    )

    # Manual Backup Sync Rules
    nextcloud_manual_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Manual Sync Scope', config_parameter='auto_backup_db_nextcloud.manual_sync_scope', default='all', required=True)

    nextcloud_manual_selected_db_ids = fields.Many2many(
        'nextcloud.backup.db', 
        'nextcloud_manual_db_rel',
        string='Manual Select Databases'
    )

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        ICP = self.env['ir.config_parameter'].sudo()
        
        # Save Auto DB IDs
        auto_db_ids = self.nextcloud_selected_db_ids.ids
        ICP.set_param('auto_backup_db_nextcloud.selected_db_ids', json.dumps(auto_db_ids))
        
        # Save Manual DB IDs
        manual_db_ids = self.nextcloud_manual_selected_db_ids.ids
        ICP.set_param('auto_backup_db_nextcloud.manual_selected_db_ids', json.dumps(manual_db_ids))

        # Save Authorization State
        ICP.set_param('auto_backup_db_nextcloud.is_authorized', self.nextcloud_is_authorized)
        
        # Sync with Cron Job
        cron = self.env.ref('auto_backup_db_nextcloud.ir_cron_auto_backup_db_nextcloud', raise_if_not_found=False)
        if cron:
            vals = {
                'active': self.nextcloud_backup_active,
                'interval_number': self.nextcloud_backup_interval_number,
                'interval_type': self.nextcloud_backup_interval_type,
            }
            if self.nextcloud_backup_active:
                vals['nextcall'] = fields.Datetime.now()
            cron.write(vals)

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env['ir.config_parameter'].sudo()
        
        # Get Auto DB IDs
        auto_db_ids_str = ICP.get_param('auto_backup_db_nextcloud.selected_db_ids', '[]')
        try:
            auto_db_ids = json.loads(auto_db_ids_str)
        except Exception:
            auto_db_ids = []
            
        # Get Manual DB IDs
        manual_db_ids_str = ICP.get_param('auto_backup_db_nextcloud.manual_selected_db_ids', '[]')
        try:
            manual_db_ids = json.loads(manual_db_ids_str)
        except Exception:
            manual_db_ids = []

        # Filter out non-existent IDs
        if auto_db_ids:
            auto_db_ids = self.env['nextcloud.backup.db'].sudo().browse(auto_db_ids).exists().ids
        if manual_db_ids:
            manual_db_ids = self.env['nextcloud.backup.db'].sudo().browse(manual_db_ids).exists().ids

        res.update(
            nextcloud_selected_db_ids=[(6, 0, auto_db_ids)],
            nextcloud_manual_selected_db_ids=[(6, 0, manual_db_ids)],
            nextcloud_is_authorized=ICP.get_param('auto_backup_db_nextcloud.is_authorized', False) == 'True',
        )
        return res

    @api.onchange('nextcloud_sync_scope', 'nextcloud_manual_sync_scope')
    def _onchange_nextcloud_sync_scope(self):
        if self.nextcloud_sync_scope in ['all', 'selective'] or self.nextcloud_manual_sync_scope in ['all', 'selective']:
            try:
                available_dbs = db.list_dbs()
                existing_dbs = self.env['nextcloud.backup.db'].search([])
                existing_names = existing_dbs.mapped('name')
                
                # Add new ones
                for db_name in available_dbs:
                    if db_name not in existing_names:
                        self.env['nextcloud.backup.db'].create({'name': db_name})
            except Exception:
                pass

    def action_nextcloud_authenticate(self):
        """ Test and 'Authorize' Nextcloud connection """
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
                ICP.set_param('auto_backup_db_nextcloud.is_authorized', True)
                self.nextcloud_is_authorized = True
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Successfully connected and authorized Nextcloud.'),
                        'type': 'success',
                        'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                    }
                }
            else:
                ICP.set_param('auto_backup_db_nextcloud.is_authorized', False)
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
            ICP.set_param('auto_backup_db_nextcloud.is_authorized', False)
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
            'auto_backup_db_nextcloud.url',
            'auto_backup_db_nextcloud.username',
            'auto_backup_db_nextcloud.password',
            'auto_backup_db_nextcloud.is_authorized',
            'auto_backup_db_nextcloud.backup_active',
        ]
        for p in params:
            ICP.set_param(p, False)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _get_webdav_auth(self):
        ICP = self.env['ir.config_parameter'].sudo()
        url = ICP.get_param('auto_backup_db_nextcloud.url')
        user = ICP.get_param('auto_backup_db_nextcloud.username')
        pwd = ICP.get_param('auto_backup_db_nextcloud.password')
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
            # Use stream-based upload by passing the file-like object directly to data
            if hasattr(content, 'seek'):
                content.seek(0)
            response = requests.put(target_url, auth=(user, pwd), data=content, timeout=600)
            if response.status_code not in [200, 201, 204]:
                _logger.error("Nextcloud Upload Failed for %s. Status: %s. Response: %s", 
                             file_name, response.status_code, response.text)
            return response.status_code in [200, 201, 204]
        except Exception as e:
            _logger.error("Nextcloud Upload Error for %s: %s", file_name, str(e))
            return False

    def action_manual_sync(self, backup_type='Manual'):
        self.ensure_one()
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

        user_tz = self.env.user.tz or self.env.company.partner_id.tz or 'UTC'
        tz = pytz.timezone(user_tz)
        utc_now = fields.Datetime.now()
        local_dt = pytz.utc.localize(utc_now).astimezone(tz)
        timestamp = local_dt.strftime("%d-%m-%Y_%H-%M-%S")
        
        ICP = self.env['ir.config_parameter'].sudo()
        root_path = ICP.get_param('auto_backup_db_nextcloud.folder_path') or '/Odoo_Backups'
        backup_folder = f"{backup_type}_{timestamp}"
        
        try:
            if backup_type == 'Auto':
                sync_scope = ICP.get_param('auto_backup_db_nextcloud.sync_scope') or 'all'
                db_ids_field = 'auto_backup_db_nextcloud.selected_db_ids'
            else:
                sync_scope = ICP.get_param('auto_backup_db_nextcloud.manual_sync_scope') or 'all'
                db_ids_field = 'auto_backup_db_nextcloud.manual_selected_db_ids'

            if sync_scope == 'selective':
                selected_db_ids_str = ICP.get_param(db_ids_field, '[]')
                selected_db_ids = json.loads(selected_db_ids_str)
                selected_dbs = self.env['nextcloud.backup.db'].browse(selected_db_ids)
                db_list = selected_dbs.mapped('name')
            else:
                db_list = db.list_dbs()

            success_count = 0
            error_details = []

            for db_name in db_list:
                try:
                    dest_folder = f"{root_path}/{backup_folder}/{db_name}"
                    
                    # SQL Dump
                    sql_stream = io.BytesIO()
                    db.dump_db(db_name, sql_stream, backup_format='sql')
                    sql_stream.seek(0)
                    sql_uploaded = self._upload_to_nextcloud(url, user, pwd, dest_folder, f"{db_name}.sql", sql_stream)

                    # ZIP (with filestore)
                    zip_stream = io.BytesIO()
                    db.dump_db(db_name, zip_stream, backup_format='zip')
                    zip_stream.seek(0)
                    zip_uploaded = self._upload_to_nextcloud(url, user, pwd, dest_folder, f"{db_name}.zip", zip_stream)
                    
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
                self._apply_retention_policy(url, user, pwd)
            except Exception as e:
                _logger.error("Retention policy error: %s", str(e))
            
            last_run = fields.Datetime.now()
            self.nextcloud_backup_last_run = last_run
            ICP.set_param('auto_backup_db_nextcloud.backup_last_run', last_run)
            
            msg = _('Successfully backed up %s databases to Nextcloud.') % success_count
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Backup Summary'),
                    'message': msg + ("\n" + "\n".join(error_details) if error_details else ""),
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
                    'message': str(e),
                    'type': 'danger',
                }
            }

    @api.model
    def _cron_auto_backup(self):
        ICP = self.env['ir.config_parameter'].sudo()
        if not ICP.get_param('auto_backup_db_nextcloud.backup_active'):
            return
        config = self.sudo().create({})
        config.action_manual_sync(backup_type='Auto')

    def _apply_retention_policy(self, url, user, pwd):
        ICP = self.env['ir.config_parameter'].sudo()
        retention_type = ICP.get_param('auto_backup_db_nextcloud.retention_type')
        if not retention_type or retention_type == 'none' or retention_type == 'False':
            return

        root_path = ICP.get_param('auto_backup_db_nextcloud.folder_path') or '/Odoo_Backups'
        webdav_url = f"{url.rstrip('/')}/remote.php/dav/files/{user}/{root_path.strip('/')}/"
        
        try:
            # Depth 1 to get direct children of Odoo_Backups
            headers = {'Depth': '1'}
            response = requests.request("PROPFIND", webdav_url, auth=(user, pwd), headers=headers)
            if response.status_code not in [200, 207]:
                _logger.error("Failed to list backups for retention: %s", response.status_code)
                return

            tree = ET.fromstring(response.content)
            namespaces = {'d': 'DAV:'}
            backups = []
            
            # Skip the first response as it is the folder itself
            for resp in tree.findall('d:response', namespaces)[1:]:
                href = resp.find('d:href', namespaces).text
                # href might be URL encoded, and might contain the full path
                # We need the last part of the path
                name = href.rstrip('/').split('/')[-1]
                
                # We expect folder names like Auto_DD-MM-YYYY_HH-MM-SS or Manual_...
                if name.startswith(('Auto_', 'Manual_')):
                    try:
                        ts_str = name.split('_', 1)[1]
                        # Correct format is %d-%m-%Y_%H-%M-%S
                        dt = datetime.strptime(ts_str, "%d-%m-%Y_%H-%M-%S")
                        backups.append({'href': href, 'time': dt, 'name': name})
                    except Exception as e:
                        _logger.debug("Skipping folder %s: %s", name, str(e))
                        continue
            
            if not backups:
                return

            backups.sort(key=lambda x: x['time']) # Oldest first

            to_delete = []
            if retention_type == 'count':
                try:
                    count_val = ICP.get_param('auto_backup_db_nextcloud.retention_count', '10')
                    count = int(count_val) if count_val else 10
                except (ValueError, TypeError):
                    count = 10
                    
                if len(backups) > count:
                    to_delete = backups[:len(backups) - count]
                    
            elif retention_type == 'days':
                try:
                    days_val = ICP.get_param('auto_backup_db_nextcloud.retention_days', '30')
                    days = int(days_val) if days_val else 30
                except (ValueError, TypeError):
                    days = 30
                    
                from datetime import timedelta
                # Use naive datetime for comparison if datetime.strptime returns naive
                limit = datetime.now() - timedelta(days=days)
                to_delete = [b for b in backups if b['time'] < limit]

            for b in to_delete:
                # Ensure del_url is correctly formed
                # if b['href'] is an absolute path from the root of the server
                # del_url = f"{url.rstrip('/')}{b['href']}"
                # Sometimes href is already a full URL or just a path. 
                # PROPFIND href is usually /remote.php/dav/files/user/path
                
                if b['href'].startswith('http'):
                    del_url = b['href']
                else:
                    # Construct full URL if it's just a path
                    del_url = f"{url.rstrip('/')}{b['href']}"
                
                res = requests.delete(del_url, auth=(user, pwd))
                if res.status_code in [200, 204]:
                    _logger.info("Deleted Nextcloud backup: %s", b['name'])
                else:
                    _logger.error("Failed to delete Nextcloud backup %s: %s", b['name'], res.status_code)
        except Exception as e:
            _logger.error("Retention policy error: %s", str(e))
