# -*- coding: utf-8 -*-
"""
AWS S3 Sync Engine — adapted from aws_odoo_integration_cloudaddons
Uses boto3 for all S3 operations. Config model: cloud.provider.config (provider_type='aws').
"""
import logging
import mimetypes
import posixpath
import urllib.parse
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class CloudSyncAws(models.AbstractModel):
    _name = 'cloud.sync.aws'
    _description = 'Cloud Sync — AWS S3 Engine'

    # ─── Boto3 helpers ──────────────────────────────────────────────────────────

    def _get_s3_client(self, config):
        import boto3
        return boto3.client(
            's3',
            aws_access_key_id=(config.aws_access_key_id or '').strip(),
            aws_secret_access_key=(config.aws_secret_access_key or '').strip(),
            region_name=(config.aws_region or '').strip(),
        )

    def _s3_url(self, config, remote_path):
        bucket = (config.aws_s3_bucket_name or '').strip()
        region = (config.aws_region or '').strip()
        path   = str(remote_path).lstrip('/')
        return f'https://{bucket}.s3.{region}.amazonaws.com/{path}'

    def _list_entries(self, config, remote_path='/'):
        """List objects and pseudo-folders directly under remote_path."""
        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or '').strip()
        prefix = remote_path.strip('/') + '/' if remote_path.strip('/') else ''
        entries = []
        try:
            pager = s3.get_paginator('list_objects_v2')
            for page in pager.paginate(Bucket=bucket, Prefix=prefix, Delimiter='/'):
                for cp in page.get('CommonPrefixes', []):
                    fp = cp['Prefix'].strip('/')
                    name = fp.split('/')[-1]
                    rp = '/' + fp
                    entries.append({'name': name, 'remote_path': rp, 'is_folder': True, 'size': 0, 'etag': ''})
                for obj in page.get('Contents', []):
                    if obj['Key'].endswith('/'):
                        continue
                    fp = obj['Key'].strip('/')
                    if fp == prefix.strip('/'):
                        continue
                    name = fp.split('/')[-1]
                    rp = '/' + fp
                    entries.append({'name': name, 'remote_path': rp, 'is_folder': False,
                                    'size': obj['Size'], 'etag': obj['ETag'].strip('"'),
                                    'last_modified': str(obj['LastModified'])})
        except Exception as e:
            _logger.error('S3 list error at %s: %s', remote_path, e)
        return entries

    def _mkdir(self, config, remote_path):
        """Create a folder marker in S3 (idempotent)."""
        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or '').strip()
        key = remote_path.strip('/') + '/'
        s3.put_object(Bucket=bucket, Key=key, Body=b'')

    # ─── Interface ──────────────────────────────────────────────────────────────

    def find_or_create_folder(self, folder_name, parent_path, config):
        parent = (parent_path or '/').rstrip('/')
        if not parent.startswith('/'):
            parent = '/' + parent
        computed = parent.rstrip('/') + '/' + folder_name
        try:
            self._mkdir(config, computed)
        except Exception as e:
            raise Exception(f'AWS S3 folder creation failed: {e}')
        return {'remote_id': computed, 'name': folder_name, 'cloud_url': self._s3_url(config, computed)}

    def upload_file(self, cloud_file_rec, config):
        """Push a cloud.file (pending) record to S3 using its linked ir.attachment."""
        att = self.env['ir.attachment'].sudo().search(
            [('cloud_file_id', '=', cloud_file_rec.id)], limit=1
        )
        if not att:
            return
        file_data = att.raw or (att.datas and att.datas.encode('utf-8'))
        if not file_data:
            return
        remote_path = self._build_remote_path(cloud_file_rec)
        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or '').strip()
        key = remote_path.strip('/')
        mime = att.mimetype or 'application/octet-stream'
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=file_data, ContentType=mime)
            cloud_file_rec.sudo().write({
                'cloud_file_id': remote_path,
                'cloud_url': self._s3_url(config, remote_path),
                'sync_state': 'synced',
            })
            att.sudo().with_context(sync_type='skip').write({'cloud_file_id': cloud_file_rec.id, 'type': 'url',
                                                              'url': self._s3_url(config, remote_path)})
        except Exception as e:
            cloud_file_rec.sudo().write({'sync_state': 'error', 'sync_error_msg': str(e)})
            raise

    def upload_attachment(self, attachment, config_rec):
        """Upload ir.attachment directly (called from auto-sync hook)."""
        import base64
        provider = config_rec.cloud_provider_id
        folder   = config_rec.cloud_folder_id
        file_data = attachment.raw or (attachment.datas and base64.b64decode(attachment.datas))
        if not file_data:
            return
        mime = attachment.mimetype or 'application/octet-stream'
        folder_path = folder.cloud_file_id if folder else ''
        remote_path = (folder_path.rstrip('/') + '/' + attachment.name).replace('//', '/')

        s3 = self._get_s3_client(provider)
        bucket = (provider.aws_s3_bucket_name or '').strip()
        key = remote_path.strip('/')
        s3.put_object(Bucket=bucket, Key=key, Body=file_data, ContentType=mime)
        url = self._s3_url(provider, remote_path)

        # Upsert cloud.file
        cf = self.env['cloud.file'].sudo().search([
            ('cloud_file_id', '=', remote_path), ('drive_config_id', '=', provider.id)
        ], limit=1)
        if not cf:
            cf = self.env['cloud.file'].sudo().create({
                'name': attachment.name,
                'file_type': 'file',
                'drive_config_id': provider.id,
                'cloud_file_id': remote_path,
                'cloud_url': url,
                'root_folder_id': folder.root_folder_id.id if folder and folder.root_folder_id else False,
                'parent_folder_id': folder.id if folder else False,
                'mime_type': mime,
                'file_size': len(file_data),
                'sync_state': 'synced',
                'res_model': attachment.res_model,
                'res_id': attachment.res_id,
            })
        else:
            cf.sudo().write({'sync_state': 'synced', 'cloud_url': url})

        attachment.sudo().with_context(sync_type='skip').write({
            'cloud_file_id': cf.id,
            'type': 'url' if config_rec.storage_mode == 'drive' else attachment.type,
            'url': url if config_rec.storage_mode == 'drive' else attachment.url,
        })

        # Log
        self.env['cloud.sync.log'].sudo().log_operation(
            config=provider, file_name=attachment.name, operation='upload', state='success',
            sync_type=self.env.context.get('sync_type', 'auto'),
            cloud_file_id=remote_path, file_size=len(file_data),
        )

    def _build_remote_path(self, cloud_file_rec):
        parts = []
        if cloud_file_rec.root_folder_id:
            parts.append(cloud_file_rec.root_folder_id.name)
        parent = cloud_file_rec.parent_folder_id
        ancestors = []
        while parent:
            ancestors.insert(0, parent.name)
            parent = parent.parent_folder_id
        parts.extend(ancestors)
        parts.append(cloud_file_rec.name)
        return '/' + '/'.join(parts)

    def _sync_config_files(self, config, root_folder_id=None, remote_parent_id=None,
                           parent_local_id=None, depth=0, max_depth=8):
        """Recursively pull S3 into cloud.file records."""
        if depth > max_depth or not config.is_connected:
            return
        remote_path = remote_parent_id or '/'
        entries = self._list_entries(config, remote_path)
        for entry in entries:
            self._upsert_file_record(entry, config, root_folder_id, parent_local_id)
        # Recurse
        subfolders = self.env['cloud.file'].sudo().search([
            ('root_folder_id', '=', root_folder_id),
            ('parent_folder_id', '=', parent_local_id or False),
            ('file_type', '=', 'folder'),
        ])
        for sf in subfolders:
            if sf.cloud_file_id:
                self._sync_config_files(config, root_folder_id=root_folder_id,
                                        remote_parent_id=sf.cloud_file_id,
                                        parent_local_id=sf.id,
                                        depth=depth + 1, max_depth=max_depth)

    def _upsert_file_record(self, entry, config, root_folder_id, parent_local_id):
        name      = entry.get('name', '')
        rpath     = entry.get('remote_path', '')
        is_folder = entry.get('is_folder', False)
        size      = entry.get('size', 0)
        etag      = entry.get('etag', '')
        if not name or not rpath:
            return
        ftype = 'folder' if is_folder else 'file'
        mime, _ = mimetypes.guess_type(name)
        vals = {
            'name': name, 'file_type': ftype,
            'mime_type': mime or 'application/octet-stream',
            'drive_config_id': config.id,
            'root_folder_id': root_folder_id,
            'parent_folder_id': parent_local_id,
            'cloud_file_id': rpath,
            'cloud_url': self._s3_url(config, rpath),
            'file_size': size,
            'sync_state': 'synced',
        }
        existing = self.env['cloud.file'].sudo().search([
            ('cloud_file_id', '=', rpath), ('drive_config_id', '=', config.id)
        ], limit=1)
        if existing:
            existing.sudo().write(vals)
        else:
            try:
                self.env['cloud.file'].sudo().create(vals)
            except Exception as e:
                _logger.warning("Could not upsert cloud.file '%s': %s", name, e)

    def get_presigned_url(self, cloud_file_id_str, config, expiry_seconds=3600):
        """Generate a presigned download URL for an S3 object."""
        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or '').strip()
        key = cloud_file_id_str.strip('/')
        return s3.generate_presigned_url('get_object', Params={'Bucket': bucket, 'Key': key},
                                          ExpiresIn=expiry_seconds)

    @api.model
    def fetch_and_sync_files(self):
        """Cron entry — sync all active AWS configs."""
        configs = self.env['cloud.provider.config'].sudo().search([
            ('provider_type', '=', 'aws'), ('active', '=', True)
        ])
        for config in configs:
            if not config.is_connected:
                continue
            self.env['cloud.file'].sudo().with_context(sync_type='cron').sync_pending_to_drive(drive_config_id=config.id)
            for root in config.root_ids.filtered(lambda r: r.active):
                try:
                    result = self.find_or_create_folder(root.name, '/', config)
                    if result and result.get('remote_id') and root.root_id != result['remote_id']:
                        root.sudo().write({'root_id': result['remote_id']})
                    self._sync_config_files(config, root_folder_id=root.id,
                                            remote_parent_id=root.root_id or ('/' + root.name))
                except Exception as e:
                    _logger.error("AWS cron sync error for '%s': %s", root.name, e)
