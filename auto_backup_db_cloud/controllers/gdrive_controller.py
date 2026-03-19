# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import requests
import werkzeug


class GDriveBackupController(http.Controller):

    @http.route('/google_account/authentication', type='http', auth='user', website=True, multilang=False)
    def gdrive_oauth2callback(self, **kw):
        code = kw.get('code')
        if not code:
            return "No code provided"

        ICP = request.env['ir.config_parameter'].sudo()
        client_id = ICP.get_param('auto_backup_db_cloud.gdrive_client_id')
        client_secret = ICP.get_param('auto_backup_db_cloud.gdrive_client_secret')

        base_url = ICP.get_param('web.base.url')
        host_url = base_url or ''
        try:
            if host_url and ("localhost" in host_url or "127.0.0.1" in host_url or ":8069" in host_url):
                req_root = request.httprequest.url_root.rstrip('/')
                if req_root:
                    host_url = req_root
        except Exception:
            pass
        redirect_uri = f"{host_url}/google_account/authentication"

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
                ICP.set_param('auto_backup_db_cloud.gdrive_refresh_token', refresh_token)
                return self._render_success_page('Google Drive')
            else:
                return "Failed to get refresh token. If you already authorized, try Revoking access in Google Account or use prompt=consent."
        else:
            return f"Error: {response.text}"

    def _render_success_page(self, provider_name):
        """Render a success notification page that auto-redirects to settings."""
        return request.render('auto_backup_db_cloud.cloud_auth_success', {
            'provider_name': provider_name,
        })

