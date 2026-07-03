# -*- coding: utf-8 -*-
"""
Google Drive Sync Engine — adapted from google_drive_odoo_integration
Uses Google Drive REST API v3 with OAuth 2.0. Config model: cloud.provider.config (provider_type='gdrive').
"""
import base64, logging, mimetypes
import requests as http_requests
from odoo import models, fields, api

_logger = logging.getLogger(__name__)
GDRIVE_API  = 'https://www.googleapis.com/drive/v3'
GDRIVE_UPLOAD = 'https://www.googleapis.com/upload/drive/v3'
TOKEN_URL = 'https://oauth2.googleapis.com/token'


class CloudSyncGdrive(models.AbstractModel):
    _name = 'cloud.sync.gdrive'
    _description = 'Cloud Sync — Google Drive Engine'

    def _get_access_token(self, config):
        if not config.refresh_token:
            return False
        data = {
            'client_id': config.client_id,
            'client_secret': config.client_secret,
            'refresh_token': config.refresh_token,
            'grant_type': 'refresh_token',
        }
        try:
            resp = http_requests.post(TOKEN_URL, data=data, timeout=15)
            if resp.status_code == 200:
                return resp.json().get('access_token')
            _logger.warning('GDrive token refresh failed: %s', resp.text)
        except Exception as e:
            _logger.error('GDrive token error: %s', e)
        return False

    def _headers(self, token):
        return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    def find_or_create_folder(self, folder_name, parent_id, config):
        token = self._get_access_token(config)
        if not token:
            raise Exception('Google Drive: could not get access token')
        # Search for existing
        q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id and parent_id != '/':
            q += f" and '{parent_id}' in parents"
        resp = http_requests.get(f'{GDRIVE_API}/files', headers=self._headers(token),
                                  params={'q': q, 'fields': 'files(id,name)', 'spaces': 'drive'}, timeout=15)
        if resp.status_code == 200 and resp.json().get('files'):
            fid = resp.json()['files'][0]['id']
            return {'remote_id': fid, 'name': folder_name, 'cloud_url': f'https://drive.google.com/drive/folders/{fid}'}
        # Create
        meta = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id] if parent_id and parent_id != '/' else []}
        cr = http_requests.post(f'{GDRIVE_API}/files', headers=self._headers(token), json=meta, timeout=15)
        if cr.status_code == 200:
            fid = cr.json().get('id')
            return {'remote_id': fid, 'name': folder_name, 'cloud_url': f'https://drive.google.com/drive/folders/{fid}'}
        raise Exception(f'GDrive folder create failed: {cr.text}')

    def upload_attachment(self, attachment, config_rec):
        import base64
        provider = config_rec.cloud_provider_id
        folder   = config_rec.cloud_folder_id
        file_data = attachment.raw or (attachment.datas and base64.b64decode(attachment.datas))
        if not file_data:
            return
        token = self._get_access_token(provider)
        if not token:
            raise Exception('Google Drive: no access token')
        mime = attachment.mimetype or 'application/octet-stream'
        parent_id = folder.cloud_file_id if folder else None
        meta = {'name': attachment.name, 'parents': [parent_id] if parent_id else []}
        import json
        resp = http_requests.post(
            f'{GDRIVE_UPLOAD}/files?uploadType=multipart',
            headers={'Authorization': f'Bearer {token}'},
            files={'data': ('metadata', json.dumps(meta), 'application/json'),
                   'file': (attachment.name, file_data, mime)},
            timeout=60,
        )
        if resp.status_code not in (200, 201):
            raise Exception(f'GDrive upload failed: {resp.text}')
        file_id = resp.json().get('id')
        url = f'https://drive.google.com/file/d/{file_id}/view'
        cf = self.env['cloud.file'].sudo().search([
            ('cloud_file_id', '=', file_id), ('drive_config_id', '=', provider.id)
        ], limit=1)
        if not cf:
            cf = self.env['cloud.file'].sudo().create({
                'name': attachment.name, 'file_type': 'file',
                'drive_config_id': provider.id, 'cloud_file_id': file_id,
                'cloud_url': url, 'mime_type': mime,
                'file_size': len(file_data), 'sync_state': 'synced',
                'res_model': attachment.res_model, 'res_id': attachment.res_id,
                'root_folder_id': folder.root_folder_id.id if folder and folder.root_folder_id else False,
                'parent_folder_id': folder.id if folder else False,
            })
        attachment.sudo().with_context(sync_type='skip').write({
            'cloud_file_id': cf.id,
            'type': 'url' if config_rec.storage_mode == 'drive' else attachment.type,
            'url': url if config_rec.storage_mode == 'drive' else attachment.url,
        })
        self.env['cloud.sync.log'].sudo().log_operation(
            config=provider, file_name=attachment.name, operation='upload', state='success',
            sync_type=self.env.context.get('sync_type', 'auto'),
            cloud_file_id=file_id, file_size=len(file_data),
        )

    def upload_file(self, cloud_file_rec, config):
        att = self.env['ir.attachment'].sudo().search([('cloud_file_id', '=', cloud_file_rec.id)], limit=1)
        if att:
            config_rec = self.env['cloud.attachment.config'].sudo().search([
                ('cloud_provider_id', '=', config.id)], limit=1)
            if config_rec:
                self.upload_attachment(att, config_rec)

    def _sync_config_files(self, config, root_folder_id=None, remote_parent_id=None,
                           parent_local_id=None, depth=0, max_depth=8):
        if depth > max_depth or config.state != 'connected':
            return
        token = self._get_access_token(config)
        if not token:
            return
        parent_q = f"'{remote_parent_id}' in parents" if remote_parent_id else "'root' in parents"
        q = f"{parent_q} and trashed=false"
        params = {'q': q, 'fields': 'files(id,name,mimeType,size,modifiedTime)', 'spaces': 'drive', 'pageSize': 1000}
        resp = http_requests.get(f'{GDRIVE_API}/files', headers=self._headers(token), params=params, timeout=30)
        if resp.status_code != 200:
            return
        for f in resp.json().get('files', []):
            is_folder = f.get('mimeType') == 'application/vnd.google-apps.folder'
            url = f'https://drive.google.com/{"drive/folders" if is_folder else "file/d"}/{f["id"]}/view'
            vals = {
                'name': f.get('name'), 'file_type': 'folder' if is_folder else 'file',
                'mime_type': f.get('mimeType', ''), 'drive_config_id': config.id,
                'root_folder_id': root_folder_id, 'parent_folder_id': parent_local_id,
                'cloud_file_id': f['id'], 'cloud_url': url,
                'file_size': int(f.get('size', 0) or 0), 'sync_state': 'synced',
            }
            existing = self.env['cloud.file'].sudo().search([
                ('cloud_file_id', '=', f['id']), ('drive_config_id', '=', config.id)], limit=1)
            if existing:
                existing.sudo().write(vals)
            else:
                try:
                    existing = self.env['cloud.file'].sudo().create(vals)
                except Exception as e:
                    _logger.warning("GDrive upsert failed for '%s': %s", f.get('name'), e)
                    continue
            if is_folder:
                self._sync_config_files(config, root_folder_id=root_folder_id,
                                        remote_parent_id=f['id'],
                                        parent_local_id=existing.id,
                                        depth=depth + 1, max_depth=max_depth)

    @api.model
    def fetch_and_sync_files(self):
        configs = self.env['cloud.provider.config'].sudo().search([
            ('provider_type', '=', 'gdrive'), ('active', '=', True)])
        for config in configs:
            if config.state != 'connected':
                continue
            self.env['cloud.file'].sudo().with_context(sync_type='cron').sync_pending_to_drive(drive_config_id=config.id)
            for root in config.root_ids.filtered(lambda r: r.active):
                try:
                    self._sync_config_files(config, root_folder_id=root.id, remote_parent_id=root.root_id)
                except Exception as e:
                    _logger.error("GDrive cron error for '%s': %s", root.name, e)
