# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
import io
import logging

try:
    import boto3
    from botocore.exceptions import NoCredentialsError, ClientError
except ImportError:
    boto3 = None

_logger = logging.getLogger(__name__)


class ResConfigSettingsAwsS3(models.TransientModel):
    _inherit = 'res.config.settings'

    # ─── AWS S3 Credentials ─────────────────────────────────────────
    s3_access_key = fields.Char(
        string='AWS Access Key ID',
        config_parameter='auto_backup_db_cloud.s3_access_key'
    )
    s3_secret_key = fields.Char(
        string='AWS Secret Access Key',
        config_parameter='auto_backup_db_cloud.s3_secret_key'
    )
    s3_bucket = fields.Char(
        string='S3 Bucket Name',
        config_parameter='auto_backup_db_cloud.s3_bucket'
    )
    s3_region = fields.Char(
        string='AWS Region',
        config_parameter='auto_backup_db_cloud.s3_region',
        default='us-east-1'
    )
    s3_is_connection_tested = fields.Boolean(
        string='S3 Connection Tested',
        default=False
    )

    # ─── AWS S3 Backup Settings ─────────────────────────────────────
    s3_backup_active = fields.Boolean(
        string='Enable S3 Auto Backup',
        config_parameter='auto_backup_db_cloud.s3_backup_active'
    )
    s3_backup_interval_number = fields.Integer(
        string='Backup Interval',
        config_parameter='auto_backup_db_cloud.s3_backup_interval_number',
        default=1
    )
    s3_backup_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], string='Backup Interval Unit',
        config_parameter='auto_backup_db_cloud.s3_backup_interval_type',
        default='days')

    s3_backup_last_run = fields.Datetime(
        string='Last S3 Backup Run',
        config_parameter='auto_backup_db_cloud.s3_backup_last_run',
        readonly=True
    )

    s3_retention_type = fields.Selection([
        ('none', 'Keep All'),
        ('count', 'Keep Last X Backups'),
        ('days', 'Keep for X Days'),
    ], string='Retention Policy',
        config_parameter='auto_backup_db_cloud.s3_retention_type',
        default='none')

    s3_retention_count = fields.Integer(
        string='Number of Backups to Keep',
        config_parameter='auto_backup_db_cloud.s3_retention_count',
        default=10
    )

    s3_retention_days = fields.Integer(
        string='Number of Days to Keep',
        config_parameter='auto_backup_db_cloud.s3_retention_days',
        default=30
    )

    s3_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Sync Scope',
        config_parameter='auto_backup_db_cloud.s3_sync_scope',
        default='all', required=True)

    s3_selected_db_ids = fields.Many2many(
        'cloud.backup.db',
        'cloud_s3_auto_db_rel',
        string='Select Databases'
    )

    s3_manual_sync_scope = fields.Selection([
        ('all', 'All Databases (Overall Sync)'),
        ('selective', 'Selected Databases'),
    ], string='Manual Sync Scope',
        config_parameter='auto_backup_db_cloud.s3_manual_sync_scope',
        default='all', required=True)

    s3_manual_selected_db_ids = fields.Many2many(
        'cloud.backup.db',
        'cloud_s3_manual_db_rel',
        string='Manual Select Databases'
    )

    # ═══════════════════════════════════════════════════════════════
    # Connection Management
    # ═══════════════════════════════════════════════════════════════

    def action_test_s3_connection(self):
        self.ensure_one()
        try:
            # Read from self (current form values) to allow testing before saving
            access_key = self.s3_access_key
            secret_key = self.s3_secret_key
            region = self.s3_region or 'us-east-1'
            bucket_name = self.s3_bucket

            if not (access_key and secret_key and bucket_name):
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Incomplete Data'),
                        'message': _('Please enter Access Key, Secret Key, and Bucket Name first.'),
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
            s3_client.list_objects_v2(Bucket=bucket_name, MaxKeys=1)

            # Save credentials AND connection status to ICP
            ICP = self.env['ir.config_parameter'].sudo()
            ICP.set_param('auto_backup_db_cloud.s3_access_key', access_key)
            ICP.set_param('auto_backup_db_cloud.s3_secret_key', secret_key)
            ICP.set_param('auto_backup_db_cloud.s3_region', region)
            ICP.set_param('auto_backup_db_cloud.s3_bucket', bucket_name)
            ICP.set_param('auto_backup_db_cloud.s3_is_connection_tested', 'True')

            # Show success notification and reload settings
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connected Successfully'),
                    'message': _('AWS S3 connection established. Your credentials have been saved.'),
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                }
            }

        except Exception as e:
            self.env['ir.config_parameter'].sudo().set_param('auto_backup_db_cloud.s3_is_connection_tested', 'False')
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
        self.env['ir.config_parameter'].sudo().set_param('auto_backup_db_cloud.s3_is_connection_tested', 'False')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'target': 'inline',
            'context': {'module': 'auto_backup_db_cloud'},
        }

    def action_disconnect_s3(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('auto_backup_db_cloud.s3_access_key', '')
        ICP.set_param('auto_backup_db_cloud.s3_secret_key', '')
        ICP.set_param('auto_backup_db_cloud.s3_bucket', '')
        ICP.set_param('auto_backup_db_cloud.s3_is_connection_tested', 'False')
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'target': 'inline',
            'context': {'module': 'auto_backup_db_cloud'},
        }

    # ═══════════════════════════════════════════════════════════════
    # S3 Backup Logic
    # ═══════════════════════════════════════════════════════════════

    def _get_s3_client(self):
        ICP = self.env['ir.config_parameter'].sudo()
        access_key = ICP.get_param('auto_backup_db_cloud.s3_access_key')
        secret_key = ICP.get_param('auto_backup_db_cloud.s3_secret_key')
        region = ICP.get_param('auto_backup_db_cloud.s3_region') or 'us-east-1'

        if not (access_key and secret_key):
            return False

        if not boto3:
            _logger.error("boto3 library not found. Please install it using 'pip install boto3'.")
            return False

        try:
            return boto3.client(
                's3',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region
            )
        except Exception as e:
            _logger.error("Failed to connect to AWS S3: %s", str(e))
            return False

    def _s3_manual_sync(self, backup_type='Manual'):
        self.ensure_one()
        from odoo.service import db

        s3_client = self._get_s3_client()
        ICP = self.env['ir.config_parameter'].sudo()
        bucket_name = ICP.get_param('auto_backup_db_cloud.s3_bucket')

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

        timestamp = self._get_backup_timestamp()
        root_prefix = f"{backup_type}_{timestamp}"

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
                    s3_client.put_object(
                        Bucket=bucket_name,
                        Key=f"{root_prefix}/{db_name}/{db_name}.sql",
                        Body=sql_stream.read()
                    )

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

            try:
                self._apply_s3_retention_policy(s3_client, bucket_name)
            except Exception as e:
                error_details.append(f"Retention policy failed: {str(e)}")

            self._save_backup_last_run()
            return self._build_summary_notification('AWS S3', success_count, error_details)

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

    def _apply_s3_retention_policy(self, s3_client, bucket_name):
        ICP = self.env['ir.config_parameter'].sudo()
        retention_type = ICP.get_param('auto_backup_db_cloud.s3_retention_type')

        if not retention_type or retention_type == 'none' or retention_type == 'False':
            return

        paginator = s3_client.get_paginator('list_objects_v2')
        prefixes = set()
        for page in paginator.paginate(Bucket=bucket_name):
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    if '/' in key:
                        prefix = key.split('/')[0]
                        if prefix.startswith(('Auto_', 'Manual_')):
                            prefixes.add(prefix)

        if not prefixes:
            return

        prefix_info = []
        for prefix in prefixes:
            response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix, MaxKeys=1)
            if 'Contents' in response:
                creation_time = response['Contents'][0]['LastModified']
                prefix_info.append({'prefix': prefix, 'time': creation_time})

        prefix_info.sort(key=lambda x: x['time'])

        if retention_type == 'count':
            try:
                raw_count = ICP.get_param('auto_backup_db_cloud.s3_retention_count')
                retention_count = int(raw_count) if raw_count and raw_count != 'False' else 10
            except (ValueError, TypeError):
                retention_count = 10

            if len(prefix_info) > retention_count:
                to_delete = prefix_info[:len(prefix_info) - retention_count]
                for info in to_delete:
                    self._delete_s3_prefix(s3_client, bucket_name, info['prefix'])

        elif retention_type == 'days':
            try:
                raw_days = ICP.get_param('auto_backup_db_cloud.s3_retention_days')
                retention_days = int(raw_days) if raw_days and raw_days != 'False' else 30
            except (ValueError, TypeError):
                retention_days = 30

            from datetime import timedelta, timezone, datetime
            limit_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

            for info in prefix_info:
                if info['time'] < limit_date:
                    self._delete_s3_prefix(s3_client, bucket_name, info['prefix'])

    def _delete_s3_prefix(self, s3_client, bucket_name, prefix):
        """Helper to recursively delete all objects with a given prefix."""
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            if 'Contents' in page:
                delete_keys = [{'Key': obj['Key']} for obj in page['Contents']]
                s3_client.delete_objects(Bucket=bucket_name, Delete={'Objects': delete_keys})
        _logger.info("Deleted S3 backup prefix: %s", prefix)
