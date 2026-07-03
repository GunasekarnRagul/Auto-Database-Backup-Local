# -*- coding: utf-8 -*-
"""
Microsoft OneDrive Sync Engine — adapted from one_drive_odoo_integration
"""
import base64, logging
import requests as http_requests
from odoo import models, fields, api

_logger = logging.getLogger(__name__)
GRAPH_API = 'https://graph.microsoft.com/v1.0/me/drive'
TOKEN_URL = 'https://login.microsoftonline.com/common/oauth2/v2.0/token'


class CloudSyncOnedrive(models.AbstractModel):
    _name = 'cloud.sync.onedrive'
    _description = 'Cloud Sync — Microsoft OneDrive Engine'

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
            _logger.error('OneDrive token error: %s', e)
        return False

    def _hdrs(self, token):
        return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    def find_or_create_folder(self, folder_name, parent_id, config):
        token = self._get_access_token(config)
        if not token:
            raise Exception('OneDrive: no access token')
        h = self._hdrs(token)
        base = f'{GRAPH_API}/items/{parent_id}' if parent_id and parent_id not in ('root', '/', '') else f'{GRAPH_API}/root'
        resp = http_requests.get(f'{base}/children', headers=h,
                                  params={'$filter': f"name eq '{folder_name}'"}, timeout=15)
        if resp.status_code == 200:
            items = [i for i in resp.json().get('value', []) if 'folder' in i]
            if items:
                return {'remote_id': items[0]['id'], 'name': folder_name, 'cloud_url': items[0].get('webUrl', '')}
        cr = http_requests.post(f'{base}/children', headers=h,
                                 json={'name': folder_name, 'folder': {}, '@microsoft.graph.conflictBehavior': 'rename'},
                                 timeout=15)
        if cr.status_code in (200, 201):
            fid = cr.json().get('id')
            return {'remote_id': fid, 'name': folder_name, 'cloud_url': cr.json().get('webUrl', '')}
        raise Exception(f'OneDrive folder create failed: {cr.text}')

    def upload_attachment(self, attachment, config_rec):
        provider = config_rec.cloud_provider_id
        folder   = config_rec.cloud_folder_id
        file_data = attachment.raw or (attachment.datas and base64.b64decode(attachment.datas))
        if not file_data:
            return
        token = self._get_access_token(provider)
        if not token:
            raise Exception('OneDrive: no access token')
        parent_id = folder.cloud_file_id if folder else 'root'
        fn = attachment.name
        base_url = f'{GRAPH_API}/items/{parent_id}:/{fn}:/content' if parent_id and parent_id != 'root' else f'{GRAPH_API}/root:/{fn}:/content'
        resp = http_requests.put(base_url, headers={'Authorization': f'Bearer {token}',
                                                     'Content-Type': attachment.mimetype or 'application/octet-stream'},
                                  data=file_data, timeout=60)
        if resp.status_code not in (200, 201):
            raise Exception(f'OneDrive upload failed: {resp.text}')
        r = resp.json()
        fid = r.get('id')
        web_url = r.get('webUrl', '')
        cf = self.env['cloud.file'].sudo().search([('cloud_file_id', '=', fid),
                                                    ('drive_config_id', '=', provider.id)], limit=1)
        if not cf:
            cf = self.env['cloud.file'].sudo().create({
                'name': fn, 'file_type': 'file', 'drive_config_id': provider.id,
                'cloud_file_id': fid, 'cloud_url': web_url,
                'mime_type': attachment.mimetype or '', 'file_size': len(file_data),
                'sync_state': 'synced', 'res_model': attachment.res_model, 'res_id': attachment.res_id,
                'root_folder_id': folder.root_folder_id.id if folder and hasattr(folder, 'root_folder_id') and folder.root_folder_id else False,
                'parent_folder_id': folder.id if folder else False,
            })
        attachment.sudo().with_context(sync_type='skip').write({
            'cloud_file_id': cf.id,
            'type': 'url' if config_rec.storage_mode == 'drive' else attachment.type,
            'url': web_url if config_rec.storage_mode == 'drive' else attachment.url,
        })
        self.env['cloud.sync.log'].sudo().log_operation(
            config=provider, file_name=fn, operation='upload', state='success',
            sync_type=self.env.context.get('sync_type', 'auto'), cloud_file_id=fid, file_size=len(file_data),
        )

    def upload_file(self, cloud_file_rec, config):
        att = self.env['ir.attachment'].sudo().search([('cloud_file_id', '=', cloud_file_rec.id)], limit=1)
        if att:
            cfg = self.env['cloud.attachment.config'].sudo().search([('cloud_provider_id', '=', config.id)], limit=1)
            if cfg:
                self.upload_attachment(att, cfg)

    def _sync_config_files(self, config, root_folder_id=None, remote_parent_id=None,
                           parent_local_id=None, depth=0, max_depth=8):
        if depth > max_depth or config.state != 'connected':
            return
        token = self._get_access_token(config)
        if not token:
            return
        h = self._hdrs(token)
        base_url = (f'{GRAPH_API}/items/{remote_parent_id}/children'
                    if remote_parent_id and remote_parent_id not in ('root', '/')
                    else f'{GRAPH_API}/root/children')
        resp = http_requests.get(base_url, headers=h, params={'$top': 1000}, timeout=30)
        if resp.status_code != 200:
            return
        for item in resp.json().get('value', []):
            is_folder = 'folder' in item
            fid = item['id']
            vals = {'name': item['name'], 'file_type': 'folder' if is_folder else 'file',
                    'drive_config_id': config.id, 'root_folder_id': root_folder_id,
                    'parent_folder_id': parent_local_id, 'cloud_file_id': fid,
                    'cloud_url': item.get('webUrl', ''), 'file_size': item.get('size', 0) or 0,
                    'sync_state': 'synced'}
            existing = self.env['cloud.file'].sudo().search([('cloud_file_id', '=', fid),
                                                              ('drive_config_id', '=', config.id)], limit=1)
            if existing:
                existing.sudo().write(vals)
            else:
                try:
                    existing = self.env['cloud.file'].sudo().create(vals)
                except Exception as e:
                    _logger.warning("OneDrive upsert failed: %s", e)
                    continue
            if is_folder:
                self._sync_config_files(config, root_folder_id=root_folder_id, remote_parent_id=fid,
                                        parent_local_id=existing.id, depth=depth + 1, max_depth=max_depth)

    @api.model
    def fetch_and_sync_files(self):
        configs = self.env['cloud.provider.config'].sudo().search([
            ('provider_type', '=', 'onedrive'), ('active', '=', True)])
        for config in configs:
            if config.state != 'connected':
                continue
            self.env['cloud.file'].sudo().with_context(sync_type='cron').sync_pending_to_drive(drive_config_id=config.id)
            for root in config.root_ids.filtered(lambda r: r.active):
                try:
                    self._sync_config_files(config, root_folder_id=root.id, remote_parent_id=root.root_id)
                except Exception as e:
                    _logger.error("OneDrive cron error for '%s': %s", root.name, e)
