# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import requests
import werkzeug

class GoogleDriveController(http.Controller):

    @http.route('/google_account/authentication', type='http', auth='user')
    def google_drive_oauth2callback(self, **kw):
        code = kw.get('code')
        state = kw.get('state')
        if not code:
            return "No code provided"
        if not state:
            return "No state (config ID) provided"

        config = request.env['google.drive.config'].sudo().browse(int(state))
        if not config:
            return "Invalid Configuration ID"

        client_id = config.client_id
        client_secret = config.client_secret
        redirect_uri = config.redirect_uri

        data = {
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }
        
        response = requests.post("https://oauth2.googleapis.com/token", data=data)
        if response.status_code == 200:
            token_data = response.json()
            refresh_token = token_data.get('refresh_token')
            if refresh_token:
                config.write({'refresh_token': refresh_token})
                return werkzeug.utils.redirect(f'/web#id={config.id}&model=google.drive.config&view_type=form')
            else:
                return "Failed to get refresh token. Make sure you haven't already authorized this app, or use prompt=consent."
        else:
            return f"Error: {response.text}"

    @http.route('/google_drive/download/<int:file_id>', type='http', auth='user')
    def google_drive_download(self, file_id, **kw):
        """Proxy and force download of a Google Drive file."""
        file_record = request.env['google.drive.file'].sudo().browse(file_id)
        if not file_record.exists() or not file_record.google_file_id:
            return request.not_found()

        config = file_record.drive_config_id
        access_token = request.env['google.drive.sync'].sudo()._get_access_token(config)
        if not access_token:
            return "Failed to authenticate with Google Drive."

        headers = {'Authorization': f'Bearer {access_token}'}
        g_id = file_record.google_file_id
        mimetype = file_record.mime_type or ''
        filename = file_record.name

        # Handle native Google Docs/Sheets (Export required)
        is_export = False
        target_mime = mimetype
        if 'vnd.google-apps' in mimetype:
            is_export = True
            export_map = {
                'application/vnd.google-apps.document': ('application/pdf', '.pdf'),
                'application/vnd.google-apps.spreadsheet': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
                'application/vnd.google-apps.presentation': ('application/pdf', '.pdf'),
                'application/vnd.google-apps.drawing': ('image/png', '.png'),
            }
            target_mime, ext = export_map.get(mimetype, ('application/pdf', '.pdf'))
            if not filename.lower().endswith(ext):
                filename += ext
            url = f"https://www.googleapis.com/drive/v3/files/{g_id}/export?mimeType={target_mime}"
        else:
            url = f"https://www.googleapis.com/drive/v3/files/{g_id}?alt=media"

        try:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                return f"Error downloading from Google Drive: {response.text}"

            headers = [
                ('Content-Type', target_mime),
                ('Content-Disposition', http.content_disposition(filename))
            ]
            return request.make_response(response.content, headers=headers)
        except Exception as e:
            return f"Download error: {str(e)}"
