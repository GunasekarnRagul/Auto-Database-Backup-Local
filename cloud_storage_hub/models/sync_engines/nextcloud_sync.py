# -*- coding: utf-8 -*-
"""Nextcloud Sync Engine — WebDAV/Basic Auth"""
import base64, logging, mimetypes
import requests as http_requests
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class CloudSyncNextcloud(models.AbstractModel):
    _name = 'cloud.sync.nextcloud'
    _description = 'Cloud Sync — Nextcloud Engine'

    def _auth(self, config):
        return ((config.nc_username or '').strip(), (config.nc_app_password or '').strip())

    def _dav_url(self, config, path=''):
        base = (config.nextcloud_url or '').strip().rstrip('/')
        user = (config.nc_username or '').strip()
        p = path.lstrip('/')
        return f'{base}/remote.php/dav/files/{user}/{p}'

    def _list(self, config, remote_path='/'):
        url = self._dav_url(config, remote_path)
        body = '''<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:prop><d:getlastmodified/><d:getcontentlength/><d:resourcetype/><d:getetag/>
  <oc:fileid/><oc:size/></d:prop>
</d:propfind>'''
        try:
            resp = http_requests.request('PROPFIND', url, auth=self._auth(config),
                                          headers={'Depth': '1', 'Content-Type': 'application/xml'},
                                          data=body, timeout=30)
            if resp.status_code not in (207, 200):
                return []
            return self._parse_propfind(resp.text, remote_path, config)
        except Exception as e:
            _logger.error('Nextcloud list error: %s', e)
            return []

    def _parse_propfind(self, xml_text, parent_path, config):
        import xml.etree.ElementTree as ET
        NS = {'d': 'DAV:', 'oc': 'http://owncloud.org/ns'}
        entries = []
        try:
            root = ET.fromstring(xml_text)
            for resp in root.findall('.//d:response', NS):
                href = resp.findtext('d:href', '', NS)
                rt = resp.find('.//d:resourcetype', NS)
                is_folder = rt is not None and rt.find('d:collection', NS) is not None
                name = href.rstrip('/').split('/')[-1]
                if not name or href.rstrip('/') == self._dav_url(config, parent_path).rstrip('/'):
                    continue
                size = resp.findtext('.//d:getcontentlength', '0', NS) or resp.findtext('.//oc:size', '0', NS)
                etag = resp.findtext('.//d:getetag', '', NS).strip('"')
                fileid = resp.findtext('.//oc:fileid', '', NS) or href
                lm = resp.findtext('.//d:getlastmodified', '', NS)
                remote_p = (parent_path.rstrip('/') + '/' + name).replace('//', '/')
                entries.append({'name': name, 'remote_path': remote_p, 'is_folder': is_folder,
                                'size': int(size) if size else 0, 'etag': etag, 'fileid': fileid,
                                'last_modified': lm})
        except Exception as e:
            _logger.error('Nextcloud propfind parse error: %s', e)
        return entries

    def _mkdir(self, config, remote_path):
        url = self._dav_url(config, remote_path)
        resp = http_requests.request('MKCOL', url, auth=self._auth(config), timeout=15)
        return resp.status_code in (201, 405)  # 405 = already exists

    def find_or_create_folder(self, folder_name, parent_path, config):
        p = (parent_path or '/').rstrip('/') + '/' + folder_name
        self._mkdir(config, p)
        return {'remote_id': p, 'name': folder_name, 'cloud_url': self._dav_url(config, p)}

    def upload_attachment(self, attachment, config_rec):
        provider = config_rec.cloud_provider_id
        folder   = config_rec.cloud_folder_id
        file_data = attachment.raw or (attachment.datas and base64.b64decode(attachment.datas))
        if not file_data:
            return
        folder_path = folder.cloud_file_id if folder else '/'
        remote_path = folder_path.rstrip('/') + '/' + attachment.name
        url = self._dav_url(provider, remote_path)
        resp = http_requests.put(url, auth=self._auth(provider), data=file_data,
                                  headers={'Content-Type': attachment.mimetype or 'application/octet-stream'},
                                  timeout=60)
        if resp.status_code not in (200, 201, 204):
            raise Exception(f'Nextcloud upload failed: {resp.status_code}')
        cf = self.env['cloud.file'].sudo().search([('cloud_file_id', '=', remote_path),
                                                    ('drive_config_id', '=', provider.id)], limit=1)
        if not cf:
            cf = self.env['cloud.file'].sudo().create({
                'name': attachment.name, 'file_type': 'file', 'drive_config_id': provider.id,
                'cloud_file_id': remote_path, 'cloud_url': url,
                'mime_type': attachment.mimetype or '', 'file_size': len(file_data),
                'sync_state': 'synced', 'res_model': attachment.res_model, 'res_id': attachment.res_id,
                'parent_folder_id': folder.id if folder else False,
            })
        attachment.sudo().with_context(sync_type='skip').write({
            'cloud_file_id': cf.id,
            'type': 'url' if config_rec.storage_mode == 'drive' else attachment.type,
            'url': url if config_rec.storage_mode == 'drive' else attachment.url,
        })
        self.env['cloud.sync.log'].sudo().log_operation(
            config=provider, file_name=attachment.name, operation='upload', state='success',
            sync_type=self.env.context.get('sync_type', 'auto'), file_size=len(file_data),
        )

    def upload_file(self, cloud_file_rec, config):
        att = self.env['ir.attachment'].sudo().search([('cloud_file_id', '=', cloud_file_rec.id)], limit=1)
        if att:
            cfg = self.env['cloud.attachment.config'].sudo().search([('cloud_provider_id', '=', config.id)], limit=1)
            if cfg:
                self.upload_attachment(att, cfg)

    def _sync_config_files(self, config, root_folder_id=None, remote_parent_id=None,
                           parent_local_id=None, depth=0, max_depth=8):
        if depth > max_depth or not config.is_connected:
            return
        entries = self._list(config, remote_parent_id or '/')
        for entry in entries:
            name = entry['name']
            rp   = entry['remote_path']
            is_f = entry['is_folder']
            ftype = 'folder' if is_f else 'file'
            mime, _ = mimetypes.guess_type(name)
            vals = {'name': name, 'file_type': ftype, 'drive_config_id': config.id,
                    'root_folder_id': root_folder_id, 'parent_folder_id': parent_local_id,
                    'cloud_file_id': rp, 'cloud_url': self._dav_url(config, rp),
                    'mime_type': mime or 'application/octet-stream',
                    'file_size': entry.get('size', 0), 'sync_state': 'synced'}
            existing = self.env['cloud.file'].sudo().search([('cloud_file_id', '=', rp),
                                                              ('drive_config_id', '=', config.id)], limit=1)
            if existing:
                existing.sudo().write(vals)
            else:
                try:
                    existing = self.env['cloud.file'].sudo().create(vals)
                except Exception as e:
                    _logger.warning("Nextcloud upsert failed: %s", e)
                    continue
            if is_f:
                self._sync_config_files(config, root_folder_id=root_folder_id, remote_parent_id=rp,
                                        parent_local_id=existing.id, depth=depth + 1, max_depth=max_depth)

    @api.model
    def fetch_and_sync_files(self):
        configs = self.env['cloud.provider.config'].sudo().search([
            ('provider_type', '=', 'nextcloud'), ('active', '=', True)])
        for config in configs:
            if not config.is_connected:
                continue
            self.env['cloud.file'].sudo().with_context(sync_type='cron').sync_pending_to_drive(drive_config_id=config.id)
            for root in config.root_ids.filtered(lambda r: r.active):
                try:
                    result = self.find_or_create_folder(root.name, '/', config)
                    if result and result['remote_id'] != root.root_id:
                        root.sudo().write({'root_id': result['remote_id']})
                    self._sync_config_files(config, root_folder_id=root.id, remote_parent_id=root.root_id)
                except Exception as e:
                    _logger.error("Nextcloud cron error for '%s': %s", root.name, e)
