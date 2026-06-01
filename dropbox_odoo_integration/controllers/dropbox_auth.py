# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import requests
import werkzeug
import io, zipfile, json

DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
DROPBOX_CONTENT_URL = "https://content.dropboxapi.com/2/files/download"

class OneDriveController(http.Controller):

    @http.route("/dropbox/authentication", type="http", auth="user")
    def one_drive_oauth2callback(self, **kw):
        code = kw.get("code")
        state = kw.get("state")
        if not code:
            return "No code provided"
        if not state:
            return "No state (config ID) provided"
        config = request.env["one.drive.config"].sudo().browse(int(state))
        if not config:
            return "Invalid Configuration ID"
        data = {
            "code": code,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
            "grant_type": "authorization_code",
        }
        response = requests.post(DROPBOX_TOKEN_URL, data=data)
        if response.status_code == 200:
            token_data = response.json()
            refresh_token = token_data.get("refresh_token")
            if refresh_token:
                config.write({"refresh_token": refresh_token})
                return werkzeug.utils.redirect(
                    f"/web#id={config.id}&model=one.drive.config&view_type=form"
                )
            return "Failed to get refresh token. Ensure token_access_type=offline is set."
        return f"Error: {response.text}"

    @http.route("/dropbox/download/<int:file_id>", type="http", auth="user")
    def one_drive_download(self, file_id, **kw):
        file_record = request.env["one.drive.file"].sudo().browse(file_id)
        if not file_record.exists() or not file_record.one_drive_file_id:
            return request.not_found()
        config = file_record.drive_config_id
        access_token = request.env["one.drive.sync"].sudo()._get_access_token(config)
        if not access_token:
            return "Failed to authenticate with Dropbox."
        dbx_arg = json.dumps({"path": f"id:{file_record.one_drive_file_id}"})
        headers = {"Authorization": f"Bearer {access_token}", "Dropbox-API-Arg": dbx_arg}
        try:
            response = requests.post(DROPBOX_CONTENT_URL, headers=headers, timeout=60)
            if response.status_code == 200:
                request.env["one.drive.sync"].sudo()._log_file(
                    config, file_record, file_record.name, "download"
                )
                resp_headers = [
                    ("Content-Type", file_record.mime_type or "application/octet-stream"),
                    ("Content-Disposition", http.content_disposition(file_record.name)),
                ]
                return request.make_response(response.content, headers=resp_headers)
            request.env["one.drive.sync"].sudo()._log_file(
                config, file_record, file_record.name, "download",
                state="fail", error_message=f"HTTP {response.status_code}: {response.text}",
            )
            return f"Error downloading from Dropbox: {response.text}"
        except Exception as e:
            return f"Download error: {str(e)}"

    @http.route("/dropbox/preview/<int:file_id>", type="http", auth="user")
    def dropbox_preview(self, file_id, **kw):
        file_record = request.env["one.drive.file"].sudo().browse(file_id)
        if not file_record.exists() or not file_record.one_drive_file_id:
            return request.not_found()
        config = file_record.drive_config_id
        access_token = request.env["one.drive.sync"].sudo()._get_access_token(config)
        if not access_token:
            return "Failed to authenticate with Dropbox."
        dbx_arg = json.dumps({"path": f"id:{file_record.one_drive_file_id}"})
        headers = {"Authorization": f"Bearer {access_token}", "Dropbox-API-Arg": dbx_arg}
        try:
            import os
            _, ext = os.path.splitext(file_record.name.lower())
            preview_exts = ['.doc', '.docx', '.docm', '.ppt', '.pps', '.ppsx', '.ppsm', '.pptx', '.pptm', '.xls', '.xlsx', '.xlsm', '.rtf']
            
            target_url = "https://content.dropboxapi.com/2/files/get_preview" if ext in preview_exts else DROPBOX_CONTENT_URL
            response = requests.post(target_url, headers=headers, timeout=60)
            
            # If get_preview fails (e.g. file too large or unsupported format), fallback to download
            if ext in preview_exts and response.status_code != 200:
                response = requests.post(DROPBOX_CONTENT_URL, headers=headers, timeout=60)
                target_url = DROPBOX_CONTENT_URL

            if response.status_code == 200:
                import mimetypes
                if target_url == "https://content.dropboxapi.com/2/files/get_preview":
                    content_type = "application/pdf"
                    filename = f"{file_record.name}.pdf"
                else:
                    guessed_mime, _ = mimetypes.guess_type(file_record.name)
                    content_type = file_record.mime_type or guessed_mime or "application/octet-stream"
                    filename = file_record.name
                    
                resp_headers = [
                    ("Content-Type", content_type),
                    ("Content-Disposition", f"inline; filename=\"{filename}\""),
                ]
                return request.make_response(response.content, headers=resp_headers)
            return f"Error previewing from Dropbox: {response.text}"
        except Exception as e:
            return f"Preview error: {str(e)}"

    @http.route("/dropbox/download_zip", type="http", auth="user")
    def one_drive_download_zip(self, file_ids, **kw):
        if not file_ids:
            return "No files selected."
        try:
            ids = [int(i) for i in file_ids.split(",")]
            file_model = request.env["one.drive.file"]
            all_files = file_model.sudo().get_recursive_files_for_zip(ids)
            if not all_files:
                return "No files found to download."
            sync_service = request.env["one.drive.sync"].sudo()
            first_rec = all_files[0][0]
            config = first_rec.drive_config_id
            access_token = sync_service._get_access_token(config)
            if not access_token:
                return "Failed to authenticate with Dropbox."
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for record, rel_path in all_files:
                    if not record.one_drive_file_id:
                        continue
                    dbx_arg = json.dumps({"path": f"id:{record.one_drive_file_id}"})
                    h = {"Authorization": f"Bearer {access_token}", "Dropbox-API-Arg": dbx_arg}
                    try:
                        r = requests.post(DROPBOX_CONTENT_URL, headers=h, timeout=30)
                        if r.status_code == 200:
                            zf.writestr(rel_path, r.content)
                    except Exception as loop_e:
                        print(f"Error zipping {rel_path}: {loop_e}")
            zip_buffer.seek(0)
            zip_filename = "dropbox_export.zip"
            log_name = "Bulk Download (ZIP)"
            if len(ids) == 1:
                rec = file_model.sudo().browse(ids[0])
                zip_filename = f"{rec.name}.zip"
                log_name = f"Download ZIP: {rec.name}"
            sync_service._log(config, log_name, "download")
            return request.make_response(
                zip_buffer.getvalue(),
                headers=[
                    ("Content-Type", "application/zip"),
                    ("Content-Disposition", http.content_disposition(zip_filename)),
                ],
            )
        except Exception as e:
            return f"ZIP Download Error: {str(e)}"
