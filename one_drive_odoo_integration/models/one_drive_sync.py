# -*- coding: utf-8 -*-
import base64
import io
import json
import logging
import time

import requests as http_requests

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Microsoft Graph API client helper
HAS_GOOGLE_API = False


class GoogleDriveSync(models.Model):
    _name = 'one.drive.sync'
    _description = 'OneDrive Synchronization Logic'

    # ─── Logging Helper ───

    def _get_file_info(self, file_record):
        """Extract root folder name and full folder path from a file record."""
        root_name = ''
        folder_path = ''
        try:
            if file_record.root_folder_id:
                root_name = file_record.root_folder_id.name or ''
            # Build folder path by walking the parent chain
            parts = []
            parent = file_record.parent_folder_id
            while parent:
                parts.append(parent.name or '')
                parent = parent.parent_folder_id
            if parts:
                folder_path = ' / '.join(reversed(parts))
        except Exception:
            pass
        return root_name, folder_path

    def _log(self, config, file_name, operation, state='success',
             error_message=False, file_type='file', root_folder_name=False,
             folder_path=False, one_drive_file_id=False, file_size=0, duration=0,
             sync_type=None, **kwargs):
        """Create a sync log entry. Captures real UID to persist through sudo.

        sync_type can be supplied explicitly by the caller (e.g. the cron runner
        passes 'cron' so backward-sync entries are not mis-labelled as 'manual').
        Falls back to the context value and then to 'auto' as the safe default.
        """
        # Capture the UID of the current environment (the person who triggered this)
        real_uid = self.env.uid
        # Explicit argument > context > safe default
        effective_sync_type = sync_type or self.env.context.get('sync_type', 'auto')

        self.env['one.drive.sync.log'].log_operation(
            config=config,
            file_name=file_name,
            operation=operation,
            state=state,
            error_message=error_message,
            sync_type=effective_sync_type,
            file_type=file_type,
            root_folder_name=root_folder_name,
            folder_path=folder_path,
            one_drive_file_id=one_drive_file_id,
            file_size=file_size,
            duration=duration,
            user_id=kwargs.get('user_id') or real_uid
        )

    def _log_file(self, config, file_record, file_name, operation, **kwargs):
        """Log with auto-extracted path info from file_record."""
        root_name, fpath = self._get_file_info(file_record)
        kwargs.setdefault('file_type', file_record.file_type or 'file')
        kwargs.setdefault('one_drive_file_id', file_record.one_drive_file_id or '')
        kwargs.setdefault('root_folder_name', root_name)
        kwargs.setdefault('folder_path', fpath)
        self._log(config, file_name, operation, **kwargs)

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
        response = http_requests.post("https://login.microsoftonline.com/common/oauth2/v2.0/token", data=data)
        if response.status_code == 200:
            return response.json().get('access_token')
        _logger.warning("Failed to get access token: %s", response.text)
        return False

    @api.model
    def get_preview_url(self, file_id, config_id):
        """Get a short-lived embeddable preview URL from Microsoft Graph."""
        config = self.env['one.drive.config'].sudo().browse(config_id)
        access_token = self._get_access_token(config)
        if not access_token:
            return False

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        # Method 1: Use the official /preview endpoint
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}/preview"
        try:
            # Some Graph endpoints require an empty body {} even if no params are sent
            response = http_requests.post(url, headers=headers, json={}, timeout=15)
            if response.status_code == 200:
                return response.json().get('getUrl')
            _logger.info("Preview endpoint status %s: %s", response.status_code, response.text)
        except Exception as e:
            _logger.error("Error fetching preview URL via /preview: %s", str(e))

        # Method 2: Fallback to createLink (embed)
        # This is more stable for Office/SharePoint items
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}/createLink"
        try:
            body = {"type": "embed", "scope": "anonymous"}
            response = http_requests.post(url, headers=headers, json=body, timeout=15)
            if response.status_code in (200, 201):
                return response.json().get('link', {}).get('webUrl')
            
            # If anonymous fails, try organization scope
            body["scope"] = "organization"
            response = http_requests.post(url, headers=headers, json=body, timeout=15)
            if response.status_code in (200, 201):
                return response.json().get('link', {}).get('webUrl')
                
            _logger.warning("Failed all preview methods for %s: %s", file_id, response.text)
        except Exception as e:
            _logger.error("Error fetching preview URL via createLink: %s", str(e))
            
        return False

    # ─── Odoo → Drive: Upload file ───

    def upload_file(self, attachment):
        """Upload an Odoo attachment to OneDrive."""
        configs = self.env['one.drive.config'].search([
            ('active', '=', True), ('readonly', '=', False)
        ])
        for config in configs:
            self._upload_via_requests(attachment, config)

    def _upload_via_requests(self, attachment, config):
        """Upload using raw HTTP requests (fallback)."""
        access_token = self._get_access_token(config)
        if not access_token:
            self._log(config, attachment.name, 'upload', state='fail',
                      error_message='Could not obtain access token')
            return

        # Use first active root folder for upload
        first_root = config.root_ids.filtered(lambda r: r.active)[:1]
        parent_id = first_root.root_id if first_root else 'root'
        
        filename = attachment.name
        # OneDrive simple upload endpoint
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{parent_id}:/{filename}:/content"

        t0 = time.time()
        try:
            response = http_requests.put(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": attachment.mimetype or "application/octet-stream"
                },
                data=attachment.raw,
                timeout=60
            )
        except Exception as e:
            _logger.error("Network error during upload of %s: %s", attachment.name, str(e))
            return
            
        elapsed = time.time() - t0
        if response.status_code in (200, 201):
            data = response.json()
            file_id = data.get('id')
            web_url = data.get('webUrl')
            if file_id:
                self._register_uploaded_file(attachment, config, file_id, one_drive_url=web_url)
                self._log(config, attachment.name, 'upload',
                          one_drive_file_id=file_id,
                          file_size=len(attachment.raw) if attachment.raw else 0,
                          root_folder_name=first_root.name if first_root else False,
                          duration=elapsed)
        else:
            self._log(config, attachment.name, 'upload', state='fail',
                      error_message=f'HTTP {response.status_code}: {response.text}',
                      root_folder_name=first_root.name if first_root else False,
                      duration=elapsed)

    def _register_uploaded_file(self, attachment, config, file_id, one_drive_url=False):
        """Save the uploaded file info in both ir.attachment and one.drive.file."""
        attachment.with_context(skip_one_drive_sync=True).write({'one_drive_file_id': file_id})
        self.env['one.drive.file'].sudo().create({
            'name': attachment.name,
            'drive_config_id': config.id,
            'file_type': 'file',
            'mime_type': attachment.mimetype,
            'one_drive_file_id': file_id,
            'one_drive_url': one_drive_url or f'https://onedrive.live.com/redir?resid={file_id}',
            'owner_name': self.env.user.name,
            'last_modified': fields.Datetime.now(),
            'sync_state': 'synced',
            'last_synced': fields.Datetime.now(),
        })
        _logger.info("Uploaded %s to Drive (ID: %s)", attachment.name, file_id)

    def rename_file(self, file_record, new_name):
        """Rename a file or folder on OneDrive."""
        # Get old name from context (set by the caller before record was updated)
        old_name = self.env.context.get('rename_old_name') or file_record.name
        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            self._log_file(config, file_record, f'{old_name} → {new_name}', 'rename',
                           state='fail', error_message='Could not obtain access token')
            return False

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        data = {"name": new_name}
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}"

        t0 = time.time()
        try:
            response = http_requests.patch(url, headers=headers, data=json.dumps(data))
            elapsed = time.time() - t0
            if response.status_code == 200:
                _logger.info("Successfully renamed file %s to %s on Drive", file_record.one_drive_file_id, new_name)
                self._log_file(config, file_record, f'{old_name} → {new_name}', 'rename',
                               duration=elapsed)
                return True
            else:
                _logger.warning("Failed to rename file on Drive: %s", response.text)
                self._log_file(config, file_record, f'{old_name} → {new_name}', 'rename',
                               state='fail', error_message=f'HTTP {response.status_code}: {response.text}',
                               duration=elapsed)
                return False
        except Exception as e:
            _logger.error("Error renaming file on Drive: %s", str(e))
            self._log_file(config, file_record, f'{old_name} → {new_name}', 'rename',
                           state='fail', error_message=str(e),
                           duration=time.time() - t0)
            return False

    def trash_file(self, file_record, trashed=True):
        """Move a file or folder to the OneDrive Recycle Bin.
        Microsoft Graph uses the DELETE method to move an item to the recycle bin.
        """
        if not file_record.one_drive_file_id:
            return True

        # Graph doesn't support a simple "untrash" via PATCH like Google Drive.
        # Restoring from the recycle bin is a different operation (/restore).
        # For now, we only support trashing (moving to recycle bin).
        if not trashed:
            return True

        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            self._log_file(config, file_record, file_record.name, 'trash', state='fail',
                           error_message='Could not obtain access token')
            return False

        headers = {
            "Authorization": f"Bearer {access_token}"
        }
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}"

        t0 = time.time()
        try:
            # Microsoft Graph DELETE moves the item to the Recycle Bin.
            # Ref: https://learn.microsoft.com/en-us/graph/api/driveitem-delete
            response = http_requests.delete(url, headers=headers, timeout=30)
            elapsed = time.time() - t0
            
            # Successful deletion returns 204 No Content
            if response.status_code in (200, 204):
                _logger.info("Deleted (moved to recycle bin) file %s on Drive", file_record.one_drive_file_id)
                self._log_file(config, file_record, file_record.name, 'trash',
                               duration=elapsed)
                return True
            else:
                _logger.warning("Failed to delete file on Drive: %s", response.text)
                self._log_file(config, file_record, file_record.name, 'trash', state='fail',
                               error_message=f'HTTP {response.status_code}: {response.text}',
                               duration=elapsed)
                return False
        except Exception as e:
            _logger.error("Error trashing/untrashing file on Drive: %s", str(e))
            self._log_file(config, file_record, file_record.name, 'trash', state='fail',
                           error_message=str(e), duration=time.time() - t0)
            return False

    def delete_file_from_drive(self, file_record):
        """Permanently delete a file or folder from OneDrive."""
        if not file_record.one_drive_file_id:
            return True

        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            self._log_file(config, file_record, file_record.name, 'delete', state='fail',
                           error_message='Could not obtain access token')
            return False

        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}"

        t0 = time.time()
        try:
            response = http_requests.delete(url, headers=headers)
            elapsed = time.time() - t0
            if response.status_code in (200, 204, 404):
                _logger.info("Permanently deleted file %s from Drive", file_record.one_drive_file_id)
                self._log_file(config, file_record, file_record.name, 'delete',
                               duration=elapsed)
                return True
            else:
                _logger.warning("Failed to permanently delete file from Drive: %s", response.text)
                self._log_file(config, file_record, file_record.name, 'delete', state='fail',
                               error_message=f'HTTP {response.status_code}: {response.text}',
                               duration=elapsed)
                return False
        except Exception as e:
            _logger.error("Error permanently deleting file from Drive: %s", str(e))
            self._log_file(config, file_record, file_record.name, 'delete', state='fail',
                           error_message=str(e), duration=time.time() - t0)
            return False

    def move_file(self, file_record, old_parent_id, new_parent_id, target_parent_id=None, target_root_id=None):
        """Move a file or folder on OneDrive."""
        if not file_record.one_drive_file_id or not new_parent_id:
            return False

        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            self._log_file(config, file_record, file_record.name, 'move', state='fail',
                           error_message='Could not obtain access token')
            return False

        # Capture old path info before moving
        old_root, old_path = self._get_file_info(file_record)
        old_full_path = f"{config.name}"
        if old_root:
            old_full_path += f"/{old_root}"
        if old_path:
            old_full_path += f"/{old_path}"
        old_full_path += f"/{file_record.name}"
        
        # Calculate new path info
        new_full_path = f"{config.name}"
        if target_parent_id:
            parent_rec = self.env['one.drive.file'].sudo().browse(target_parent_id)
            if parent_rec.exists():
                p_root, p_path = self._get_file_info(parent_rec)
                if p_root:
                    new_full_path += f"/{p_root}"
                if p_path:
                    new_full_path += f"/{p_path}"
                new_full_path += f"/{parent_rec.name}"
        elif target_root_id:
            root_rec = self.env['one.drive.root.folder'].sudo().browse(target_root_id)
            if root_rec.exists():
                new_full_path += f"/{root_rec.name}"
        else:
            new_full_path = "Unknown Location"
            
        new_full_path += f"/{file_record.name}"
        
        combined_transition = f"{old_full_path} ➜ MOVE TO ➜ {new_full_path}"

        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}"
        
        # Microsoft Graph moves items by PATCHing the parentReference property.
        # Ref: https://learn.microsoft.com/en-us/graph/api/driveitem-move
        headers["Content-Type"] = "application/json"
        data = {
            "parentReference": {
                "id": new_parent_id
            }
        }
        
        t0 = time.time()
        try:
            # We use data instead of params for the move request body
            response = http_requests.patch(url, headers=headers, data=json.dumps(data), timeout=60)
            elapsed = time.time() - t0
            if response.status_code == 200:
                _logger.info("Successfully moved file %s on Drive", file_record.one_drive_file_id)
                # Log using the combined transition as the filename, suppressing other fields for clarity
                self.env['one.drive.sync.log'].log_operation(
                    config=False, # Suppress related drive_name column
                    file_name=combined_transition,
                    operation='move',
                    state='success',
                    duration=elapsed,
                    file_type=file_record.file_type or 'file'
                )
                return True
            else:
                _logger.warning("Failed to move file on Drive: %s", response.text)
                self.env['one.drive.sync.log'].log_operation(
                    config=False,
                    file_name=combined_transition,
                    operation='move',
                    state='fail',
                    error_message=f'HTTP {response.status_code}: {response.text}',
                    duration=elapsed,
                    file_type=file_record.file_type or 'file'
                )
                return False
        except Exception as e:
            _logger.error("Error moving file on Drive: %s", str(e))
            self.env['one.drive.sync.log'].log_operation(
                config=False,
                file_name=combined_transition,
                operation='move',
                state='fail',
                error_message=str(e),
                duration=time.time() - t0,
                file_type=file_record.file_type or 'file'
            )
            return False

    # ─── Odoo → Drive: Create folder ───

    def create_folder_in_drive(self, folder_name, config, parent_one_drive_id=None, file_record=None):
        """Create a folder on OneDrive."""
        access_token = self._get_access_token(config)
        if not access_token:
            if file_record:
                self._log_file(config, file_record, folder_name, 'create_folder', state='fail',
                               error_message='Could not obtain access token')
            else:
                self._log(config, folder_name, 'create_folder', state='fail',
                          error_message='Could not obtain access token',
                          file_type='folder')
            return False

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        metadata = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail"
        }
        
        # Use root or specific parent ID in URL
        parent_path = f"items/{parent_one_drive_id}" if parent_one_drive_id and parent_one_drive_id != 'root' else "root"
        url = f"https://graph.microsoft.com/v1.0/me/drive/{parent_path}/children"

        t0 = time.time()
        # Added timeout to prevent hanging connections
        response = http_requests.post(url, headers=headers, data=json.dumps(metadata), timeout=30)
        elapsed = time.time() - t0
        if response.status_code == 201:
            data = response.json()
            if file_record:
                self._log_file(config, file_record, folder_name, 'create_folder',
                               one_drive_file_id=data.get('id'),
                               duration=elapsed)
            else:
                self._log(config, folder_name, 'create_folder',
                          file_type='folder',
                          one_drive_file_id=data.get('id'),
                          duration=elapsed)
            return {
                'one_drive_file_id': data.get('id'),
                'one_drive_url': data.get('webViewLink', ''),
            }
        _logger.warning("Failed to create folder: %s", response.text)
        if file_record:
            self._log_file(config, file_record, folder_name, 'create_folder', state='fail',
                           error_message=f'HTTP {response.status_code}: {response.text}',
                           duration=elapsed)
        else:
            self._log(config, folder_name, 'create_folder', state='fail',
                      error_message=f'HTTP {response.status_code}: {response.text}',
                      file_type='folder',
                      duration=elapsed)
        return False

    def find_folder_by_name(self, folder_name, parent_id, config):
        """Search for a folder by name under a specific parent."""
        access_token = self._get_access_token(config)
        if not access_token:
            return False

        headers = {"Authorization": f"Bearer {access_token}"}
        # Prepare safe query (single quotes in name handled by escaping)
        safe_name = folder_name.replace("'", "''")
        
        parent_path = f"items/{parent_id}" if parent_id and parent_id != 'root' else "root"
        url = f"https://graph.microsoft.com/v1.0/me/drive/{parent_path}/children"
        params = {"$filter": f"name eq '{safe_name}'"}
        
        try:
            response = http_requests.get(url, headers=headers, params=params, timeout=15)
            if response.status_code == 200:
                files = response.json().get('value', [])
                # Filter for folders only (Microsoft Graph returns 'folder' key for folders)
                folders = [f for f in files if 'folder' in f]
                if folders:
                    return {
                        'one_drive_file_id': folders[0].get('id'),
                        'one_drive_url': folders[0].get('webUrl', ''),
                    }
            return False
        except Exception as e:
            _logger.error("Error searching for folder %s: %s", folder_name, str(e))
            return False

    def find_or_create_folder(self, folder_name, parent_id, config):
        """Find a folder by name, or create it if it doesn't exist."""
        existing = self.find_folder_by_name(folder_name, parent_id, config)
        if existing:
            return existing
        return self.create_folder_in_drive(folder_name, config, parent_one_drive_id=parent_id)

    def upload_file_to_drive(self, file_name, file_content, mime_type, config, parent_one_drive_id=None):
        """Upload a file to OneDrive with optional parent folder placement.
        Automatically switches to resumable upload for large files (>= 4 MB).
        Microsoft Graph recommended threshold for resumable uploads is 4MB.
        """
        RESUMABLE_THRESHOLD = 4 * 1024 * 1024  # 4 MB
        if isinstance(file_content, (bytes, bytearray)) and len(file_content) >= RESUMABLE_THRESHOLD:
            return self.upload_file_to_drive_resumable(
                file_name, file_content, mime_type, config, parent_one_drive_id=parent_one_drive_id
            )

        access_token = self._get_access_token(config)
        if not access_token:
            return False

        # Simple upload using PUT /content
        # Ref: https://learn.microsoft.com/en-us/graph/api/driveitem-put-content
        parent_path = f"items/{parent_one_drive_id}" if parent_one_drive_id and parent_one_drive_id != 'root' else "root"
        url = f"https://graph.microsoft.com/v1.0/me/drive/{parent_path}:/{file_name}:/content"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": mime_type or 'application/octet-stream'
        }
        
        try:
            response = http_requests.put(url, headers=headers, data=file_content, timeout=60)
            if response.status_code in (200, 201):
                data = response.json()
                return {
                    'one_drive_file_id': data.get('id'),
                    'one_drive_url': data.get('webUrl', ''), # webUrl is standard in Graph
                    'mime_type': data.get('file', {}).get('mimeType', mime_type),
                }
            _logger.warning("Failed to upload file to Drive: %s", response.text)
        except Exception as e:
            _logger.error("Error during simple upload to Drive: %s", str(e))
        return False

    def upload_file_to_drive_resumable(self, file_name, file_content, mime_type, config, parent_one_drive_id=None):
        """Upload a large file to OneDrive using the resumable upload API.
        The resumable upload API is designed for larger files (Graph recommends > 4MB).
        It initiates an upload session and streams the file.
        """
        access_token = self._get_access_token(config)
        if not access_token:
            return False

        # Step 1: Initiate a resumable session
        parent_path = f"items/{parent_one_drive_id}" if parent_one_drive_id and parent_one_drive_id != 'root' else "root"
        url = f"https://graph.microsoft.com/v1.0/me/drive/{parent_path}:/{file_name}:/createUploadSession"
        
        try:
            init_resp = http_requests.post(
                url,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
                timeout=30,
            )
            if init_resp.status_code not in (200, 201):
                _logger.warning("Failed to initiate resumable upload session for %s: %s", file_name, init_resp.text)
                return False
            
            upload_url = init_resp.json().get('uploadUrl')
            if not upload_url:
                _logger.warning("No upload URL returned for resumable session of %s", file_name)
                return False
                
            # Step 2: Stream the file in one chunk for simplicity
            size = len(file_content)
            stream_headers = {
                "Content-Length": str(size),
                "Content-Range": f"bytes 0-{size-1}/{size}"
            }
            resp = http_requests.put(upload_url, data=file_content, headers=stream_headers, timeout=300)
            
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    'one_drive_file_id': data.get('id'),
                    'one_drive_url': data.get('webUrl', ''),
                    'mime_type': data.get('file', {}).get('mimeType', mime_type),
                }
            _logger.warning("Failed to stream resumable upload for %s: %s", file_name, resp.text)
            return False
        except Exception as e:
            _logger.error("Error during resumable upload of %s: %s", file_name, str(e))
            return False
        except Exception as e:
            _logger.error("Error during resumable upload of %s: %s", file_name, str(e))
            return False


    # ─── Drive → Odoo: Backward sync ───

    def fetch_and_sync_files(self):
        configs = self.env['one.drive.config'].search([('active', '=', True)])
        for config in configs:
            for root in config.root_ids.filtered(lambda r: r.active):
                self._sync_config_files(config, root_folder_id=root.id, one_drive_parent_id=root.root_id)

    def _sync_config_files(self, config, root_folder_id=False, one_drive_parent_id=False):
        """Iterative sync with incremental commits for large drive support."""
        access_token = self._get_access_token(config)
        if not access_token:
            return

        headers = {"Authorization": f"Bearer {access_token}"}

        # Work stack stores: (odoo_parent_id, google_parent_id)
        # We start with the root configuration
        # For OneDrive, if no parent ID, use 'root'
        start_id = one_drive_parent_id if one_drive_parent_id else 'root'
        work_stack = [(False, start_id)]
        
        # Track total files processed for logging
        total_files_processed = 0

        while work_stack:
            parent_folder_id, current_drive_parent = work_stack.pop()

            # Step 1: Fetch all remote children for the current OneDrive folder
            synced_drive_ids = []
            files_batch_count = 0

            url = f"https://graph.microsoft.com/v1.0/me/drive/items/{current_drive_parent}/children"
            if current_drive_parent == 'root':
                url = "https://graph.microsoft.com/v1.0/me/drive/root/children"

            while url:
                try:
                    response = http_requests.get(url, headers=headers, timeout=30)
                    if response.status_code != 200:
                        _logger.warning("OneDrive API error for parent %s: %s", current_drive_parent, response.text)
                        break
                except Exception as e:
                    _logger.error("Network error during sync of parent %s: %s", current_drive_parent, str(e))
                    break

                result = response.json()
                files_batch = result.get('value', [])
                files_batch_count += len(files_batch)

                for file_data in files_batch:
                    ms_id = file_data.get('id')
                    synced_drive_ids.append(ms_id)

                    # Process the single file/folder record (create or update)
                    is_folder = 'folder' in file_data
                    explorer_record = self._process_drive_file(
                        file_data, config, root_folder_id, parent_folder_id, headers
                    )
                    
                    total_files_processed += 1

                    # If it's a folder, push it onto the stack to process its children later
                    if is_folder and explorer_record:
                        work_stack.append((explorer_record.id, ms_id))

                url = result.get('@odata.nextLink')

            # Step 2: Cleanup stale records for THIS SPECIFIC folder only
            # This is more efficient than a full drive cleanup in a single pass
            stale_domain = [
                ('drive_config_id', '=', config.id),
                ('root_folder_id', '=', root_folder_id),
                ('parent_folder_id', '=', parent_folder_id),
                ('one_drive_file_id', '!=', False),
            ]
            if synced_drive_ids:
                stale_domain.append(('one_drive_file_id', 'not in', synced_drive_ids))

            stale_records = self.env['one.drive.file'].sudo().search(stale_domain)
            if stale_records:
                _logger.info("Sync: Removing %d stale record(s) from Folder %s", len(stale_records), parent_folder_id)
                stale_records.unlink()

            # Step 3: Incremental Commit
            # This ensures progress is persistent even if a timeout occurs afterwards
            self.env.cr.commit()
            _logger.info("Sync: Completed Folder %s (%s). Processed %d files. Total: %d. Progress Saved.", 
                        current_drive_parent, parent_folder_id, files_batch_count, total_files_processed)

            # Step 4: Notify the frontend about the updated folder
            # This allows real-time UI refresh per folder
            self._notify_folder_sync(parent_folder_id)

    def _notify_folder_sync(self, folder_id):
        """Send a notification to the Odoo bus for real-time UI refresh."""
        try:
            # Broadcast to the general channel so all active explorers refresh
            self.env['bus.bus']._sendone('one.drive.sync', 'one.drive.sync', {
                'folder_id': folder_id or False,
                'type': 'folder_synced'
            })
        except Exception as e:
            _logger.warning("Failed to send bus notification: %s", str(e))

    def _process_drive_file(self, file_data, config, root_folder_id, parent_folder_id, headers):
        ms_id = file_data.get('id')
        name = file_data.get('name')
        # Microsoft Graph has different structure for mime types
        mimetype = file_data.get('file', {}).get('mimeType', 'application/octet-stream')
        if 'folder' in file_data:
            mimetype = 'application/vnd.google-apps.folder' # Keep this for Odoo internal folder logic
            is_folder = True
        else:
            is_folder = False
            
        size = int(file_data.get('size', 0))
        # Microsoft Graph uses hashes (e.g. quickXorHash or sha1Hash)
        hash_data = file_data.get('file', {}).get('hashes', {})
        md5_checksum = hash_data.get('quickXorHash') or hash_data.get('sha1Hash')
        
        url = file_data.get('webUrl')

        owner_data = file_data.get('createdBy', {}).get('user', {})
        owner_name = owner_data.get('displayName', 'Me')

        modified_time = file_data.get('lastModifiedDateTime')
        last_modified = False
        if modified_time:
            last_modified = modified_time.replace('T', ' ').replace('Z', '')
            if '.' in last_modified:
                last_modified = last_modified[:last_modified.index('.')]

        is_starred = file_data.get('starred', False)

        # Search for an existing record — use one_drive_file_id + drive_config_id
        # as the unique key. Do NOT include root_folder_id in the primary search
        # because forward-sync and backward-sync may assign different root IDs,
        # leading to duplicate records.
        explorer_record = self.env['one.drive.file'].sudo().search([
            ('one_drive_file_id', '=', ms_id),
            ('drive_config_id', '=', config.id),
        ], limit=1)

        vals = {
            'name': name,
            'drive_config_id': config.id,
            'root_folder_id': root_folder_id,
            'file_type': 'folder' if is_folder else 'file',
            'mime_type': mimetype,
            'file_size': size,
            'md5_checksum': md5_checksum,
            'one_drive_file_id': ms_id,
            'one_drive_url': url,
            'parent_folder_id': parent_folder_id,
            'owner_name': owner_name,
            'last_modified': last_modified,
            'starred': False,
            'last_synced': fields.Datetime.now(),
            'sync_state': 'synced',
        }

        is_new = not explorer_record
        if explorer_record:
            explorer_record.write(vals)
        else:
            explorer_record = self.env['one.drive.file'].sudo().create(vals)

        # Log the sync operation (only for new files to avoid flooding logs)
        if is_new:
            root_name = False
            folder_path_val = False
            if root_folder_id:
                root_rec = self.env['one.drive.root.folder'].sudo().browse(root_folder_id)
                root_name = root_rec.name if root_rec.exists() else False
            # Build full folder path by walking the parent chain of the new record.
            # This ensures "Folder Path" in the Activity Log shows the complete hierarchy
            # (e.g. "Invoices / move_idINV / 2025 / 0001") for every Drive→Odoo sync entry.
            try:
                _, folder_path_val = self._get_file_info(explorer_record)
            except Exception:
                folder_path_val = False

            # Preserve sync_type from context so cron runs are labelled "Scheduled (Cron)"
            # instead of falling back to "Manual Sync".
            sync_type_ctx = self.env.context.get('sync_type', 'auto')
            self._log(config, name, 'sync',
                      file_type='folder' if is_folder else 'file',
                      root_folder_name=root_name,
                      folder_path=folder_path_val,
                      one_drive_file_id=ms_id,
                      file_size=size,
                      sync_type=sync_type_ctx)

        # NOTE: File downloads during sync have been removed for performance.
        # Files are downloaded on-demand when explicitly accessed/viewed by the user.
        # This keeps sync fast, especially for large folders with many files.

        return explorer_record

    # ─── OneDrive Permissions API ───

    def get_file_permissions(self, file_record):
        """Get all permissions for a file/folder on OneDrive."""
        if not file_record.one_drive_file_id:
            return {'permissions': [], 'generalAccess': 'restricted', 'role': 'reader'}

        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            return {'error': 'Could not get access token'}

        headers = {"Authorization": f"Bearer {access_token}"}
        # Microsoft Graph /permissions endpoint returns all permission objects
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}/permissions"

        try:
            response = http_requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                permissions = data.get('value', [])

                general_access = 'restricted'
                anyone_role = 'reader'
                people_permissions = []
                links_permissions = []

                for perm in permissions:
                    roles = perm.get('roles', [])
                    role_str = 'owner' if 'owner' in roles else ('writer' if 'write' in roles else 'reader')

                    # ── Sharing Links ──────────────────────────────────────
                    link_obj = perm.get('link')
                    if link_obj and link_obj.get('webUrl'):
                        scope = link_obj.get('scope', '')
                        link_type = link_obj.get('type', 'view')

                        if scope in ('anonymous', 'organization'):
                            general_access = 'anyone' if scope == 'anonymous' else 'organization'
                            anyone_role = 'writer' if link_type == 'edit' else 'reader'

                        # Collect granted identities that this link works for
                        link_recipients = []
                        for identity in perm.get('grantedToIdentities', []):
                            u = identity.get('user', {})
                            email = u.get('email') or u.get('userPrincipalName', '')
                            if email:
                                link_recipients.append(email)
                        for identity in perm.get('grantedToIdentitiesV2', []):
                            u = identity.get('user', {}) or identity.get('siteUser', {})
                            email = u.get('email') or u.get('loginName', '') or u.get('userPrincipalName', '')
                            if email and email not in link_recipients:
                                link_recipients.append(email)

                        links_permissions.append({
                            'id': perm.get('id'),
                            'url': link_obj.get('webUrl', ''),
                            'scope': scope,
                            'type': link_type,
                            'role': 'writer' if (link_type == 'edit' or 'write' in roles) else 'reader',
                            'preventsDownload': link_obj.get('preventsDownload', False),
                            'recipients': list(dict.fromkeys(link_recipients)),
                            'description': (
                                'Anyone can edit' if scope == 'anonymous' and link_type == 'edit'
                                else 'Anyone can view' if scope == 'anonymous'
                                else 'People in organization can edit' if scope == 'organization' and link_type == 'edit'
                                else 'People in organization can view' if scope == 'organization'
                                else 'People you specify can edit' if link_type == 'edit'
                                else 'People you specify can view'
                            ),
                        })
                        continue  # link perms handled — skip to next

                    # ── Direct People Permissions ─────────────────────────
                    # Graph returns grantedTo (v1) or grantedToV2 (newer)
                    def _extract_user(identity_obj):
                        if not identity_obj:
                            return None
                        u = (identity_obj.get('user')
                             or identity_obj.get('siteUser')
                             or identity_obj.get('group')
                             or identity_obj.get('remoteUser'))
                        return u

                    user_info = _extract_user(perm.get('grantedToV2')) or _extract_user(perm.get('grantedTo'))

                    # Also handle grantedToIdentitiesV2 (returned by /invite for external users)
                    if not user_info:
                        identities = perm.get('grantedToIdentitiesV2') or perm.get('grantedToIdentities') or []
                        if identities:
                            user_info = _extract_user(identities[0])

                    if user_info:
                        email = (user_info.get('email')
                                 or user_info.get('userPrincipalName')
                                 or user_info.get('loginName', ''))
                        display_name = user_info.get('displayName', email)

                        person = {
                            'id': perm.get('id'),
                            'type': 'user',
                            'role': role_str,
                            'emailAddress': email,
                            'displayName': display_name,
                            'photoLink': '',
                        }
                        if role_str == 'owner':
                            people_permissions.insert(0, person)
                        else:
                            people_permissions.append(person)

                # Update local record with synced state
                file_record.sudo().write({
                    'permission_type': general_access,
                    'anyone_role': anyone_role,
                })

                return {
                    'permissions': people_permissions,
                    'links': links_permissions,
                    'generalAccess': general_access,
                    'anyoneRole': anyone_role,
                    'writersCanShare': file_record.writers_can_share,
                    'copyRequiresWriterPermission': file_record.copy_requires_writer,
                }
            return {'error': f'HTTP {response.status_code}: {response.text}'}
        except Exception as e:
            _logger.error("Error getting OneDrive permissions: %s", str(e))
            return {'error': str(e)}



    def create_permission(self, file_record, email, role='reader', send_notification=True):
        """Add a permission (share with a person) on OneDrive using /invite."""
        if not file_record.one_drive_file_id:
            return {'error': 'File not synced to OneDrive'}

        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            return {'error': 'Could not get access token'}

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        # Microsoft Graph uses /invite to share with people
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}/invite"

        # Map Odoo roles to Graph roles
        graph_role = 'read' if role in ('reader', 'commenter') else 'write'
        
        body = {
            'recipients': [{'email': email}],
            'roles': [graph_role],
            'requireSignIn': True,
            'sendInvitation': send_notification,
        }

        t0 = time.time()
        try:
            response = http_requests.post(url, headers=headers, data=json.dumps(body), timeout=15)
            elapsed = time.time() - t0
            if response.status_code in (200, 201):
                self._log(config, f'{file_record.name} → shared with {email} ({role})',
                          'share_add',
                          file_type=file_record.file_type,
                          one_drive_file_id=file_record.one_drive_file_id,
                          duration=elapsed)
                return {
                    'success': True,
                    'permissions': response.json().get('value', [])
                }
            else:
                error_data = response.json() if response.content else {}
                error_msg = error_data.get('error', {}).get('message', response.text)
                _logger.warning("Failed to create permission: %s", error_msg)
                self._log(config, f'{file_record.name} → share with {email}',
                          'share_add', state='fail',
                          error_message=error_msg,
                          file_type=file_record.file_type,
                          one_drive_file_id=file_record.one_drive_file_id,
                          duration=elapsed)
                return {'error': error_msg}
        except Exception as e:
            _logger.error("Error creating permission: %s", str(e))
            self._log(config, f'{file_record.name} → share with {email}',
                      'share_add', state='fail',
                      error_message=str(e),
                      file_type=file_record.file_type,
                      one_drive_file_id=file_record.one_drive_file_id,
                      duration=time.time() - t0)
            return {'error': str(e)}

    def update_permission(self, file_record, permission_id, role):
        """Update a permission role on OneDrive."""
        if not file_record.one_drive_file_id:
            return {'error': 'File not synced to OneDrive'}

        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            return {'error': 'Could not get access token'}

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        t0 = time.time()
        try:
            # Get the existing permission details to inspect its type/scope
            get_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}/permissions/{permission_id}"
            get_resp = http_requests.get(get_url, headers=headers, timeout=15)
            
            is_link = False
            scope = None
            recipients = []
            
            if get_resp.status_code == 200:
                perm_data = get_resp.json()
                link_obj = perm_data.get('link')
                if link_obj:
                    is_link = True
                    scope = link_obj.get('scope')
                    
                    # Extract recipients
                    for identity in perm_data.get('grantedToIdentities', []):
                        u = identity.get('user', {})
                        email = u.get('email') or u.get('userPrincipalName')
                        if email:
                            recipients.append(email)
                    for identity in perm_data.get('grantedToIdentitiesV2', []):
                        u = identity.get('user', {}) or identity.get('siteUser', {})
                        email = u.get('email') or u.get('loginName') or u.get('userPrincipalName')
                        if email and email not in recipients:
                            recipients.append(email)
            else:
                error_data = get_resp.json() if get_resp.content else {}
                error_msg = error_data.get('error', {}).get('message', get_resp.text)
                return {'error': f'Failed to fetch permission details: {error_msg}'}
                
            if is_link:
                # 1. Delete the old permission link
                del_resp = http_requests.delete(get_url, headers=headers, timeout=15)
                if del_resp.status_code not in (200, 204):
                    error_data = del_resp.json() if del_resp.content else {}
                    error_msg = error_data.get('error', {}).get('message', del_resp.text)
                    return {'error': f'Failed to remove old sharing link: {error_msg}'}
                    
                # 2. Recreate link with the new role
                if scope == 'users':
                    # Use /invite endpoint
                    invite_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}/invite"
                    invite_body = {
                        'recipients': [{'email': r} for r in recipients],
                        'roles': ['read' if role in ('reader', 'commenter') else 'write'],
                        'requireSignIn': True,
                        'sendInvitation': False
                    }
                    response = http_requests.post(invite_url, headers=headers, data=json.dumps(invite_body), timeout=15)
                    if response.status_code in (200, 201):
                        self._log(config, f'{file_record.name} → link role updated (users) to {role}',
                                  'share_update', file_type=file_record.file_type,
                                  one_drive_file_id=file_record.one_drive_file_id, duration=time.time() - t0)
                        return {'success': True, 'permissions': response.json().get('value', [])}
                    else:
                        error_data = response.json() if response.content else {}
                        error_msg = error_data.get('error', {}).get('message', response.text)
                        return {'error': f'Failed to recreate users link: {error_msg}'}
                else:
                    # anonymous or organization -> use /createLink
                    create_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}/createLink"
                    create_body = {
                        'type': 'edit' if role == 'writer' else 'view',
                        'scope': 'anonymous' if scope == 'anonymous' else 'organization'
                    }
                    response = http_requests.post(create_url, headers=headers, data=json.dumps(create_body), timeout=15)
                    if response.status_code in (200, 201):
                        self._log(config, f'{file_record.name} → link role updated ({scope}) to {role}',
                                  'share_update', file_type=file_record.file_type,
                                  one_drive_file_id=file_record.one_drive_file_id, duration=time.time() - t0)
                        return {'success': True, 'permission': response.json()}
                    else:
                        error_data = response.json() if response.content else {}
                        error_msg = error_data.get('error', {}).get('message', response.text)
                        return {'error': f'Failed to recreate {scope} link: {error_msg}'}
            else:
                # Direct permission: PATCH roles
                graph_role = 'read' if role in ('reader', 'commenter') else 'write'
                body = {'roles': [graph_role]}
                response = http_requests.patch(get_url, headers=headers, data=json.dumps(body), timeout=15)
                if response.status_code == 200:
                    perm_data = response.json()
                    self._log(config, f'{file_record.name} → role changed to {role}',
                              'share_update',
                              file_type=file_record.file_type,
                              one_drive_file_id=file_record.one_drive_file_id,
                              duration=time.time() - t0)
                    return {'success': True, 'permission': perm_data}
                else:
                    error_data = response.json() if response.content else {}
                    error_msg = error_data.get('error', {}).get('message', response.text)
                    return {'error': error_msg}
        except Exception as e:
            _logger.error("Error updating permission: %s", str(e))
            return {'error': str(e)}



    def delete_permission(self, file_record, permission_id):
        """Delete a permission from a file on OneDrive."""
        if not file_record.one_drive_file_id:
            return {'error': 'File not synced to OneDrive'}

        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            self._log(config, file_record.name, 'share_remove', state='fail',
                      error_message='Could not get access token',
                      file_type=file_record.file_type,
                      one_drive_file_id=file_record.one_drive_file_id)
            return {'error': 'Could not get access token'}

        headers = {"Authorization": f"Bearer {access_token}"}
        url = (
            f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}"
            f"/permissions/{permission_id}"
        )

        t0 = time.time()
        try:
            response = http_requests.delete(url, headers=headers, timeout=15)
            elapsed = time.time() - t0
            if response.status_code in (200, 204):
                self._log(config, f'{file_record.name} → removed access (perm: {permission_id})',
                          'share_remove',
                          file_type=file_record.file_type,
                          one_drive_file_id=file_record.one_drive_file_id,
                          duration=elapsed)
                return {'success': True}
            else:
                error_data = response.json() if response.content else {}
                error_msg = error_data.get('error', {}).get('message', response.text)
                self._log(config, f'{file_record.name} → remove access',
                          'share_remove', state='fail',
                          error_message=error_msg,
                          file_type=file_record.file_type,
                          one_drive_file_id=file_record.one_drive_file_id,
                          duration=elapsed)
                return {'error': error_msg}
        except Exception as e:
            _logger.error("Error deleting permission: %s", str(e))
            self._log(config, f'{file_record.name} → remove access',
                      'share_remove', state='fail',
                      error_message=str(e),
                      file_type=file_record.file_type,
                      one_drive_file_id=file_record.one_drive_file_id,
                      duration=time.time() - t0)
            return {'error': str(e)}

    def set_general_access(self, file_record, access_type, role='reader', block_download=None, password=None, expirationDate=None):
        """Set general access to 'anyone', 'organization' or 'restricted' using /createLink or by deleting links."""
        if not file_record.one_drive_file_id:
            return {'error': 'File not synced to OneDrive'}

        config = file_record.drive_config_id
        access_token = self._get_access_token(config)
        if not access_token:
            return {'error': 'Could not get access token'}

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        if block_download is None:
            block_download = file_record.copy_requires_writer

        t0 = time.time()
        try:
            # 1. ALWAYS clean up existing 'anonymous' or 'organization' links first
            url_perms = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}/permissions"
            perm_resp = http_requests.get(url_perms, headers=headers, timeout=15)
            if perm_resp.status_code == 200:
                for perm in perm_resp.json().get('value', []):
                    link_scope = perm.get('link', {}).get('scope')
                    if link_scope in ('anonymous', 'organization'):
                        del_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}/permissions/{perm.get('id')}"
                        del_resp = http_requests.delete(del_url, headers=headers, timeout=15)
                        if del_resp.status_code not in (200, 204):
                            return {'error': f'Failed to remove existing {link_scope} link: {del_resp.text}'}
            else:
                return {'error': f'Failed to fetch permissions to clean up: {perm_resp.text}'}

            if access_type in ('anyone', 'organization'):
                # 2. Create the new link
                scope = 'anonymous' if access_type == 'anyone' else 'organization'
                url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_record.one_drive_file_id}/createLink"
                body = {
                    'type': 'edit' if role == 'writer' else 'view',
                    'scope': scope
                }
                if role == 'reader':
                    body['preventsDownload'] = bool(block_download)
                else:
                    body['preventsDownload'] = False
                
                if password:
                    body['password'] = password
                if expirationDate:
                    body['expirationDateTime'] = expirationDate + "T23:59:59Z"

                response = http_requests.post(url, headers=headers, data=json.dumps(body), timeout=15)
                elapsed = time.time() - t0
                if response.status_code in (200, 201):
                    data = response.json()
                    file_record.sudo().write({
                        'permission_type': access_type,
                        'anyone_role': role,
                        'copy_requires_writer': bool(block_download) if role == 'reader' else False,
                        'one_drive_url': data.get('link', {}).get('webUrl', file_record.one_drive_url)
                    })
                    self._log(config, f'{file_record.name} → general access: {access_type} ({role}, block_download={block_download})',
                              'share_general', file_type=file_record.file_type,
                              one_drive_file_id=file_record.one_drive_file_id, duration=elapsed)
                    return data
                else:
                    err_msg = response.text
                    self._log(config, f'Failed to set general access for {file_record.name}: {err_msg}',
                              'share_general', file_type=file_record.file_type, duration=elapsed, state='failed')
                    return {'error': f'Microsoft Graph Error: {err_msg}'}
            else:
                # restricted: we already deleted the broad links
                file_record.sudo().write({
                    'permission_type': 'restricted',
                    'copy_requires_writer': False
                })
                self._log(config, f'{file_record.name} → general access: restricted',
                          'share_general', file_type=file_record.file_type,
                          one_drive_file_id=file_record.one_drive_file_id, duration=time.time() - t0)
                return {'success': True}

        except Exception as e:
            _logger.error("Error setting general access: %s", str(e))
            return {'error': str(e)}

    def update_file_sharing_settings(self, file_record, writers_can_share=None, copy_requires_writer=None):
        """Update file sharing settings on OneDrive. Persist locally since Graph permissions are link/role-based."""
        vals = {}
        if writers_can_share is not None:
            vals['writers_can_share'] = writers_can_share
        if copy_requires_writer is not None:
            vals['copy_requires_writer'] = copy_requires_writer
        if vals:
            file_record.sudo().write(vals)
        return {'success': True}

