# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import requests
import werkzeug
import io
import zipfile

class OneDriveController(http.Controller):

    @http.route('/one_drive/authentication', type='http', auth='user')
    def one_drive_oauth2callback(self, **kw):
        code = kw.get('code')
        state = kw.get('state')
        if not code:
            return "No code provided"
        if not state:
            return "No state (config ID) provided"

        config = request.env['one.drive.config'].sudo().browse(int(state))
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
        
        response = requests.post("https://login.microsoftonline.com/common/oauth2/v2.0/token", data=data)
        if response.status_code == 200:
            token_data = response.json()
            refresh_token = token_data.get('refresh_token')
            if refresh_token:
                config.write({'refresh_token': refresh_token})
                return werkzeug.utils.redirect(f'/web#id={config.id}&model=one.drive.config&view_type=form')
            else:
                return "Failed to get refresh token. Make sure you haven't already authorized this app, or use prompt=consent."
        else:
            return f"Error: {response.text}"

    @http.route('/one_drive/download/<int:file_id>', type='http', auth='user')
    def one_drive_download(self, file_id, **kw):
        """Proxy and force download of a OneDrive file."""
        file_record = request.env['one.drive.file'].sudo().browse(file_id)
        if not file_record.exists() or not file_record.one_drive_file_id:
            return request.not_found()

        config = file_record.drive_config_id
        access_token = request.env['one.drive.sync'].sudo()._get_access_token(config)
        if not access_token:
            return "Failed to authenticate with OneDrive."

        headers = {'Authorization': f'Bearer {access_token}'}
        o_id = file_record.one_drive_file_id
        mimetype = file_record.mime_type or ''
        filename = file_record.name

        # Handle OneDrive formats if needed, or just download
        is_export = False
        target_mime = mimetype
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{o_id}/content"

        try:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                request.env['one.drive.sync'].sudo()._log_file(
                    config, file_record, file_record.name, 'download', 
                    state='fail', error_message=f"HTTP {response.status_code}: {response.text}"
                )
                return f"Error downloading from OneDrive: {response.text}"

            # Log successful download
            request.env['one.drive.sync'].sudo()._log_file(
                config, file_record, file_record.name, 'download'
            )

            headers = [
                ('Content-Type', target_mime),
                ('Content-Disposition', http.content_disposition(filename))
            ]
            return request.make_response(response.content, headers=headers)
        except Exception as e:
            request.env['one.drive.sync'].sudo()._log_file(
                config, file_record, file_record.name, 'download',
                state='fail', error_message=str(e)
            )
            return f"Download error: {str(e)}"
    @http.route('/one_drive/download_zip', type='http', auth='user')
    def one_drive_download_zip(self, file_ids, **kw):
        """Build and stream a ZIP file of the selected files and folders."""
        if not file_ids:
            return "No files selected."
            
        try:
            ids = [int(i) for i in file_ids.split(',')]
            file_model = request.env['one.drive.file']
            
            # Resolve all files recursively (Model method)
            all_files = file_model.sudo().get_recursive_files_for_zip(ids)
            if not all_files:
                return "No files found to download."
                
            sync_service = request.env['one.drive.sync'].sudo()
            # Use the first record to get the drive configuration
            first_rec = all_files[0][0]
            config = first_rec.drive_config_id
            access_token = sync_service._get_access_token(config)
            
            if not access_token:
                return "Failed to authenticate with OneDrive."
                
            zip_buffer = io.BytesIO()
            headers = {'Authorization': f'Bearer {access_token}'}
            
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for record, rel_path in all_files:
                    if not record.one_drive_file_id:
                        continue
                        
                    o_id = record.one_drive_file_id
                    mimetype = record.mime_type or ''
                    
                    url = f"https://graph.microsoft.com/v1.0/me/drive/items/{o_id}/content"
                    
                    try:
                        response = requests.get(url, headers=headers, timeout=30)
                        if response.status_code == 200:
                            zip_file.writestr(rel_path, response.content)
                    except Exception as loop_e:
                        print(f"Error zipping file {rel_path}: {str(loop_e)}")
            
            zip_buffer.seek(0)
            zip_filename = "one_drive_export.zip"
            log_name = "Bulk Download (ZIP)"
            if len(ids) == 1:
                rec = file_model.sudo().browse(ids[0])
                zip_filename = f"{rec.name}.zip"
                log_name = f"Download ZIP: {rec.name}"
            
            # Log successful ZIP download
            sync_service._log(config, log_name, 'download')

            return request.make_response(
                zip_buffer.getvalue(),
                headers=[
                    ('Content-Type', 'application/zip'),
                    ('Content-Disposition', http.content_disposition(zip_filename))
                ]
            )
        except Exception as e:
            return f"ZIP Download Error: {str(e)}"
