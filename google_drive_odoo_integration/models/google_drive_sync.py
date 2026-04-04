# -*- coding: utf-8 -*-
import base64
import io
import json
import logging

import requests as http_requests

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Try importing official Google API client; fall back to requests
try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    from google.oauth2.credentials import Credentials
    HAS_GOOGLE_API = True
except ImportError:
    HAS_GOOGLE_API = False
    _logger.info("Google API client not installed. Using requests fallback for Drive sync.")


class GoogleDriveSync(models.AbstractModel):
    _name = 'google.drive.sync'
    _description = 'Google Drive Synchronization Logic'

    # ─── Authentication ───

    def _get_access_token(self, config):
        """Get a fresh access token using the refresh token."""
        if not config.refresh_token:
            return False

        data = {
            'client_id': config.client_id,
            'client_secret': config.client_secret,
            'refresh_token': config.refresh_token,
            'grant_type': 'refresh_token',
        }
        response = http_requests.post("https://oauth2.googleapis.com/token", data=data)
        if response.status_code == 200:
            return response.json().get('access_token')
        _logger.warning("Failed to get access token: %s", response.text)
        return False

    def _get_drive_service(self, config):
        """Return a Google Drive API service (only if google-api-python-client is installed)."""
        if not HAS_GOOGLE_API:
            return False
        if not config.refresh_token:
            return False

        creds = Credentials(
            None,
            refresh_token=config.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config.client_id,
            client_secret=config.client_secret,
        )
        return build('drive', 'v3', credentials=creds)

    # ─── Odoo → Drive: Upload file ───

    def upload_file(self, attachment):
        """Upload an Odoo attachment to Google Drive."""
        configs = self.env['google.drive.config'].search([
            ('active', '=', True), ('readonly', '=', False)
        ])
        for config in configs:
            # Try official client first, fallback to requests
            service = self._get_drive_service(config)
            if service:
                self._upload_via_client(service, attachment, config)
            else:
                self._upload_via_requests(attachment, config)

    def _upload_via_client(self, service, attachment, config):
        """Upload using the official Google API client."""
        # Use first active root folder for upload
        first_root = config.root_ids.filtered(lambda r: r.active)[:1]
        root_id = first_root.root_id if first_root else False
        
        file_metadata = {
            'name': attachment.name,
            'parents': [root_id] if root_id else []
        }
        file_content = base64.b64decode(attachment.datas) if attachment.datas else attachment.raw
        media = MediaIoBaseUpload(
            io.BytesIO(file_content),
            mimetype=attachment.mimetype,
            resumable=True,
        )
        try:
            result = service.files().create(
                body=file_metadata, media_body=media, fields='id',
            ).execute()
            file_id = result.get('id')
            if file_id:
                self._register_uploaded_file(attachment, config, file_id)
        except Exception as e:
            _logger.error("Client upload failed for %s: %s", attachment.name, str(e))

    def _upload_via_requests(self, attachment, config):
        """Upload using raw HTTP requests (fallback)."""
        access_token = self._get_access_token(config)
        if not access_token:
            return

        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Use first active root folder for upload
        first_root = config.root_ids.filtered(lambda r: r.active)[:1]
        root_id = first_root.root_id if first_root else False

        metadata = {
            "name": attachment.name,
            "parents": [root_id] if root_id else []
        }
        files = {
            'data': ('metadata', json.dumps(metadata), 'application/json; charset=UTF-8'),
            'file': (attachment.name, attachment.raw, attachment.mimetype)
        }
        response = http_requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id",
            headers=headers, files=files,
        )
        if response.status_code == 200:
            file_id = response.json().get('id')
            if file_id:
                self._register_uploaded_file(attachment, config, file_id)

    def _register_uploaded_file(self, attachment, config, file_id):
        """Save the uploaded file info in both ir.attachment and google.drive.file."""
        attachment.with_context(skip_gdrive_sync=True).write({'google_file_id': file_id})
        self.env['google.drive.file'].sudo().create({
            'name': attachment.name,
            'drive_config_id': config.id,
            'file_type': 'file',
            'mime_type': attachment.mimetype,
            'google_file_id': file_id,
            'google_url': f'https://drive.google.com/file/d/{file_id}/view',
            'owner_name': self.env.user.name,
            'last_modified': fields.Datetime.now(),
            'sync_state': 'synced',
            'last_synced': fields.Datetime.now(),
        })
        _logger.info("Uploaded %s to Drive (ID: %s)", attachment.name, file_id)

    def rename_file(self, file_record, new_name):
        """Rename a file or folder on Google Drive."""
        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            return False

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        data = {"name": new_name}
        url = f"https://www.googleapis.com/drive/v3/files/{file_record.google_file_id}"

        try:
            response = http_requests.patch(url, headers=headers, data=json.dumps(data))
            if response.status_code == 200:
                _logger.info("Successfully renamed file %s to %s on Drive", file_record.google_file_id, new_name)
                return True
            else:
                _logger.warning("Failed to rename file on Drive: %s", response.text)
                return False
        except Exception as e:
            _logger.error("Error renaming file on Drive: %s", str(e))
            return False

    def delete_file_from_drive(self, file_record):
        """Delete (trash) a file or folder on Google Drive."""
        if not file_record.google_file_id:
            return True  # Nothing to delete on Drive

        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            return False

        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"https://www.googleapis.com/drive/v3/files/{file_record.google_file_id}"

        try:
            response = http_requests.delete(url, headers=headers)
            if response.status_code in (200, 204):
                _logger.info("Deleted file %s from Drive", file_record.google_file_id)
                return True
            else:
                _logger.warning("Failed to delete file from Drive: %s", response.text)
                return False
        except Exception as e:
            _logger.error("Error deleting file from Drive: %s", str(e))
            return False

    # ─── Odoo → Drive: Create folder ───

    def create_folder_in_drive(self, folder_name, config, parent_gdrive_id=None):
        """Create a folder on Google Drive."""
        access_token = self._get_access_token(config)
        if not access_token:
            return False

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_gdrive_id:
            metadata["parents"] = [parent_gdrive_id]

        response = http_requests.post(
            "https://www.googleapis.com/drive/v3/files?fields=id,webViewLink",
            headers=headers, data=json.dumps(metadata),
        )
        if response.status_code == 200:
            data = response.json()
            return {
                'google_file_id': data.get('id'),
                'google_url': data.get('webViewLink', ''),
            }
        _logger.warning("Failed to create folder: %s", response.text)
        return False

    def upload_file_to_drive(self, file_name, file_content, mime_type, config, parent_gdrive_id=None):
        """Upload a file to Google Drive with optional parent folder placement."""
        access_token = self._get_access_token(config)
        if not access_token:
            return False

        headers = {"Authorization": f"Bearer {access_token}"}
        metadata = {"name": file_name}
        if parent_gdrive_id:
            metadata["parents"] = [parent_gdrive_id]

        files = {
            'data': ('metadata', json.dumps(metadata), 'application/json; charset=UTF-8'),
            'file': (file_name, file_content, mime_type)
        }
        response = http_requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink",
            headers=headers, files=files,
        )
        if response.status_code == 200:
            data = response.json()
            return {
                'google_file_id': data.get('id'),
                'google_url': data.get('webViewLink', ''),
            }
        _logger.warning("Failed to upload file to Drive: %s", response.text)
        return False


    # ─── Drive → Odoo: Backward sync ───

    def fetch_and_sync_files(self):
        configs = self.env['google.drive.config'].search([('active', '=', True)])
        for config in configs:
            for root in config.root_ids.filtered(lambda r: r.active):
                self._sync_config_files(config, root_folder_id=root.id, gdrive_parent_id=root.root_id)

    def _sync_config_files(self, config, root_folder_id=False, parent_folder_id=False, gdrive_parent_id=False):
        access_token = self._get_access_token(config)
        if not access_token:
            return

        headers = {"Authorization": f"Bearer {access_token}"}
        current_parent = gdrive_parent_id

        if current_parent:
            query = f"'{current_parent}' in parents and trashed = false"
        else:
            # Fallback for syncs triggered without a specific parent
            # But normally _sync_config_files is called with a root or parent
            return

        # Collect all Google file IDs seen during this sync pass
        synced_google_ids = []

        # Paginate through all results
        page_token = None
        while True:
            url = (
                f"https://www.googleapis.com/drive/v3/files"
                f"?q={query}"
                f"&pageSize=1000"
                f"&fields=nextPageToken,files(id,name,mimeType,size,webViewLink,owners,modifiedTime,starred)"
            )
            if page_token:
                url += f"&pageToken={page_token}"

            response = http_requests.get(url, headers=headers)
            if response.status_code != 200:
                _logger.warning("Drive API error: %s", response.text)
                break

            result = response.json()
            files = result.get('files', [])
            for file_data in files:
                synced_google_ids.append(file_data.get('id'))
                self._process_drive_file(file_data, config, root_folder_id, parent_folder_id, headers)

            page_token = result.get('nextPageToken')
            if not page_token:
                break

        # Remove Odoo records whose Google Drive file no longer exists
        # (i.e. deleted from Google Drive)
        stale_domain = [
            ('drive_config_id', '=', config.id),
            ('root_folder_id', '=', root_folder_id),
            ('parent_folder_id', '=', parent_folder_id),
            # Ignore pending un-uploaded records which don't have a google_file_id yet
            ('google_file_id', '!=', False),
        ]
        if synced_google_ids:
            stale_domain.append(('google_file_id', 'not in', synced_google_ids))

        stale_records = self.env['google.drive.file'].sudo().search(stale_domain)
        if stale_records:
            _logger.info(
                "Removing %d stale file(s) from Odoo (deleted in Google Drive): %s",
                len(stale_records),
                ', '.join(stale_records.mapped('name'))
            )
            stale_records.unlink()

    def _process_drive_file(self, file_data, config, root_folder_id, parent_folder_id, headers):
        """Process a single file from the Google Drive API response."""
        g_id = file_data.get('id')
        name = file_data.get('name')
        mimetype = file_data.get('mimeType')
        size = int(file_data.get('size', 0))
        url = file_data.get('webViewLink')
        is_folder = mimetype == 'application/vnd.google-apps.folder'

        owners = file_data.get('owners', [])
        owner_name = owners[0].get('displayName', 'Unknown') if owners else 'Me'

        modified_time = file_data.get('modifiedTime')
        last_modified = False
        if modified_time:
            last_modified = modified_time.replace('T', ' ').replace('Z', '')
            if '.' in last_modified:
                last_modified = last_modified[:last_modified.index('.')]

        is_starred = file_data.get('starred', False)

        explorer_record = self.env['google.drive.file'].sudo().search([
            ('google_file_id', '=', g_id),
            ('drive_config_id', '=', config.id),
            ('root_folder_id', '=', root_folder_id),
        ], limit=1)

        vals = {
            'name': name,
            'drive_config_id': config.id,
            'root_folder_id': root_folder_id,
            'file_type': 'folder' if is_folder else 'file',
            'mime_type': mimetype,
            'file_size': size,
            'google_file_id': g_id,
            'google_url': url,
            'parent_folder_id': parent_folder_id,
            'owner_name': owner_name,
            'last_modified': last_modified,
            'starred': is_starred,
            'last_synced': fields.Datetime.now(),
            'sync_state': 'synced',
        }

        if explorer_record:
            explorer_record.write(vals)
        else:
            explorer_record = self.env['google.drive.file'].sudo().create(vals)

        # Download file content to Odoo (backward sync)
        if not is_folder:
            attachment = self.env['ir.attachment'].sudo().search([
                ('google_file_id', '=', g_id),
            ], limit=1)

            if not attachment and 'vnd.google-apps' not in mimetype:
                try:
                    file_response = http_requests.get(
                        f"https://www.googleapis.com/drive/v3/files/{g_id}?alt=media",
                        headers=headers,
                    )
                    if file_response.status_code == 200:
                        self.env['ir.attachment'].with_context(
                            skip_gdrive_sync=True
                        ).sudo().create({
                            'name': name,
                            'raw': file_response.content,
                            'mimetype': mimetype,
                            'google_file_id': g_id,
                        })
                except Exception as e:
                    _logger.warning("Failed to download %s: %s", name, str(e))

        if is_folder:
            self._sync_config_files(config, root_folder_id, explorer_record.id, g_id)
