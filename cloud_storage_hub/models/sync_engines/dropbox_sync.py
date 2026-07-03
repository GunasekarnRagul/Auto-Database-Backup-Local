# -*- coding: utf-8 -*-
"""Dropbox Sync Engine — Dropbox API v2 with OAuth 2.0"""
import base64, json, logging
import requests as http_requests
from odoo import models, fields, api

_logger = logging.getLogger(__name__)
DBX_API     = 'https://api.dropboxapi.com/2'
DBX_CONTENT = 'https://content.dropboxapi.com/2'
TOKEN_URL   = 'https://api.dropbox.com/oauth2/token'


class CloudSyncDropbox(models.AbstractModel):
    _name = 'cloud.sync.dropbox'
    _description = 'Cloud Sync — Dropbox Engine'

    def _get_access_token(self, config):
        if not config.refresh_token:
            return False
        data = {'client_id': config.client_id, 'client_secret': config.client_secret,
                'refresh_token': config.refresh_token, 'grant_type': 'refresh_token'}
        try:
            resp = http_requests.post(TOKEN_URL, data=data, timeout=15)
            if resp.status_code == 200:
                return resp.json().get('access_token')
        except Exception as e:
            _logger.error('Dropbox token error: %s', e)
        return False

    def _hdrs(self, token):
        return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    def find_or_create_folder(self, folder_name, parent_path, config):
        token = self._get_access_token(config)
        if not token:
            raise Exception('Dropbox: no access token')
        path = (parent_path.rstrip('/') + '/' + folder_name) if parent_path and parent_path != '/' else ('/' + folder_name)
        # Try create
        resp = http_requests.post(f'{DBX_API}/files/create_folder_v2',
                                   headers=self._hdrs(token), json={'path': path, 'autorename': False}, timeout=15)
        if resp.status_code in (200, 201):
            meta = resp.json().get('metadata', {})
            fid = meta.get('id', path)
            return {'remote_id': fid, 'name': folder_name, 'cloud_url': f'https://www.dropbox.com/home{path}'}
        if resp.status_code == 409:  # Already exists
            return {'remote_id': path, 'name': folder_name, 'cloud_url': f'https://www.dropbox.com/home{path}'}
        raise Exception(f'Dropbox folder create failed: {resp.text}')

    def upload_attachment(self, attachment, config_rec):
        provider = config_rec.cloud_provider_id
        folder   = config_rec.cloud_folder_id
        file_data = attachment.raw or (attachment.datas and base64.b64decode(attachment.datas))
        if not file_data:
            return
        token = self._get_access_token(provider)
        if not token:
            raise Exception('Dropbox: no access token')
        folder_path = folder.cloud_file_id if folder else ''
        # folder.cloud_file_id may be a Dropbox ID like 'id:xxx' — use display path instead
        if folder_path.startswith('id:'):
            folder_path = '/' + (folder.name or 'odoo')
        remote_path = (folder_path.rstrip('/') + '/' + attachment.name) if folder_path else ('/' + attachment.name)
        dbx_arg = json.dumps({'path': remote_path, 'mode': 'add', 'autorename': True})
        resp = http_requests.post(f'{DBX_CONTENT}/files/upload',
                                   headers={'Authorization': f'Bearer {token}',
                                            'Content-Type': 'application/octet-stream',
                                            'Dropbox-API-Arg': dbx_arg},
                                   data=file_data, timeout=60)
        if resp.status_code not in (200, 201):
            raise Exception(f'Dropbox upload failed: {resp.text}')
        r = resp.json()
        fid = r.get('id', remote_path)
        actual_path = r.get('path_lower', remote_path)
        web_url = f'https://www.dropbox.com/home{actual_path}'
        cf = self.env['cloud.file'].sudo().search([('cloud_file_id', '=', fid),
                                                    ('drive_config_id', '=', provider.id)], limit=1)
        if not cf:
            cf = self.env['cloud.file'].sudo().create({
                'name': attachment.name, 'file_type': 'file', 'drive_config_id': provider.id,
                'cloud_file_id': fid, 'cloud_url': web_url,
                'mime_type': attachment.mimetype or '', 'file_size': len(file_data),
                'sync_state': 'synced', 'res_model': attachment.res_model, 'res_id': attachment.res_id,
                'parent_folder_id': folder.id if folder else False,
            })
        attachment.sudo().with_context(sync_type='skip').write({
            'cloud_file_id': cf.id,
            'type': 'url' if config_rec.storage_mode == 'drive' else attachment.type,
            'url': web_url if config_rec.storage_mode == 'drive' else attachment.url,
        })
        self.env['cloud.sync.log'].sudo().log_operation(
            config=provider, file_name=attachment.name, operation='upload', state='success',
            sync_type=self.env.context.get('sync_type', 'auto'), cloud_file_id=fid, file_size=len(file_data),
        )

    def upload_file(self, cloud_file_rec, config):
        att = self.env['ir.attachment'].sudo().search([('cloud_file_id', '=', cloud_file_rec.id)], limit=1)
        if att:
            cfg = self.env['cloud.attachment.config'].sudo().search([('cloud_provider_id', '=', config.id)], limit=1)
            if cfg:
                self.upload_attachment(att, cfg)

    def _list_folder(self, token, path=''):
        resp = http_requests.post(f'{DBX_API}/files/list_folder',
                                   headers=self._hdrs(token), json={'path': path, 'recursive': False}, timeout=30)
        if resp.status_code != 200:
            return []
        return resp.json().get('entries', [])

    def _sync_config_files(self, config, root_folder_id=None, remote_parent_id=None,
                           parent_local_id=None, depth=0, max_depth=8):
        if depth > max_depth or config.state != 'connected':
            return
        token = self._get_access_token(config)
        if not token:
            return
        path = remote_parent_id if remote_parent_id and not remote_parent_id.startswith('id:') else ''
        entries = self._list_folder(token, path)
        for entry in entries:
            tag = entry.get('.tag', '')
            is_folder = tag == 'folder'
            fid = entry.get('id', entry.get('path_lower', ''))
            name = entry.get('name', '')
            actual_path = entry.get('path_lower', '')
            web_url = f'https://www.dropbox.com/home{actual_path}'
            vals = {'name': name, 'file_type': 'folder' if is_folder else 'file',
                    'drive_config_id': config.id, 'root_folder_id': root_folder_id,
                    'parent_folder_id': parent_local_id, 'cloud_file_id': fid,
                    'cloud_url': web_url, 'file_size': entry.get('size', 0) or 0,
                    'sync_state': 'synced'}
            existing = self.env['cloud.file'].sudo().search([('cloud_file_id', '=', fid),
                                                              ('drive_config_id', '=', config.id)], limit=1)
            if existing:
                existing.sudo().write(vals)
            else:
                try:
                    existing = self.env['cloud.file'].sudo().create(vals)
                except Exception as e:
                    _logger.warning("Dropbox upsert failed: %s", e)
                    continue
            if is_folder:
                self._sync_config_files(config, root_folder_id=root_folder_id, remote_parent_id=actual_path,
                                        parent_local_id=existing.id, depth=depth + 1, max_depth=max_depth)

    @api.model
    def fetch_and_sync_files(self):
        configs = self.env['cloud.provider.config'].sudo().search([
            ('provider_type', '=', 'dropbox'), ('active', '=', True)])
        for config in configs:
            if config.state != 'connected':
                continue
            self.env['cloud.file'].sudo().with_context(sync_type='cron').sync_pending_to_drive(drive_config_id=config.id)
            for root in config.root_ids.filtered(lambda r: r.active):
                try:
                    self._sync_config_files(config, root_folder_id=root.id, remote_parent_id=root.root_id)
                except Exception as e:
                    _logger.error("Dropbox cron error for '%s': %s", root.name, e)
