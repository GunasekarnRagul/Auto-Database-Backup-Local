# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.http import request
import json
import io
from datetime import datetime
import pytz
from odoo.service import db
import logging

try:
    import boto3
    from botocore.exceptions import NoCredentialsError, ClientError
except ImportError:
    boto3 = None

_logger = logging.getLogger(__name__)

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    s3_access_key = fields.Char(
        string='AWS Access Key ID', 
        config_parameter='auto_backup_db_aws_s3.access_key'
    )
    s3_secret_key = fields.Char(
        string='AWS Secret Access Key', 
        config_parameter='auto_backup_db_aws_s3.secret_key'
    )
    s3_bucket = fields.Char(
        string='S3 Bucket Name', 
        config_parameter='auto_backup_db_aws_s3.bucket'
    )
    s3_region = fields.Char(
        string='AWS Region', 
        config_parameter='auto_backup_db_aws_s3.region',
        default='us-east-1'
    )

    s3_is_connection_tested = fields.Boolean(
        string='S3 Connection Tested',
        default=False
    )
    
    # Auto Backup Configuration
    s3_backup_active = fields.Boolean(
        string='Enable Auto Backup',
        config_parameter='auto_backup_db_aws_s3.backup_active'
    )
    s3_backup_interval_number = fields.Integer(
        string='Backup Interval',
        config_parameter='auto_backup_db_aws_s3.backup_interval_number',
        default=1
    )
    s3_backup_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], string='Backup Interval Unit', config_parameter='auto_backup_db_aws_s3.backup_interval_type', default='days')
    
    s3_backup_last_run = fields.Datetime(
        string='Last Backup Run',
        config_parameter='auto_backup_db_aws_s3.backup_last_run',
        readonly=True
    )

    # Retention Policy
    s3_retention_type = fields.Selection([
        ('none', 'Keep All'),
        ('count', 'Keep Last X Backups'),
        ('days', 'Keep for X Days'),
    ], string='Retention Policy', config_parameter='auto_backup_db_aws_s3.retention_type', default='none')
    
    s3_retention_count = fields.Integer(
        string='Number of Backups to Keep',
        config_parameter='auto_backup_db_aws_s3.retention_count',
        default=10
    )
    
    s3_retention_days = fields.Integer(
        string='Number of Days to Keep',
        config_parameter='auto_backup_db_aws_s3.retention_days',
        default=30
    )
   
    s3_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Sync Scope', config_parameter='auto_backup_db_aws_s3.sync_scope', default='all', required=True)

    s3_selected_db_ids = fields.Many2many(
        's3.backup.db', 
        's3_auto_db_rel',
        string='Select Databases'
    )

    # Manual Backup Sync Rules
    s3_manual_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Manual Sync Scope', config_parameter='auto_backup_db_aws_s3.manual_sync_scope', default='all', required=True)

    s3_manual_selected_db_ids = fields.Many2many(
        's3.backup.db', 
        's3_manual_db_rel',
        string='Manual Select Databases'
    )

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        ICP = self.env['ir.config_parameter'].sudo()
        
        # Save Auto DB IDs
        auto_db_ids = self.s3_selected_db_ids.ids
        ICP.set_param('auto_backup_db_aws_s3.selected_db_ids', json.dumps(auto_db_ids))
        
        # Save Manual DB IDs
        manual_db_ids = self.s3_manual_selected_db_ids.ids
        ICP.set_param('auto_backup_db_aws_s3.manual_selected_db_ids', json.dumps(manual_db_ids))
        
        # Note: s3_is_connection_tested is NOT saved here on purpose.
        # It is managed exclusively by action_test_s3_connection / reset / disconnect.
        
        # Sync with Cron Job
        cron = self.env.ref('auto_backup_db_aws_s3.ir_cron_auto_backup_db_aws_s3', raise_if_not_found=False)
        if cron:
            vals = {
                'active': self.s3_backup_active,
                'interval_number': self.s3_backup_interval_number,
                'interval_type': self.s3_backup_interval_type,
            }
            # Reset nextcall so Odoo recalculates based on the new interval
            if self.s3_backup_active:
                vals['nextcall'] = fields.Datetime.now()
            cron.write(vals)

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env['ir.config_parameter'].sudo()
        
        # Get Auto DB IDs
        auto_db_ids_str = ICP.get_param('auto_backup_db_aws_s3.selected_db_ids', '[]')
        try:
            auto_db_ids = json.loads(auto_db_ids_str)
        except Exception:
            auto_db_ids = []
            
        # Get Manual DB IDs
        manual_db_ids_str = ICP.get_param('auto_backup_db_aws_s3.manual_selected_db_ids', '[]')
        try:
            manual_db_ids = json.loads(manual_db_ids_str)
        except Exception:
            manual_db_ids = []

        # Filter out non-existent IDs
        if auto_db_ids:
            auto_db_ids = self.env['s3.backup.db'].sudo().browse(auto_db_ids).exists().ids
        if manual_db_ids:
            manual_db_ids = self.env['s3.backup.db'].sudo().browse(manual_db_ids).exists().ids

        res.update(
            s3_selected_db_ids=[(6, 0, auto_db_ids)],
            s3_manual_selected_db_ids=[(6, 0, manual_db_ids)],
            s3_is_connection_tested=ICP.get_param('auto_backup_db_aws_s3.is_connection_tested') == 'True',
        )
        return res

    @api.onchange('s3_sync_scope', 's3_manual_sync_scope')
    def _onchange_s3_sync_scope(self):
        if self.s3_sync_scope in ['all', 'selective'] or self.s3_manual_sync_scope in ['all', 'selective']:
            try:
                available_dbs = db.list_dbs()
                existing_dbs = self.env['s3.backup.db'].search([])
                existing_names = existing_dbs.mapped('name')
                
                # Add new ones
                for db_name in available_dbs:
                    if db_name not in existing_names:
                        self.env['s3.backup.db'].create({'name': db_name})
            except Exception:
                pass

    # Removed onchange for credentials as it conflicts with the connection test flow.

    def action_test_s3_connection(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        try:
            # Read from ICP (saved values)
            access_key = ICP.get_param('auto_backup_db_aws_s3.access_key')
            secret_key = ICP.get_param('auto_backup_db_aws_s3.secret_key')
            region = ICP.get_param('auto_backup_db_aws_s3.region') or 'us-east-1'
            bucket_name = ICP.get_param('auto_backup_db_aws_s3.bucket')

            if not (access_key and secret_key and bucket_name):
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Incomplete Data'),
                        'message': _('Please Save your credentials first, then click Test Connection.'),
                        'type': 'warning',
                        'sticky': False,
                    }
                }

            if not boto3:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Library Missing'),
                        'message': _("boto3 library not found. Please install it using 'pip install boto3'."),
                        'type': 'danger',
                        'sticky': False,
                    }
                }

            s3_client = boto3.client(
                's3',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region
            )
            
            # Simple test: list objects with MaxKeys=1
            s3_client.list_objects_v2(Bucket=bucket_name, MaxKeys=1)
            
            # Save the status to ICP
            ICP.set_param('auto_backup_db_aws_s3.is_connection_tested', 'True')
            
            # Reopen settings to reflect the new visibility
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'res.config.settings',
                'view_mode': 'form',
                'target': 'inline',
                'context': {'module': 'auto_backup_db_aws_s3'},
            }

        except Exception as e:
            ICP.set_param('auto_backup_db_aws_s3.is_connection_tested', 'False')
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Failed'),
                    'message': _('Failed to connect to AWS S3: %s') % str(e),
                    'type': 'danger',
                    'sticky': False,
                }
            }

    def action_reset_s3_connection(self):
        self.ensure_one()
        self.env['ir.config_parameter'].sudo().set_param('auto_backup_db_aws_s3.is_connection_tested', 'False')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'target': 'inline',
            'context': {'module': 'auto_backup_db_aws_s3'},
        }

    def action_disconnect_s3(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('auto_backup_db_aws_s3.access_key', '')
        ICP.set_param('auto_backup_db_aws_s3.secret_key', '')
        ICP.set_param('auto_backup_db_aws_s3.bucket', '')
        ICP.set_param('auto_backup_db_aws_s3.is_connection_tested', 'False')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'target': 'inline',
            'context': {'module': 'auto_backup_db_aws_s3'},
        }

    def _get_s3_client(self):
        ICP = self.env['ir.config_parameter'].sudo()
        access_key = ICP.get_param('auto_backup_db_aws_s3.access_key')
        secret_key = ICP.get_param('auto_backup_db_aws_s3.secret_key')
        region = ICP.get_param('auto_backup_db_aws_s3.region') or 'us-east-1'
        
        if not (access_key and secret_key):
            return False
            
        if not boto3:
            _logger.error("boto3 library not found. Please install it using 'pip install boto3'.")
            return False
            
        try:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region
            )
            return s3_client
        except Exception as e:
            _logger.error("Failed to connect to AWS S3: %s", str(e))
            return False

    def action_manual_sync(self, backup_type='Manual'):
        self.ensure_one()
        s3_client = self._get_s3_client()
        ICP = self.env['ir.config_parameter'].sudo()
        bucket_name = ICP.get_param('auto_backup_db_aws_s3.bucket')
        
        if not s3_client:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': _('Failed to connect to AWS S3. Please check your credentials.'),
                    'type': 'danger',
                    'sticky': False,
                }
            }
            
        if not bucket_name:
             return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': _('S3 Bucket Name is not configured.'),
                    'type': 'danger',
                    'sticky': False,
                }
            }

        # Timestamp for folder/prefix
        user_tz = self.env.user.tz or self.env.company.partner_id.tz or 'UTC'
        tz = pytz.timezone(user_tz)
        utc_now = fields.Datetime.now()
        local_dt = pytz.utc.localize(utc_now).astimezone(tz)
        timestamp = local_dt.strftime("%d-%m-%Y_%H-%M-%S")
        root_prefix = f"{backup_type}_{timestamp}"
        
        try:
            if backup_type == 'Auto':
                sync_scope = ICP.get_param('auto_backup_db_aws_s3.sync_scope') or 'all'
                db_ids_field = 'auto_backup_db_aws_s3.selected_db_ids'
            else:
                sync_scope = ICP.get_param('auto_backup_db_aws_s3.manual_sync_scope') or 'all'
                db_ids_field = 'auto_backup_db_aws_s3.manual_selected_db_ids'

            if sync_scope == 'selective':
                selected_db_ids_str = ICP.get_param(db_ids_field, '[]')
                selected_db_ids = json.loads(selected_db_ids_str)
                selected_dbs = self.env['s3.backup.db'].browse(selected_db_ids)
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
                    s3_client.put_object(
                        Bucket=bucket_name,
                        Key=f"{root_prefix}/{db_name}/{db_name}.sql",
                        Body=sql_stream.read()
                    )
                    
                    # ZIP (with filestore)
                    zip_stream = io.BytesIO()
                    db.dump_db(db_name, zip_stream, backup_format='zip')
                    zip_stream.seek(0)
                    s3_client.put_object(
                        Bucket=bucket_name,
                        Key=f"{root_prefix}/{db_name}/{db_name}.zip",
                        Body=zip_stream.read()
                    )
                    
                    success_count += 1
                except Exception as e:
                    error_details.append(f"Backup failed for {db_name}: {str(e)}")

            # Apply Retention Policy
            try:
                self._apply_retention_policy(s3_client, bucket_name)
            except Exception as e:
                error_details.append(f"Retention policy failed: {str(e)}")
            
            last_run = fields.Datetime.now()
            self.s3_backup_last_run = last_run
            ICP.set_param('auto_backup_db_aws_s3.backup_last_run', last_run)
            
            message = _('Successfully backed up %s databases to AWS S3.') % success_count
            if error_details:
                message += "\n" + "\n".join(error_details)

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('S3 Backup Summary'),
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
        if ICP.get_param('auto_backup_db_aws_s3.backup_active') != 'True':
            return
            
        config = self.create({})
        config.action_manual_sync(backup_type='Auto')

    def _apply_retention_policy(self, s3_client, bucket_name):
        ICP = self.env['ir.config_parameter'].sudo()
        retention_type = ICP.get_param('auto_backup_db_aws_s3.retention_type')
        
        if not retention_type or retention_type == 'none' or retention_type == 'False':
            return

        # S3 listing doesn't have "folders" in the same way, but we can list objects with a common prefix pattern
        # Our prefixes are "Manual_DD-MM-YYYY" or "Auto_DD-MM-YYYY"
        # We need to find all unique prefixes
        
        paginator = s3_client.get_paginator('list_objects_v2')
        prefixes = set()
        for page in paginator.paginate(Bucket=bucket_name):
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    if '/' in key:
                        prefix = key.split('/')[0]
                        if prefix.startswith(('Auto_', 'Manual_')):
                            # Get the creation time of the prefix (using the oldest object's creation time)
                            prefixes.add(prefix)

        if not prefixes:
            return

        # Get creation info for each prefix
        prefix_info = []
        for prefix in prefixes:
            # We assume the prefix itself represents a backup set. 
            # We'll use the oldest object in that prefix to determine "creation time"
            response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix, MaxKeys=1)
            if 'Contents' in response:
                creation_time = response['Contents'][0]['LastModified']
                prefix_info.append({
                    'prefix': prefix,
                    'time': creation_time
                })

        prefix_info.sort(key=lambda x: x['time']) # Oldest first

        if retention_type == 'count':
            try:
                raw_count = ICP.get_param('auto_backup_db_aws_s3.retention_count')
                retention_count = int(raw_count) if raw_count and raw_count != 'False' else 10
            except (ValueError, TypeError):
                retention_count = 10
                
            if len(prefix_info) > retention_count:
                to_delete = prefix_info[:len(prefix_info) - retention_count]
                for info in to_delete:
                    self._delete_s3_prefix(s3_client, bucket_name, info['prefix'])
                    
        elif retention_type == 'days':
            try:
                raw_days = ICP.get_param('auto_backup_db_aws_s3.retention_days')
                retention_days = int(raw_days) if raw_days and raw_days != 'False' else 30
            except (ValueError, TypeError):
                retention_days = 30
                
            from datetime import timedelta, timezone
            limit_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
            
            for info in prefix_info:
                if info['time'] < limit_date:
                    self._delete_s3_prefix(s3_client, bucket_name, info['prefix'])

    def _delete_s3_prefix(self, s3_client, bucket_name, prefix):
        """ Delete all objects with the given prefix. """
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            if 'Contents' in page:
                delete_keys = [{'Key': obj['Key']} for obj in page['Contents']]
                s3_client.delete_objects(Bucket=bucket_name, Delete={'Objects': delete_keys})
        _logger.info("Deleted S3 backup prefix: %s", prefix)
