# -*- coding: utf-8 -*-
import base64, io, json, logging, time
import requests as http_requests
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

NEXTCLOUD_API  = "https://api.nextcloudapi.com/2"
NEXTCLOUD_CONTENT = "https://content.nextcloudapi.com/2"
TOKEN_URL = "https://api.nextcloudapi.com/oauth2/token"

WEBDAV_NS = "DAV:"                        # WebDAV XML namespace
WEBDAV_OC_NS = "http://owncloud.org/ns"   # AWS S3/OwnCloud extension namespace


class NextcloudSync(models.Model):
    _name = "nextcloud.sync"
    _description = "AWS S3 Synchronization Logic"

    # ─── Logging Helpers ───────────────────────────────────────────────────────

    def _get_file_info(self, file_record):
        root_name = folder_path = ""
        try:
            if file_record.root_folder_id:
                root_name = file_record.root_folder_id.name or ""
            parts = []
            parent = file_record.parent_folder_id
            while parent:
                parts.append(parent.name or "")
                parent = parent.parent_folder_id
            if parts:
                folder_path = " / ".join(reversed(parts))
        except Exception:
            pass
        return root_name, folder_path

    def _log(self, config, file_name, operation, state="success",
             error_message=False, file_type="file", root_folder_name=False,
             folder_path=False, nextcloud_file_id=False, file_size=0, duration=0,
             sync_type=None, **kwargs):
        real_uid = self.env.uid
        effective_sync_type = sync_type or self.env.context.get("sync_type", "auto")
        self.env["nextcloud.sync.log"].log_operation(
            config=config, file_name=file_name, operation=operation,
            state=state, error_message=error_message,
            sync_type=effective_sync_type, file_type=file_type,
            root_folder_name=root_folder_name, folder_path=folder_path,
            nextcloud_file_id=nextcloud_file_id, file_size=file_size,
            duration=duration, user_id=kwargs.get("user_id") or real_uid,
        )

    def _log_file(self, config, file_record, file_name, operation, **kwargs):
        root_name, fpath = self._get_file_info(file_record)
        kwargs.setdefault("file_type", file_record.file_type or "file")
        kwargs.setdefault("nextcloud_file_id", file_record.nextcloud_file_id or "")
        kwargs.setdefault("root_folder_name", root_name)
        kwargs.setdefault("folder_path", fpath)
        self._log(config, file_name, operation, **kwargs)

    # ─── Authentication ─────────────────────────────────────────────────────────

    def _get_access_token(self, config):
        """Return AWS S3 access token, or None for Basic Auth configs."""
        if not config.refresh_token:
            return False
        # Basic Auth mode — credentials are stored as client_id/client_secret.
        # No OAuth token exchange is needed.
        if config.refresh_token == "basic_auth_connected":
            _logger.debug(
                "Config '%s' uses Basic Auth — skipping OAuth token fetch.", config.name
            )
            return None
        # Legacy OAuth mode
        data = {
            "client_id": config.aws_access_key_id,
            "client_secret": config.aws_secret_access_key,
            "refresh_token": config.refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            response = http_requests.post(TOKEN_URL, data=data, timeout=15)
            if response.status_code == 200:
                return response.json().get("access_token")
            _logger.warning("Failed to get AWS S3 access token: %s", response.text)
        except Exception as e:
            _logger.error("Error fetching AWS S3 access token: %s", e)
        return False

    def _dbx_headers(self, access_token):
        return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    def _get_s3_client(self, config):
        import boto3
        return boto3.client(
            's3',
            aws_access_key_id=(config.aws_access_key_id or "").strip(),
            aws_secret_access_key=(config.aws_secret_access_key or "").strip(),
            region_name=(config.aws_region or "").strip()
        )

    def _s3_format_url(self, config, remote_path):
        bucket = (config.aws_s3_bucket_name or "").strip()
        region = (config.aws_region or "").strip()
        path = str(remote_path).lstrip("/")
        return f"https://{bucket}.s3.{region}.amazonaws.com/{path}"

    def _webdav_list(self, config, remote_path="/"):
        """List objects in S3 simulating a WebDAV PROPFIND."""
        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or "").strip()
        prefix = str(remote_path).strip("/")
        if prefix:
            prefix += "/"
            
        entries = []
        try:
            paginator = s3.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter='/'):
                # Folders
                for prefix_obj in page.get('CommonPrefixes', []):
                    folder_path = prefix_obj['Prefix'].strip("/")
                    name = folder_path.split("/")[-1]
                    decoded_remote = "/" + folder_path
                    entries.append({
                        "name": name,
                        "path": decoded_remote,
                        "remote_path": decoded_remote,
                        "is_folder": True,
                        "size": 0,
                        "last_modified": "",
                        "fileid": decoded_remote,
                        "etag": "",
                    })
                # Files
                for obj in page.get('Contents', []):
                    if obj['Key'].endswith('/'):
                        continue  # Skip 0-byte folder objects, they are returned in CommonPrefixes
                    file_path = obj['Key'].strip("/")
                    if file_path == prefix.strip("/"):  # Skip the folder itself
                        continue
                    name = file_path.split("/")[-1]
                    decoded_remote = "/" + file_path
                    entries.append({
                        "name": name,
                        "path": decoded_remote,
                        "remote_path": decoded_remote,
                        "is_folder": False,
                        "size": obj['Size'],
                        "last_modified": str(obj['LastModified']),
                        "fileid": decoded_remote,
                        "etag": obj['ETag'].strip('"'),
                    })
        except Exception as e:
            _logger.error("S3 List error at %s: %s", prefix, e)
        return entries

    def _webdav_mkdir(self, config, remote_path):
        """Create a folder marker in S3 (idempotent 0-byte object with trailing slash).
        S3 put_object is always idempotent — calling this multiple times is safe.
        """
        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or "").strip()
        if not bucket:
            raise Exception("AWS S3 Bucket Name is missing. Please configure it in Settings > AWS S3 Integration.")
        prefix = str(remote_path).strip("/") + "/"
        try:
            s3.put_object(Bucket=bucket, Key=prefix, Body=b"")
            _logger.info("S3 folder marker ensured: s3://%s/%s", bucket, prefix)
            return True
        except Exception as e:
            _logger.error("S3 MKDIR error at %s: %s", prefix, e)
            raise Exception(str(e))

    # ─── Preview URL ────────────────────────────────────────────────────────────

    @api.model
    def get_preview_url(self, file_id, config_id):
        """Generate and return the final preview URL for the given S3 file.

        Returns the URL directly so the JS iframe loads it without any redirect
        chain (redirects inside iframes are unreliable cross-origin and cause
        browsers to download the file instead of previewing it).

        Strategy:
          Office docs  -> Microsoft Office Online Viewer (embed.aspx)
          PDF / images -> presigned S3 URL with inline Content-Disposition
          Text / code  -> presigned S3 URL with inline Content-Disposition
          Others       -> False  (dialog shows 'Open in AWS S3' fallback)
        """
        import os
        import urllib.parse
        import mimetypes

        file_record = self.env["nextcloud.file"].sudo().search(
            [("nextcloud_file_id", "=", file_id)], limit=1
        )
        if not file_record or not file_record.nextcloud_file_id:
            return False

        _, ext = os.path.splitext((file_record.name or "").lower())

        office_exts = {
            '.doc', '.docx', '.docm',
            '.ppt', '.pps', '.ppsx', '.ppsm', '.pptx', '.pptm',
            '.xls', '.xlsx', '.xlsm', '.rtf',
        }
        # CSV is rendered via Odoo's own csv-preview route (styled HTML table).
        # This avoids external viewers that cannot access private S3 buckets.
        csv_exts = {'.csv'}
        inline_exts = {
            '.pdf',
            '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.ico', '.tiff',
            '.txt', '.log', '.md', '.json', '.xml', '.yaml', '.yml', '.ini', '.cfg',
            '.html', '.htm', '.css', '.js',
            '.mp4', '.webm', '.ogv', '.ogg', '.mp3', '.wav',
        }

        config = file_record.drive_config_id
        if not config:
            return False

        try:
            s3 = self._get_s3_client(config)
            bucket = (config.aws_s3_bucket_name or "").strip()
            s3_key = file_record.nextcloud_file_id.lstrip("/")
            if not s3_key:
                return False

            guessed_mime, _ = mimetypes.guess_type(file_record.name or "")
            content_type = file_record.mime_type or guessed_mime or "application/octet-stream"

            if ext in csv_exts:
                # Render via Odoo's own CSV table renderer — no external service.
                return f"/nextcloud/csv-preview/{file_record.id}"

            if ext in office_exts:
                # Route to Odoo-internal renderer — no external service needed.
                # Works with private S3 buckets since Odoo fetches via IAM credentials.
                if ext in {'.xls', '.xlsx', '.xlsm'}:
                    return f"/nextcloud/xlsx-preview/{file_record.id}"
                elif ext in {'.ppt', '.pps', '.ppsx', '.ppsm', '.pptx', '.pptm'}:
                    return f"/nextcloud/pptx-preview/{file_record.id}"
                else:  # .doc, .docx, .docm, .rtf
                    return f"/nextcloud/docx-preview/{file_record.id}"

            if ext in inline_exts or content_type.startswith(('image/', 'video/', 'audio/', 'text/')):
                # Return presigned URL directly — browser renders PDF/image natively.
                presigned = s3.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": bucket,
                        "Key": s3_key,
                        "ResponseContentType": content_type,
                        "ResponseContentDisposition": f'inline; filename="{file_record.name}"',
                    },
                    ExpiresIn=3600,
                )
                return presigned

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                "get_preview_url: failed for '%s': %s", file_record.name, e
            )
        return False

    # ─── Upload helpers ─────────────────────────────────────────────────────────

    def _resolve_parent_path(self, parent_id, access_token=None):
        return parent_id or ""

    def upload_file(self, attachment):
        pass # Optional/legacy, not directly used in the new flow

    # ─── Upload to Drive (called from ir.attachment) ────────────────────────────

    def upload_file_to_drive(self, file_name, file_content, mime_type, config,
                             parent_nextcloud_id=None, conflict_behavior="rename", file_record=None):
        """Upload a file to AWS S3 via boto3."""
        import urllib.parse

        # Build the correct AWS S3 path using the Odoo record hierarchy
        remote_parent = ""
        if file_record:
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
                
            remote_parent = "/" + "/".join(path_parts) if path_parts else "/"
        else:
            if parent_nextcloud_id and not str(parent_nextcloud_id).startswith('/'):
                remote_parent = self._get_remote_path_from_id(parent_nextcloud_id)
            else:
                remote_parent = parent_nextcloud_id or "/"

        remote_parent = urllib.parse.unquote(str(remote_parent)).rstrip("/")
        if not remote_parent.startswith('/'):
            remote_parent = f'/{remote_parent}'
        remote_path = f"{remote_parent}/{file_name}"
        
        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or "").strip()
        key = remote_path.strip("/")

        try:
            s3.put_object(Bucket=bucket, Key=key, Body=file_content, ContentType=mime_type)
            web_url = self._s3_format_url(config, remote_path)
            return {"nextcloud_file_id": remote_path, "nextcloud_url": web_url, "mime_type": mime_type}
        except Exception as e:
            raise Exception(f"AWS S3 upload failed: {e}")

    def upload_file_to_drive_resumable(self, file_name, file_content, mime_type,
                                       config, parent_nextcloud_id=None):
        return self.upload_file_to_drive(file_name, file_content, mime_type, config, parent_nextcloud_id)

    # ─── File Management ────────────────────────────────────────────────────────

    def _get_remote_path_from_id(self, file_id, override_name=None):
        import urllib.parse
        file_id = urllib.parse.unquote(str(file_id)) if file_id else ""

        if file_id.startswith("/"):
            clean_id = file_id.rstrip("/")
            if override_name:
                parent_dir = clean_id.rsplit("/", 1)[0] or "/"
                return (parent_dir.rstrip("/") + "/" + override_name).replace("//", "/")
            return clean_id

        record = self.env["nextcloud.file"].sudo().search([("nextcloud_file_id", "=", file_id)], limit=1)
        if record:
            parts = []
            parent = record.parent_folder_id
            while parent:
                parts.append(parent.name)
                parent = parent.parent_folder_id

            if record.root_folder_id:
                root_path = record.root_folder_id.root_id or record.root_folder_id.name
                parts.append(root_path.strip("/"))

            segments = list(reversed(parts))
            remote_parent = "/".join(s.strip("/") for s in segments if s)
            remote_parent = "/" + remote_parent if remote_parent else "/"

            file_name = override_name or record.name
            return (remote_parent.rstrip("/") + "/" + file_name).replace("//", "/")

        return f"/{file_id}"

    def rename_file(self, file_id, new_name, config, current_path=None):
        """Rename a file/folder on AWS S3 via copy_object and delete_object."""
        import posixpath
        import urllib.parse

        rename_old_name = self.env.context.get("rename_old_name")
        if not current_path:
            current_path = self._get_remote_path_from_id(file_id, override_name=rename_old_name)

        current_path = urllib.parse.unquote(current_path).rstrip("/")
        parent_dir = posixpath.dirname(current_path) or "/"
        new_path   = (parent_dir.rstrip("/") + "/" + new_name).replace("//", "/")

        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or "").strip()
        
        src_key = current_path.strip("/")
        dst_key = new_path.strip("/")

        # Determine if folder
        is_folder = self.env.context.get("is_folder")
        if is_folder is None:
            record = self.env["nextcloud.file"].sudo().search([("nextcloud_file_id", "=", file_id)], limit=1)
            is_folder = (record.file_type == 'folder') if record else False

        try:
            if is_folder:
                src_prefix = src_key + "/"
                dst_prefix = dst_key + "/"
                paginator = s3.get_paginator('list_objects_v2')
                objects_to_delete = []
                
                for page in paginator.paginate(Bucket=bucket, Prefix=src_prefix):
                    for obj in page.get('Contents', []):
                        old_key = obj['Key']
                        new_obj_key = dst_prefix + old_key[len(src_prefix):]
                        s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': old_key}, Key=new_obj_key)
                        objects_to_delete.append({'Key': old_key})
                
                if objects_to_delete:
                    for i in range(0, len(objects_to_delete), 1000):
                        s3.delete_objects(Bucket=bucket, Delete={'Objects': objects_to_delete[i:i+1000]})
                else:
                    self._webdav_mkdir(config, new_path)
            else:
                s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': src_key}, Key=dst_key)
                s3.delete_object(Bucket=bucket, Key=src_key)
                
            return new_path
        except Exception as e:
            raise Exception(f"AWS S3 RENAME failed: {e}")

    def trash_file(self, file_id, config):
        return self.delete_file_from_drive(file_id, config)

    def delete_file_from_drive(self, file_id, config):
        import urllib.parse
        current_path = urllib.parse.unquote(self._get_remote_path_from_id(file_id))
        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or "").strip()
        key = current_path.strip("/")
        
        is_folder = self.env.context.get("is_folder")
        if is_folder is None:
            record = self.env["nextcloud.file"].sudo().search([("nextcloud_file_id", "=", file_id)], limit=1)
            is_folder = (record.file_type == 'folder') if record else False

        try:
            if is_folder:
                prefix = key + "/"
                paginator = s3.get_paginator('list_objects_v2')
                objects_to_delete = []
                for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                    for obj in page.get('Contents', []):
                        objects_to_delete.append({'Key': obj['Key']})
                if objects_to_delete:
                    for i in range(0, len(objects_to_delete), 1000):
                        s3.delete_objects(Bucket=bucket, Delete={'Objects': objects_to_delete[i:i+1000]})
            else:
                s3.delete_object(Bucket=bucket, Key=key)
            return True
        except Exception as e:
            _logger.error("AWS S3 DELETE failed for %s: %s", key, e)
            return False

    def move_file(self, file_id, new_parent_id, config, file_name=None):
        """Move a file/folder to a new parent directory on AWS S3."""
        import urllib.parse
        
        current_path = urllib.parse.unquote(self._get_remote_path_from_id(file_id))
        file_name = file_name or current_path.rstrip("/").split("/")[-1]

        if new_parent_id and new_parent_id != "root":
            parent_path = urllib.parse.unquote(self._get_remote_path_from_id(new_parent_id))
        else:
            parent_path = ""

        if parent_path and not parent_path.endswith("/") and "." in parent_path.split("/")[-1]:
            parent_path = "/".join(parent_path.split("/")[:-1])

        new_path = (parent_path.rstrip("/") + "/" + file_name).replace("//", "/")

        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or "").strip()
        
        src_key = current_path.strip("/")
        dst_key = new_path.strip("/")

        is_folder = self.env.context.get("is_folder")
        if is_folder is None:
            record = self.env["nextcloud.file"].sudo().search([("nextcloud_file_id", "=", file_id)], limit=1)
            is_folder = (record.file_type == 'folder') if record else False

        try:
            if is_folder:
                src_prefix = src_key + "/"
                dst_prefix = dst_key + "/"
                paginator = s3.get_paginator('list_objects_v2')
                objects_to_delete = []
                
                for page in paginator.paginate(Bucket=bucket, Prefix=src_prefix):
                    for obj in page.get('Contents', []):
                        old_key = obj['Key']
                        new_obj_key = dst_prefix + old_key[len(src_prefix):]
                        s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': old_key}, Key=new_obj_key)
                        objects_to_delete.append({'Key': old_key})
                
                if objects_to_delete:
                    for i in range(0, len(objects_to_delete), 1000):
                        s3.delete_objects(Bucket=bucket, Delete={'Objects': objects_to_delete[i:i+1000]})
                else:
                    self._webdav_mkdir(config, new_path)
            else:
                s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': src_key}, Key=dst_key)
                s3.delete_object(Bucket=bucket, Key=src_key)
                
            return new_path
        except Exception as e:
            raise Exception(f"AWS S3 MOVE failed: {e}")

    def create_folder_in_drive(self, folder_name, parent_id_or_config=None, config=None, parent_nextcloud_id=None, file_record=None, parent_path=None):
        if parent_id_or_config and isinstance(parent_id_or_config, models.Model) and parent_id_or_config._name == 'nextcloud.config':
            resolved_config = parent_id_or_config
            resolved_parent_id = parent_nextcloud_id
        else:
            resolved_config = config or parent_id_or_config
            resolved_parent_id = parent_id_or_config if not isinstance(parent_id_or_config, models.Model) else parent_nextcloud_id

        remote_parent = parent_path or resolved_parent_id or ""
        remote_path = remote_parent.rstrip("/") + "/" + folder_name

        try:
            ok = self._webdav_mkdir(resolved_config, remote_path)
        except Exception as e:
            raise Exception(f"Failed: {e}")

        if ok:
            existing = self.find_folder_by_name(folder_name, remote_parent or "/", resolved_config)
            if existing:
                return existing
            return {
                "nextcloud_file_id": remote_path,
                "name":              folder_name,
                "remote_path":       remote_path,
                "nextcloud_url":     self._s3_format_url(resolved_config, remote_path)
            }
        raise Exception(f"AWS S3 folder creation failed for path: {remote_path}")

    def find_folder_by_name(self, folder_name, parent_path, config):
        entries = self._webdav_list(config, remote_path=parent_path or "/")
        for entry in entries:
            if entry["is_folder"] and entry["name"].lower() == folder_name.lower():
                remote_path = entry["remote_path"]
                return {
                    "nextcloud_file_id": remote_path,
                    "name":              entry["name"],
                    "remote_path":       remote_path,
                    "nextcloud_url":     self._s3_format_url(config, remote_path)
                }
        return False

    def find_or_create_folder(self, folder_name, parent_path, config):
        """Ensure a folder exists on AWS S3 and return its metadata dict.

        Strategy:
        1. Always write the S3 folder marker (idempotent put_object) — this is
           the only reliable way to guarantee the folder exists in the bucket,
           even if no list call has seen it yet.
        2. After writing the marker, confirm via list so we return the canonical
           remote_path including any casing the bucket actually has.
        3. Fall back to the computed path if list still returns nothing (e.g. a
           freshly created bucket with no eventual-consistency propagation yet).
        """
        if parent_path and not str(parent_path).startswith('/'):
            parent_path = self._get_remote_path_from_id(parent_path)

        parent_path = (parent_path or "/").rstrip("/")
        if not parent_path.startswith('/'):
            parent_path = f'/{parent_path}'

        computed_path = parent_path.rstrip("/") + "/" + folder_name

        # ── Step 1: Write the folder marker (idempotent) ──────────────────────
        try:
            self._webdav_mkdir(config, computed_path)
        except Exception as e:
            _logger.error("find_or_create_folder: could not write marker for '%s': %s", computed_path, e)
            raise

        # ── Step 2: Confirm existence via list and return canonical metadata ──
        confirmed = self.find_folder_by_name(folder_name, parent_path, config)
        if confirmed:
            return confirmed

        # ── Step 3: Fallback — return the computed path we just wrote ─────────
        _logger.info(
            "find_or_create_folder: list did not return '%s' yet (S3 eventual consistency); "
            "using computed path.", computed_path
        )
        return {
            "nextcloud_file_id": computed_path,
            "name":              folder_name,
            "remote_path":       computed_path,
            "nextcloud_url":     self._s3_format_url(config, computed_path),
        }


    # ─── Backward Sync (AWS S3 → Odoo) ─────────────────────────────────────────

    @api.model
    def fetch_and_sync_files(self):
        """Cron entry point — sync all active drives."""
        configs = self.env["nextcloud.config"].sudo().search([("active", "=", True)])
        for config in configs:
            if not config.refresh_token:
                continue
            # 1. PUSH: Odoo to AWS S3
            try:
                self.env["nextcloud.file"].sudo().with_context(sync_type=self.env.context.get("sync_type", "auto")).sync_pending_to_drive(drive_config_id=config.id)
            except Exception as e:
                _logger.error("Push Sync error for config %s: %s", config.name, e)

            # 2. PULL: AWS S3 to Odoo
            for root in config.root_ids.filtered(lambda r: r.active):
                try:
                    # Ensure the root folder exists on S3 and root_id path is stored
                    result = self.find_or_create_folder(root.name, "/", config)
                    if result and result.get("remote_path"):
                        remote_path = result["remote_path"]
                        if root.root_id != remote_path:
                            root.sudo().write({"root_id": remote_path})
                    else:
                        remote_path = root.root_id or ("/" + root.name)

                    self._sync_config_files(config, root_folder_id=root.id,
                                            nextcloud_parent_id=remote_path)
                except Exception as e:
                    _logger.error("Sync error for root %s: %s", root.name, e)

    def _sync_config_files(self, config, root_folder_id=None, nextcloud_parent_id=None,
                           parent_local_id=None, depth=0, max_depth=8):
        """
        Recursively list AWS S3 WebDAV folder and upsert records into nextcloud.file.
        nextcloud_parent_id here is the REMOTE PATH (e.g. "/OdooRoot") not a file ID.
        """
        if depth > max_depth:
            return

        # Skip if config is not Basic Auth connected
        if not config.is_connected:
            return

        root_rec = self.env["nextcloud.root.folder"].sudo().browse(root_folder_id) \
            if root_folder_id else None

        # Determine the remote path to list
        remote_path = nextcloud_parent_id or "/"

        current_parent_id = parent_local_id or False

        entries = self._webdav_list(config, remote_path=remote_path)
        if not entries and depth == 0:
            _logger.info("WebDAV list returned no entries for path: %s", remote_path)
            return

        for entry in entries:
            self._process_drive_entry(
                entry, config, root_folder_id, current_parent_id
            )

        # Recurse into subfolders found on AWS S3
        local_subfolders = self.env["nextcloud.file"].sudo().search([
            ("root_folder_id",  "=", root_folder_id),
            ("parent_folder_id","=", current_parent_id),
            ("file_type",       "=", "folder"),
        ])
        for subfolder in local_subfolders:
            # nextcloud_file_id is the remote_path for WebDAV mode
            sub_remote_path = subfolder.nextcloud_file_id
            if sub_remote_path:
                self._sync_config_files(
                    config,
                    root_folder_id=root_folder_id,
                    nextcloud_parent_id=sub_remote_path,
                    parent_local_id=subfolder.id,
                    depth=depth + 1,
                    max_depth=max_depth,
                )

    def _process_drive_entry(self, entry, config, root_folder_id, parent_local_id):
        """Upsert an S3 entry into nextcloud.file, preventing duplicates.

        Lookup order to find an existing Odoo record:
        1. Exact match on (nextcloud_file_id, drive_config_id)  ← primary key
        2. Match on (name, parent_folder_id, root_folder_id, drive_config_id, file_type)
           — catches records written by the attachment-sync path that may have
             stored a different ID string for the same logical file/folder.
        """
        import mimetypes

        name        = entry.get("name", "")
        remote_path = entry.get("remote_path", "")
        fileid      = entry.get("fileid") or remote_path
        is_folder   = entry.get("is_folder", False)
        size        = entry.get("size", 0) or 0
        modified    = entry.get("last_modified", "")
        etag        = entry.get("etag", "")

        if not name or not fileid:
            return

        web_url = self._s3_format_url(config, remote_path)
        file_type_val = "folder" if is_folder else "file"

        # ── 1. Primary lookup: exact nextcloud_file_id ─────────────────────
        existing = self.env["nextcloud.file"].sudo().search([
            ("nextcloud_file_id", "=", fileid),
            ("drive_config_id",   "=", config.id),
        ], limit=1)

        # ── 2. Fallback lookup: same logical position by name + parent ──────
        if not existing:
            existing = self.env["nextcloud.file"].sudo().search([
                ("name",             "=", name),
                ("drive_config_id",  "=", config.id),
                ("root_folder_id",   "=", root_folder_id or False),
                ("parent_folder_id", "=", parent_local_id or False),
                ("file_type",        "=", file_type_val),
            ], limit=1)

        guessed_mime, _ = mimetypes.guess_type(name)

        vals = {
            "name":              name,
            "file_type":         file_type_val,
            "mime_type":         guessed_mime or "application/octet-stream",
            "drive_config_id":   config.id,
            "root_folder_id":    root_folder_id,
            "parent_folder_id":  parent_local_id,
            "nextcloud_file_id": fileid,
            "nextcloud_url":     web_url,
            "file_size":         size,
            "md5_checksum":      etag,
            "sync_state":        "synced",
            "last_synced":       fields.Datetime.now(),
        }
        if modified:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(modified).replace(tzinfo=None)
                vals["last_modified"] = dt
            except Exception:
                try:
                    import dateutil.parser
                    vals["last_modified"] = dateutil.parser.parse(modified).replace(tzinfo=None)
                except Exception:
                    pass

        if existing:
            # Update in-place — never create a second record for the same entry
            existing.sudo().write(vals)
        else:
            try:
                self.env["nextcloud.file"].sudo().create(vals)
            except Exception as e:
                _logger.warning("Could not create file record '%s': %s", name, e)

    # ─── Real-time notifications ─────────────────────────────────────────────────

    def _notify_folder_sync(self, folder_id):
        self.env["bus.bus"]._sendone(
            self.env.user, "nextcloud.sync",
            {"type":"folder_sync","folder_id": folder_id}
        )


    # ─── Permissions / Sharing (AWS S3 OCS API) ─────────────────────────────

    def _ocs_base(self, config):
        """Return base URL for AWS S3 OCS Sharing API."""
        return ""

    def _ocs_headers(self):
        return {'OCS-APIRequest': 'true', 'Accept': 'application/json'}

    def _ocs_auth(self, config):
        return (config.aws_access_key_id or '', config.aws_secret_access_key or '')

    def _ocs_get_shares(self, path, config):
        """GET /shares?path=... — returns list of OCS share dicts for a remote path."""
        return []

    def _ocs_create_share(self, path, share_type, share_with='', permissions=17,
                          password=None, expire_date=None, config=None):
        """POST /shares — create a new OCS share. Returns share dict or None."""
        return None

    def _ocs_update_share(self, share_id, permissions=None, password=None, expire_date=None, config=None):
        """PUT /shares/{id} — update an existing OCS share."""
        return False

    def _ocs_delete_share(self, share_id, config):
        """DELETE /shares/{id} — revoke an OCS share."""
        return False

    def _perm_to_role(self, permissions):
        """Convert OCS permission bitmask to a role string."""
        try:
            p = int(permissions)
        except (TypeError, ValueError):
            return 'reader'
        # 2=update means write access
        return 'writer' if (p & 2) else 'reader'

    def _role_to_perm(self, role):
        """Convert role string to OCS permission bitmask."""
        # 1=read, 2=update, 4=create, 8=delete, 16=share
        if role in ('writer', 'editor'):
            return 31  # full access
        return 17  # read + re-share

    def get_file_permissions(self, file_id, config):
        """Return list of user/group shares for wizard display."""
        permissions = []
        # Show the logged-in Odoo user as the owner (not the raw AWS Access Key ID)
        owner_user = self.env.user
        owner_name = owner_user.name or config.name or 'Owner'
        owner_email = owner_user.email or config.name or ''
        permissions.append({
            'id': 'owner',
            'type': 'user',
            'emailAddress': owner_email,
            'displayName': owner_name,
            'role': 'owner',
        })
        return permissions

    def get_shared_links(self, file_id, config):
        """Return list of public-link shares for wizard display."""
        record = self.env["nextcloud.file"].sudo().search([("nextcloud_file_id", "=", file_id)], limit=1)
        if record and record.permission_type == 'anyone':
            url = self._get_share_link(self._get_remote_path_from_id(file_id), config)
            if url:
                return [{
                    'id': 'public_link',
                    'url': url,
                    'scope': 'anonymous',
                    'role': record.anyone_role or 'reader',
                    'description': 'Anyone with the link can view',
                    'recipients': [],
                }]
        return []

    def create_permission(self, file_id, email, role, config, send_notification=True):
        return self._fallback_share_via_link(self._get_remote_path_from_id(file_id), email, role, config)

    def update_permission(self, file_id, member_id, role, config):
        return True

    def delete_permission(self, file_id, member_id, config):
        return True

    def set_general_access(self, file_id, access_type, role, config,
                           block_download=None, password=None):
        path = self._get_remote_path_from_id(file_id)
        if access_type == 'restricted':
            return True
        link_url = self._get_share_link(path, config)
        return {'success': True, 'link': {'webUrl': link_url, 'url': link_url}}

    def share_file_to_email(self, file_id, email, role, config, message=''):
        return self._fallback_share_via_link(file_id, email, role, config, reason="Direct S3 shares unsupported")

    def _get_share_link(self, path, config):
        """Get or create a public share link for an AWS S3 path (Presigned URL).
        Expiry duration is taken from config.share_link_expiry_days (default 7, max 7 days).
        """
        if not path:
            return ''
        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or "").strip()
        key = str(path).strip("/")
        # AWS presigned URLs max out at 7 days; clamp between 1 and 7
        expiry_days = max(1, min(7, int(config.share_link_expiry_days or 7)))
        expiry_seconds = 3600 * 24 * expiry_days
        try:
            return s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket, 'Key': key},
                ExpiresIn=expiry_seconds
            )
        except Exception as e:
            import logging
            import traceback
            logging.getLogger(__name__).error("Failed to generate presigned URL for key %s: %s", key, e)
            try:
                with open('/tmp/s3_error.txt', 'w') as f:
                    f.write(f"Key: {key}\nBucket: {bucket}\nException: {str(e)}\n{traceback.format_exc()}")
            except:
                pass
            return ''

    def _fallback_share_via_link(self, path, email, role, config, reason=''):
        """Fallback: create a public link and notify via Odoo email."""
        _logger.info('Falling back to shared-link invite for %s (reason: %s)', email, reason)
        link_url = self._get_share_link(path, config)
        if not link_url:
            return {'ok': False, 'error': reason or 'Could not create a AWS S3 shared link.'}
        try:
            role_label = 'edit' if role in ('writer', 'editor') else 'view'
            self.env['mail.mail'].sudo().create({
                'subject': "You've been invited to access a shared AWS S3 file",
                'body_html': (
                    f"<div style='font-family:sans-serif;max-width:500px;'>"
                    f"<h3 style='color:#0082C9;'>File Shared With You</h3>"
                    f"<p>You have been granted <strong>{role_label}</strong> access.</p>"
                    f"<p><a href='{link_url}' style='background:#0082C9;color:#fff;"
                    f"padding:10px 20px;border-radius:6px;text-decoration:none;display:inline-block;"
                    f"margin:10px 0;'>Open in AWS S3</a></p>"
                    f"<p style='color:#888;font-size:12px;'>Shared via Odoo &amp; AWS S3 Integration</p>"
                    f"</div>"
                ),
                'email_to': email,
                'auto_delete': True,
            }).send()
        except Exception as e:
            _logger.warning('Fallback email send failed: %s', e)
            return {
                'ok': True, 'fallback': True, 'link': link_url,
                'warning': f'Shared link created but email failed: {str(e)[:100]}.',
            }
        return {
            'ok': True, 'fallback': True, 'link': link_url,
            'warning': (
                f'Direct AWS S3 invite not available ({reason[:80] if reason else "API limitation"}). '
                f'A shareable link was sent to {email} via email.'
            ),
        }

    def get_drive_quota(self, config):
        """Fetch AWS S3 storage quota (dummy unlimited for S3)."""
        return {
            'available': True,
            'limit_formatted': 'Unlimited',
            'usage_formatted': '0 B',
            'usage_pct': 0,
            'limit_bytes': 0,
            'usage_bytes': 0,
        }

    def download_file(self, file_id, config):
        """Download file bytes from AWS S3."""
        import urllib.parse
        current_path = urllib.parse.unquote(self._get_remote_path_from_id(file_id))
        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or "").strip()
        key = current_path.strip("/")

        try:
            response = s3.get_object(Bucket=bucket, Key=key)
            return response['Body'].read()
        except Exception as e:
            _logger.warning("S3 GET %s error: %s", key, e)
            return False

    def copy_file(self, file_id, new_name, parent_id, config):
        """Copy a file to a new location in AWS S3."""
        import urllib.parse
        current_path = urllib.parse.unquote(self._get_remote_path_from_id(file_id))
        
        if parent_id and parent_id != "root":
            parent_path = urllib.parse.unquote(self._get_remote_path_from_id(parent_id))
        else:
            parent_path = ""

        if parent_path and not parent_path.endswith("/") and "." in parent_path.split("/")[-1]:
            parent_path = "/".join(parent_path.split("/")[:-1])

        new_path = (parent_path.rstrip("/") + "/" + new_name).replace("//", "/")

        s3 = self._get_s3_client(config)
        bucket = (config.aws_s3_bucket_name or "").strip()
        
        src_key = current_path.strip("/")
        dst_key = new_path.strip("/")

        try:
            s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': src_key}, Key=dst_key)
            return {
                "nextcloud_file_id": "/" + dst_key,
                "name": new_name,
                "nextcloud_url": self._s3_format_url(config, new_path),
            }
        except Exception as e:
            _logger.error("S3 COPY failed: %s", e)
            return False
