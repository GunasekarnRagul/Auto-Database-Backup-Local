# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import requests
import werkzeug
import io, zipfile, json

NEXTCLOUD_TOKEN_URL = "https://api.nextcloudapi.com/oauth2/token"
NEXTCLOUD_CONTENT_URL = "https://content.nextcloudapi.com/2/files/download"

class NextcloudController(http.Controller):

    @http.route("/nextcloud/authentication", type="http", auth="user")
    def nextcloud_oauth2callback(self, **kw):
        code = kw.get("code")
        state = kw.get("state")
        if not code:
            return "No code provided"
        if not state:
            return "No state (config ID) provided"
        config = request.env["nextcloud.config"].sudo().browse(int(state))
        if not config:
            return "Invalid Configuration ID"
        data = {
            "code": code,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
            "grant_type": "authorization_code",
        }
        response = requests.post(NEXTCLOUD_TOKEN_URL, data=data)
        if response.status_code == 200:
            token_data = response.json()
            refresh_token = token_data.get("refresh_token")
            if refresh_token:
                config.write({"refresh_token": refresh_token})
                return werkzeug.utils.redirect(
                    f"/web#id={config.id}&model=nextcloud.config&view_type=form"
                )
            return "Failed to get refresh token. Ensure token_access_type=offline is set."
        return f"Error: {response.text}"

    def _resolve_file_remote_path(self, file_record):
        """Return the correct *decoded* WebDAV path for a file record.

        Priority:
          1. Use nextcloud_file_id directly when it is already a WebDAV path
             (starts with '/').  URL-decode it so any legacy %20 values that
             were stored by older code become real spaces — the caller uses
             _safe_webdav_url() which will then re-encode exactly once.
          2. Fall back to reconstructing the path from the parent_folder_id chain
             for older records that stored a numeric Nextcloud file ID.

        The returned value is ALWAYS a decoded plain-string path (spaces, not %20).
        """
        import urllib.parse
        fid = file_record.nextcloud_file_id or ''
        if fid.startswith('/') or (fid.startswith('%2') and urllib.parse.unquote(fid).startswith('/')):
            # Decode any legacy %20 before returning
            return urllib.parse.unquote(fid)

        # Fallback: walk parent chain — names are always decoded strings already
        parts = []
        parent = file_record.parent_folder_id
        while parent:
            parts.append(parent.name)
            parent = parent.parent_folder_id
        path_parts = []
        if file_record.root_folder_id:
            path_parts.append(file_record.root_folder_id.name)
        if parts:
            path_parts.extend(reversed(parts))
        remote_parent = '/' + '/'.join(path_parts) if path_parts else ''
        return f'{remote_parent}/{file_record.name}'

    @http.route("/nextcloud/download/<int:file_id>", type="http", auth="user")
    def nextcloud_download(self, file_id, **kw):
        file_record = request.env["nextcloud.file"].sudo().browse(file_id)
        if not file_record.exists():
            return request.not_found()
        config = file_record.drive_config_id
        sync_service = request.env["nextcloud.sync"].sudo()

        # Always get a decoded path; _safe_webdav_url encodes it exactly once
        remote_path = self._resolve_file_remote_path(file_record)
        url  = sync_service._safe_webdav_url(config, remote_path)
        auth = sync_service._webdav_auth(config)

        try:
            response = requests.get(url, auth=auth, timeout=60)
            if response.status_code in (200, 207):
                sync_service._log_file(config, file_record, file_record.name, "download")
                resp_headers = [
                    ("Content-Type", file_record.mime_type or "application/octet-stream"),
                    ("Content-Disposition", http.content_disposition(file_record.name)),
                ]
                return request.make_response(response.content, headers=resp_headers)

            sync_service._log_file(config, file_record, file_record.name, "download",
                                   state="fail", error_message=f"HTTP {response.status_code}: {response.text[:200]}")
            return f"Error downloading from Nextcloud: HTTP {response.status_code} — path: {remote_path}"
        except Exception as e:
            return f"Download error: {str(e)}"

    @http.route("/nextcloud/preview/<int:file_id>", type="http", auth="user")
    def nextcloud_preview(self, file_id, **kw):
        file_record = request.env["nextcloud.file"].sudo().browse(file_id)
        if not file_record.exists():
            return request.not_found()
        config = file_record.drive_config_id
        sync_service = request.env["nextcloud.sync"].sudo()

        # Always get a decoded path; _safe_webdav_url encodes it exactly once
        remote_path = self._resolve_file_remote_path(file_record)
        url  = sync_service._safe_webdav_url(config, remote_path)
        auth = sync_service._webdav_auth(config)

        try:
            import os
            import urllib.parse
            _, ext = os.path.splitext(file_record.name.lower())
            office_exts = ['.doc', '.docx', '.docm', '.ppt', '.pps', '.ppsx', '.ppsm', '.pptx', '.pptm', '.xls', '.xlsx', '.xlsm', '.rtf', '.csv']

            if ext in office_exts:
                nc_base = (config.nextcloud_url or "").rstrip("/")
                ocs_url = f"{nc_base}/ocs/v2.php/apps/files_sharing/api/v1/shares"
                headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
                data = {"path": remote_path, "shareType": 3, "permissions": 1}
                share_url = None

                try:
                    r_get = requests.get(
                        f"{ocs_url}?path={urllib.parse.quote(remote_path, safe='/')}",
                        auth=auth, headers=headers, timeout=10,
                    )
                    if r_get.status_code == 200:
                        shares = r_get.json().get("ocs", {}).get("data", [])
                        for share in shares:
                            if share.get("share_type") == 3:
                                share_url = share.get("url")
                                break
                    if not share_url:
                        r_post = requests.post(ocs_url, auth=auth, headers=headers, data=data, timeout=10)
                        if r_post.status_code == 200:
                            share_url = r_post.json().get("ocs", {}).get("data", {}).get("url")
                except Exception:
                    pass

                if share_url:
                    raw_url = f"{share_url.rstrip('/')}/download"
                    viewer_url = f"https://docs.google.com/viewer?url={urllib.parse.quote(raw_url)}&embedded=true"
                    import werkzeug
                    return werkzeug.utils.redirect(viewer_url)

                html_content = f"""
                <!DOCTYPE html>
                <html>
                <body style="font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f8f9fa;">
                    <div style="text-align: center; padding: 2rem; background: white; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                        <h2 style="margin: 0 0 1rem 0; color: #343a40;">Preview Not Available</h2>
                        <p style="color: #6c757d; margin-bottom: 1.5rem;">Office documents cannot be previewed natively.</p>
                        <a href="/nextcloud/download/{file_record.id}" target="_blank" style="display: inline-block; padding: 0.5rem 1rem; background-color: #007bff; color: white; text-decoration: none; border-radius: 4px; font-weight: 500;">Download File</a>
                    </div>
                </body>
                </html>
                """
                return request.make_response(html_content, headers=[("Content-Type", "text/html")])

            response = requests.get(url, auth=auth, timeout=60)
            if response.status_code in (200, 207):
                import mimetypes
                guessed_mime, _ = mimetypes.guess_type(file_record.name)
                content_type = file_record.mime_type or guessed_mime or "application/octet-stream"
                resp_headers = [
                    ("Content-Type", content_type),
                    ("Content-Disposition", f'inline; filename="{file_record.name}"'),
                ]
                return request.make_response(response.content, headers=resp_headers)
            return f"Error previewing from Nextcloud: HTTP {response.status_code} — path: {remote_path}"
        except Exception as e:
            return f"Preview error: {str(e)}"

    @http.route("/nextcloud/download_zip", type="http", auth="user")
    def nextcloud_download_zip(self, file_ids, **kw):
        if not file_ids:
            return "No files selected."
        try:
            ids = [int(i) for i in file_ids.split(",")]
            file_model = request.env["nextcloud.file"]
            all_files = file_model.sudo().get_recursive_files_for_zip(ids)
            if not all_files:
                return "No files found to download."
            sync_service = request.env["nextcloud.sync"].sudo()
            first_rec = all_files[0][0]
            config = first_rec.drive_config_id
            auth = sync_service._webdav_auth(config)
            
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for record, rel_path in all_files:
                    # Use _resolve_file_remote_path for consistent, decoded paths
                    remote_path = self._resolve_file_remote_path(record)
                    # _safe_webdav_url encodes exactly once (handles spaces correctly)
                    url = sync_service._safe_webdav_url(config, remote_path)
                    try:
                        r = requests.get(url, auth=auth, timeout=60)
                        if r.status_code in (200, 207):
                            zf.writestr(rel_path, r.content)
                        else:
                            import logging
                            logging.getLogger(__name__).warning(
                                "ZIP: failed to download %s — HTTP %s", remote_path, r.status_code
                            )
                    except Exception as loop_e:
                        import logging
                        logging.getLogger(__name__).warning("ZIP: error for %s: %s", rel_path, loop_e)
                        
            zip_buffer.seek(0)
            zip_filename = "nextcloud_export.zip"
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
