# -*- coding: utf-8 -*-
"""
Unified OAuth callback controller for all cloud providers.
Routes: /cloud_hub/gdrive/callback, /cloud_hub/onedrive/callback, /cloud_hub/dropbox/callback
Also serves file downloads and ZIP exports for all providers.
"""
import io, json, logging, zipfile
import requests as http_requests
import werkzeug
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class CloudHubController(http.Controller):

    # ─────────────────────────────────────────────────────────────────────────
    # OAuth Callbacks
    # ─────────────────────────────────────────────────────────────────────────

    @http.route('/cloud_hub/gdrive/callback', type='http', auth='user')
    def gdrive_callback(self, **kw):
        code  = kw.get('code')
        state = kw.get('state')
        if not code or not state:
            return 'Missing code or state.'
        config = request.env['cloud.provider.config'].sudo().browse(int(state))
        if not config.exists():
            return 'Invalid config ID.'
        data = {'code': code, 'client_id': config.client_id, 'client_secret': config.client_secret,
                'redirect_uri': config.redirect_uri, 'grant_type': 'authorization_code'}
        resp = http_requests.post('https://oauth2.googleapis.com/token', data=data)
        if resp.status_code == 200:
            rt = resp.json().get('refresh_token')
            if rt:
                config.write({'refresh_token': rt})
                return werkzeug.utils.redirect(
                    f'/web#id={config.id}&model=cloud.provider.config&view_type=form')
            return 'No refresh token. Try consent=force in auth URL.'
        return f'Google OAuth Error: {resp.text}'

    @http.route('/cloud_hub/onedrive/callback', type='http', auth='user')
    def onedrive_callback(self, **kw):
        code  = kw.get('code')
        state = kw.get('state')
        if not code or not state:
            return 'Missing code or state.'
        config = request.env['cloud.provider.config'].sudo().browse(int(state))
        if not config.exists():
            return 'Invalid config ID.'
        data = {'code': code, 'client_id': config.client_id, 'client_secret': config.client_secret,
                'redirect_uri': config.redirect_uri, 'grant_type': 'authorization_code'}
        resp = http_requests.post('https://login.microsoftonline.com/common/oauth2/v2.0/token', data=data)
        if resp.status_code == 200:
            rt = resp.json().get('refresh_token')
            if rt:
                config.write({'refresh_token': rt})
                return werkzeug.utils.redirect(
                    f'/web#id={config.id}&model=cloud.provider.config&view_type=form')
            return 'No refresh token.'
        return f'OneDrive OAuth Error: {resp.text}'

    @http.route('/cloud_hub/dropbox/callback', type='http', auth='user')
    def dropbox_callback(self, **kw):
        code  = kw.get('code')
        state = kw.get('state')
        if not code or not state:
            return 'Missing code or state.'
        config = request.env['cloud.provider.config'].sudo().browse(int(state))
        if not config.exists():
            return 'Invalid config ID.'
        data = {'code': code, 'client_id': config.client_id, 'client_secret': config.client_secret,
                'redirect_uri': config.redirect_uri, 'grant_type': 'authorization_code'}
        resp = http_requests.post('https://api.dropbox.com/oauth2/token', data=data)
        if resp.status_code == 200:
            rt = resp.json().get('refresh_token')
            if rt:
                config.write({'refresh_token': rt})
                return werkzeug.utils.redirect(
                    f'/web#id={config.id}&model=cloud.provider.config&view_type=form')
            return 'No refresh token. Ensure token_access_type=offline is set.'
        return f'Dropbox OAuth Error: {resp.text}'

    # ─────────────────────────────────────────────────────────────────────────
    # File Download — single file (all providers)
    # ─────────────────────────────────────────────────────────────────────────

    @http.route('/cloud_hub/download/<int:file_id>', type='http', auth='user')
    def cloud_download(self, file_id, **kw):
        cf = request.env['cloud.file'].sudo().browse(file_id)
        if not cf.exists():
            return request.not_found()
        config = cf.drive_config_id
        ptype  = config.provider_type
        try:
            if ptype == 'aws':
                engine = request.env['cloud.sync.aws'].sudo()
                presigned = engine.get_presigned_url(cf.cloud_file_id, config, expiry_seconds=300)
                return request.redirect(presigned, local=False)
            elif ptype == 'gdrive':
                engine = request.env['cloud.sync.gdrive'].sudo()
                token = engine._get_access_token(config)
                r = http_requests.get(
                    f'https://www.googleapis.com/drive/v3/files/{cf.cloud_file_id}?alt=media',
                    headers={'Authorization': f'Bearer {token}'}, stream=True, timeout=60)
                if r.status_code == 200:
                    return request.make_response(r.content, headers=[
                        ('Content-Type', cf.mime_type or 'application/octet-stream'),
                        ('Content-Disposition', http.content_disposition(cf.name))])
            elif ptype == 'onedrive':
                engine = request.env['cloud.sync.onedrive'].sudo()
                token = engine._get_access_token(config)
                r = http_requests.get(
                    f'https://graph.microsoft.com/v1.0/me/drive/items/{cf.cloud_file_id}/content',
                    headers={'Authorization': f'Bearer {token}'}, allow_redirects=False, timeout=30)
                if r.status_code == 302:
                    return request.redirect(r.headers.get('Location'), local=False)
                if r.status_code == 200:
                    return request.make_response(r.content, headers=[
                        ('Content-Type', cf.mime_type or 'application/octet-stream'),
                        ('Content-Disposition', http.content_disposition(cf.name))])
            elif ptype == 'nextcloud':
                engine = request.env['cloud.sync.nextcloud'].sudo()
                url = engine._dav_url(config, cf.cloud_file_id)
                r = http_requests.get(url, auth=engine._auth(config), timeout=60)
                if r.status_code == 200:
                    return request.make_response(r.content, headers=[
                        ('Content-Type', cf.mime_type or 'application/octet-stream'),
                        ('Content-Disposition', http.content_disposition(cf.name))])
            elif ptype == 'dropbox':
                engine = request.env['cloud.sync.dropbox'].sudo()
                token = engine._get_access_token(config)
                dbx_arg = json.dumps({'path': f'id:{cf.cloud_file_id}'})
                r = http_requests.post('https://content.dropboxapi.com/2/files/download',
                                        headers={'Authorization': f'Bearer {token}', 'Dropbox-API-Arg': dbx_arg},
                                        timeout=60)
                if r.status_code == 200:
                    return request.make_response(r.content, headers=[
                        ('Content-Type', cf.mime_type or 'application/octet-stream'),
                        ('Content-Disposition', http.content_disposition(cf.name))])
        except Exception as e:
            _logger.error('Download error for file %s: %s', file_id, e)
        return 'Download failed. Please try again.'

    # ─────────────────────────────────────────────────────────────────────────
    # ZIP Download (multi-file)
    # ─────────────────────────────────────────────────────────────────────────

    @http.route('/cloud_hub/download_zip', type='http', auth='user')
    def cloud_download_zip(self, file_ids='', **kw):
        if not file_ids:
            return 'No files selected.'
        try:
            ids = [int(i) for i in file_ids.split(',') if i.strip()]
            all_files = request.env['cloud.file'].sudo().get_recursive_files_for_zip(ids)
            if not all_files:
                return 'No files found.'
            buf = io.BytesIO()
            first_config = all_files[0][0].drive_config_id
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for cf, rel_path in all_files:
                    try:
                        content = self._fetch_file_content(cf)
                        if content:
                            zf.writestr(rel_path, content)
                    except Exception as e:
                        _logger.warning('ZIP: skipping %s: %s', rel_path, e)
            buf.seek(0)
            fname = f'cloud_export_{first_config.provider_type}.zip' if first_config else 'cloud_export.zip'
            return request.make_response(buf.getvalue(), headers=[
                ('Content-Type', 'application/zip'),
                ('Content-Disposition', http.content_disposition(fname))])
        except Exception as e:
            return f'ZIP error: {e}'

    def _fetch_file_content(self, cf):
        config = cf.drive_config_id
        ptype  = config.provider_type
        if ptype == 'aws':
            engine = self.env['cloud.sync.aws'].sudo() if hasattr(self, 'env') else request.env['cloud.sync.aws'].sudo()
            s3 = engine._get_s3_client(config)
            bucket = (config.aws_s3_bucket_name or '').strip()
            key = cf.cloud_file_id.strip('/')
            return s3.get_object(Bucket=bucket, Key=key)['Body'].read()
        elif ptype == 'gdrive':
            engine = request.env['cloud.sync.gdrive'].sudo()
            token = engine._get_access_token(config)
            r = http_requests.get(f'https://www.googleapis.com/drive/v3/files/{cf.cloud_file_id}?alt=media',
                                   headers={'Authorization': f'Bearer {token}'}, timeout=60)
            return r.content if r.status_code == 200 else None
        elif ptype == 'onedrive':
            engine = request.env['cloud.sync.onedrive'].sudo()
            token = engine._get_access_token(config)
            r = http_requests.get(f'https://graph.microsoft.com/v1.0/me/drive/items/{cf.cloud_file_id}/content',
                                   headers={'Authorization': f'Bearer {token}'}, timeout=60)
            return r.content if r.status_code == 200 else None
        elif ptype == 'nextcloud':
            engine = request.env['cloud.sync.nextcloud'].sudo()
            url = engine._dav_url(config, cf.cloud_file_id)
            r = http_requests.get(url, auth=engine._auth(config), timeout=60)
            return r.content if r.status_code == 200 else None
        elif ptype == 'dropbox':
            engine = request.env['cloud.sync.dropbox'].sudo()
            token = engine._get_access_token(config)
            dbx_arg = json.dumps({'path': f'id:{cf.cloud_file_id}'})
            r = http_requests.post('https://content.dropboxapi.com/2/files/download',
                                    headers={'Authorization': f'Bearer {token}', 'Dropbox-API-Arg': dbx_arg}, timeout=60)
            return r.content if r.status_code == 200 else None
        return None
