# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import requests
import werkzeug


class DropboxBackupController(http.Controller):

    @http.route('/dropbox_account/authentication', type='http', auth='user', website=True, multilang=False)
    def dropbox_oauth2callback(self, **kw):
        code = kw.get('code')
        if not code:
            return "No code provided"

        ICP = request.env['ir.config_parameter'].sudo()
        app_key = ICP.get_param('auto_backup_db_cloud.dropbox_app_key')
        app_secret = ICP.get_param('auto_backup_db_cloud.dropbox_app_secret')

        base_url = ICP.get_param('web.base.url')
        host_url = base_url or ''
        try:
            if request and hasattr(request, 'httprequest'):
                req_root = request.httprequest.url_root.rstrip('/')
                if req_root:
                    host_url = req_root
        except Exception:
            pass
        redirect_uri = f"{host_url}/dropbox_account/authentication"

        data = {
            'code': code,
            'client_id': app_key,
            'client_secret': app_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }

        response = requests.post("https://api.dropbox.com/oauth2/token", data=data)
        if response.status_code == 200:
            token_data = response.json()
            refresh_token = token_data.get('refresh_token')
            if refresh_token:
                ICP.set_param('auto_backup_db_cloud.dropbox_refresh_token', refresh_token)
                return request.render('auto_backup_db_cloud.cloud_auth_success', {
                    'provider_name': 'Dropbox',
                })
            else:
                return "Failed to get refresh token. Ensure you requested 'offline' access type."
        else:
            return f"Error: {response.text}"

