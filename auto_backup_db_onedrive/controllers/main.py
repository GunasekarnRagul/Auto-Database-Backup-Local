# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import requests
import werkzeug
import logging
_logger = logging.getLogger(__name__)

class OneDriveBackupController(http.Controller):

    @http.route('/onedrive_account/authentication', type='http', auth='user', website=True, multilang=False)
    def onedrive_backup_oauth2callback(self, **kw):
        code = kw.get('code')
        if not code:
            return "No code provided"

        ICP = request.env['ir.config_parameter'].sudo()
        client_id = ICP.get_param('auto_backup_db_onedrive.client_id')
        client_secret = ICP.get_param('auto_backup_db_onedrive.client_secret')
        
        # Reconstruct redirect URI (must match the one used in action_onedrive_authenticate)
        base_url = ICP.get_param('web.base.url')
        host_url = base_url or ''
        try:
            if host_url and ("localhost" in host_url or "127.0.0.1" in host_url or ":8069" in host_url):
                req_root = request.httprequest.url_root.rstrip('/')
                if req_root:
                    host_url = req_root
        except Exception:
            pass
        redirect_uri = f"{host_url}/onedrive_account/authentication"
        
        data = {
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }
        
        response = requests.post("https://login.microsoftonline.com/common/oauth2/v2.0/token", data=data)
        if response.status_code == 200:
            token_data = response.json()
            refresh_token = token_data.get('refresh_token')
            if refresh_token:
                ICP.set_param('auto_backup_db_onedrive.refresh_token', refresh_token)
                return werkzeug.utils.redirect('/web#action=base_setup.action_general_configuration')
            else:
                return "Failed to get refresh token. Please ensure 'offline_access' scope is granted."
        else:
            return f"Error: {response.text}"
