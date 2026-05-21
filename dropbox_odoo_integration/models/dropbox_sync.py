# -*- coding: utf-8 -*-
import base64, io, json, logging, time
import requests as http_requests
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

DROPBOX_API  = "https://api.dropboxapi.com/2"
DROPBOX_CONTENT = "https://content.dropboxapi.com/2"
TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"


class GoogleDriveSync(models.Model):
    _name = "one.drive.sync"
    _description = "Dropbox Synchronization Logic"

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
             folder_path=False, one_drive_file_id=False, file_size=0, duration=0,
             sync_type=None, **kwargs):
        real_uid = self.env.uid
        effective_sync_type = sync_type or self.env.context.get("sync_type", "auto")
        self.env["one.drive.sync.log"].log_operation(
            config=config, file_name=file_name, operation=operation,
            state=state, error_message=error_message,
            sync_type=effective_sync_type, file_type=file_type,
            root_folder_name=root_folder_name, folder_path=folder_path,
            one_drive_file_id=one_drive_file_id, file_size=file_size,
            duration=duration, user_id=kwargs.get("user_id") or real_uid,
        )

    def _log_file(self, config, file_record, file_name, operation, **kwargs):
        root_name, fpath = self._get_file_info(file_record)
        kwargs.setdefault("file_type", file_record.file_type or "file")
        kwargs.setdefault("one_drive_file_id", file_record.one_drive_file_id or "")
        kwargs.setdefault("root_folder_name", root_name)
        kwargs.setdefault("folder_path", fpath)
        self._log(config, file_name, operation, **kwargs)

    # ─── Authentication ─────────────────────────────────────────────────────────

    def _get_access_token(self, config):
        """Refresh Dropbox access token using refresh_token."""
        if not config.refresh_token:
            return False
        data = {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "refresh_token": config.refresh_token,
            "grant_type": "refresh_token",
        }
        response = http_requests.post(TOKEN_URL, data=data)
        if response.status_code == 200:
            return response.json().get("access_token")
        _logger.warning("Failed to get Dropbox access token: %s", response.text)
        return False

    def _dbx_headers(self, access_token):
        return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    # ─── Preview URL ────────────────────────────────────────────────────────────

    @api.model
    def get_preview_url(self, file_id, config_id):
        """Return the internal Odoo preview route which forces inline display."""
        file_record = self.env["one.drive.file"].sudo().search([("one_drive_file_id", "=", file_id)], limit=1)
        if file_record:
            return f"/dropbox/preview/{file_record.id}"
        return False

    # ─── Upload helpers ─────────────────────────────────────────────────────────

    def _resolve_parent_path(self, parent_id, access_token=None):
        """Convert stored parent_id to a Dropbox path prefix for API calls."""
        if not parent_id or parent_id == "root":
            return ""
        # If it looks like a Dropbox ID (no leading /), fetch its absolute path
        if not parent_id.startswith("/"):
            if access_token:
                r = http_requests.post(
                    f"{DROPBOX_API}/files/get_metadata",
                    headers=self._dbx_headers(access_token),
                    json={"path": f"id:{parent_id}"},
                )
                if r.status_code == 200:
                    return r.json().get("path_display", "")
            return f"id:{parent_id}"
        return parent_id

    def upload_file(self, attachment):
        configs = self.env["one.drive.config"].search([("active","=",True),("readonly","=",False)])
        for config in configs:
            self._upload_via_requests(attachment, config)

    def _upload_via_requests(self, attachment, config):
        access_token = self._get_access_token(config)
        if not access_token:
            self._log(config, attachment.name, "upload", state="fail",
                      error_message="Could not obtain access token")
            return
        first_root = config.root_ids.filtered(lambda r: r.active)[:1]
        parent_path = f"id:{first_root.root_id}" if first_root and first_root.root_id else ""
        file_path = f"{parent_path}/{attachment.name}" if parent_path else f"/{attachment.name}"
        dbx_arg = json.dumps({"path": file_path, "mode": "add", "autorename": True})
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": dbx_arg,
        }
        t0 = time.time()
        try:
            response = http_requests.post(f"{DROPBOX_CONTENT}/files/upload",
                                          headers=headers, data=attachment.raw, timeout=60)
        except Exception as e:
            _logger.error("Network error uploading %s: %s", attachment.name, e)
            return
        elapsed = time.time() - t0
        if response.status_code == 200:
            data = response.json()
            file_id = data.get("id","").lstrip("id:")
            web_url = f"https://www.dropbox.com/home?select={data.get('path_display','')}"
            if file_id:
                self._register_uploaded_file(attachment, config, file_id, one_drive_url=web_url)
                self._log(config, attachment.name, "upload", one_drive_file_id=file_id,
                          file_size=len(attachment.raw) if attachment.raw else 0,
                          root_folder_name=first_root.name if first_root else False, duration=elapsed)
        else:
            self._log(config, attachment.name, "upload", state="fail",
                      error_message=f"HTTP {response.status_code}: {response.text}",
                      root_folder_name=first_root.name if first_root else False, duration=elapsed)

    def _register_uploaded_file(self, attachment, config, file_id, one_drive_url=False):
        attachment.with_context(skip_one_drive_sync=True).write({"one_drive_file_id": file_id})
        self.env["one.drive.file"].sudo().create({
            "name": attachment.name, "drive_config_id": config.id,
            "file_type": "file", "mime_type": attachment.mimetype,
            "one_drive_file_id": file_id,
            "one_drive_url": one_drive_url or f"https://www.dropbox.com/home?select={file_id}",
            "owner_name": self.env.user.name,
            "last_modified": fields.Datetime.now(),
            "sync_state": "synced", "last_synced": fields.Datetime.now(),
        })


    # ─── Upload to Drive (called from ir.attachment) ────────────────────────────

    def upload_file_to_drive(self, file_name, file_content, mime_type, config,
                             parent_one_drive_id=None, conflict_behavior="rename"):
        """Upload to Dropbox. Uses session upload for files > 4 MB."""
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        THRESHOLD = 4 * 1024 * 1024
        if len(file_content) > THRESHOLD:
            return self.upload_file_to_drive_resumable(
                file_name, file_content, mime_type, config, parent_one_drive_id
            )
        parent_path = self._resolve_parent_path(parent_one_drive_id, access_token) if parent_one_drive_id else ""
        file_path = f"{parent_path}/{file_name}" if parent_path else f"/{file_name}"
        mode = "add" if conflict_behavior == "rename" else "overwrite"
        dbx_arg = json.dumps({"path": file_path, "mode": mode, "autorename": True})
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": dbx_arg,
        }
        r = http_requests.post(f"{DROPBOX_CONTENT}/files/upload",
                               headers=headers, data=file_content, timeout=120)
        if r.status_code == 200:
            data = r.json()
            raw_id = data.get("id", "")
            clean_id = raw_id.lstrip("id:")
            path_display = data.get("path_display", "")
            web_url = self._get_share_link(clean_id, access_token) or                       f"https://www.dropbox.com/home?select={path_display}"
            return {"one_drive_file_id": clean_id, "one_drive_url": web_url,
                    "mime_type": mime_type}
        raise Exception(f"Dropbox upload failed (HTTP {r.status_code}): {r.text[:300]}")

    def _get_share_link(self, file_id, access_token):
        """Create or retrieve a shared link for a file."""
        r = http_requests.post(
            f"{DROPBOX_API}/sharing/create_shared_link_with_settings",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"path": f"id:{file_id}"},
        )
        if r.status_code == 200:
            return r.json().get("url", "")
        # If link already exists, fetch it
        if r.status_code == 409:
            r2 = http_requests.post(
                f"{DROPBOX_API}/sharing/list_shared_links",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"path": f"id:{file_id}", "direct_only": True},
            )
            if r2.status_code == 200:
                links = r2.json().get("links", [])
                if links:
                    return links[0].get("url", "")
        return ""

    def upload_file_to_drive_resumable(self, file_name, file_content, mime_type,
                                       config, parent_one_drive_id=None):
        """Dropbox session upload for files > 4 MB."""
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB chunks
        total_size = len(file_content)
        stream = io.BytesIO(file_content)

        # 1. Start upload session
        first_chunk = stream.read(CHUNK_SIZE)
        r = http_requests.post(
            f"{DROPBOX_CONTENT}/files/upload_session/start",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/octet-stream",
                "Dropbox-API-Arg": json.dumps({"close": False}),
            },
            data=first_chunk,
        )
        if r.status_code != 200:
            raise Exception(f"Dropbox session start failed (HTTP {r.status_code}): {r.text[:300]}")
        session_id = r.json().get("session_id")
        offset = len(first_chunk)

        # 2. Append remaining chunks
        while offset < total_size:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            is_last = (offset + len(chunk)) >= total_size
            if is_last:
                break
            r = http_requests.post(
                f"{DROPBOX_CONTENT}/files/upload_session/append_v2",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/octet-stream",
                    "Dropbox-API-Arg": json.dumps({"cursor": {"session_id": session_id, "offset": offset}, "close": False}),
                },
                data=chunk,
            )
            if r.status_code not in (200, 204):
                raise Exception(f"Dropbox append failed (HTTP {r.status_code}): {r.text[:300]}")
            offset += len(chunk)

        # 3. Finish
        stream.seek(offset)
        last_chunk = stream.read()
        parent_path = self._resolve_parent_path(parent_one_drive_id, access_token) if parent_one_drive_id else ""
        file_path = f"{parent_path}/{file_name}" if parent_path else f"/{file_name}"
        commit = {"path": file_path, "mode": "add", "autorename": True}
        r = http_requests.post(
            f"{DROPBOX_CONTENT}/files/upload_session/finish",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/octet-stream",
                "Dropbox-API-Arg": json.dumps({
                    "cursor": {"session_id": session_id, "offset": offset},
                    "commit": commit,
                }),
            },
            data=last_chunk,
        )
        if r.status_code == 200:
            data = r.json()
            raw_id = data.get("id", "")
            clean_id = raw_id.lstrip("id:")
            path_display = data.get("path_display", "")
            web_url = self._get_share_link(clean_id, access_token) or                       f"https://www.dropbox.com/home?select={path_display}"
            return {"one_drive_file_id": clean_id, "one_drive_url": web_url, "mime_type": mime_type}
        raise Exception(f"Dropbox finish failed (HTTP {r.status_code}): {r.text[:300]}")


    # ─── File Management ────────────────────────────────────────────────────────

    def rename_file(self, file_id, new_name, config, current_path=None):
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        if not current_path:
            # Get current path from metadata
            r = http_requests.post(
                f"{DROPBOX_API}/files/get_metadata",
                headers=self._dbx_headers(access_token),
                json={"path": f"id:{file_id}"},
            )
            if r.status_code != 200:
                return False
            current_path = r.json().get("path_display", "")
        import posixpath
        new_path = posixpath.join(posixpath.dirname(current_path), new_name)
        r = http_requests.post(
            f"{DROPBOX_API}/files/move_v2",
            headers=self._dbx_headers(access_token),
            json={"from_path": f"id:{file_id}", "to_path": new_path, "autorename": True},
        )
        return r.status_code == 200

    def trash_file(self, file_id, config):
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        r = http_requests.post(
            f"{DROPBOX_API}/files/delete_v2",
            headers=self._dbx_headers(access_token),
            json={"path": f"id:{file_id}"},
        )
        return r.status_code == 200

    def delete_file_from_drive(self, file_id, config):
        """Permanently delete a file from Dropbox (requires Dropbox Business or Admin)."""
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        # Try permanent delete first, fall back to trash
        r = http_requests.post(
            f"{DROPBOX_API}/files/permanently_delete",
            headers=self._dbx_headers(access_token),
            json={"path": f"id:{file_id}"},
        )
        if r.status_code in (200, 204):
            return True
        # Fallback: move to trash
        return self.trash_file(file_id, config)

    def move_file(self, file_id, new_parent_id, config, file_name=None):
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        if not file_name:
            r = http_requests.post(
                f"{DROPBOX_API}/files/get_metadata",
                headers=self._dbx_headers(access_token),
                json={"path": f"id:{file_id}"},
            )
            if r.status_code == 200:
                file_name = r.json().get("name", "file")
        parent_path = self._resolve_parent_path(new_parent_id, access_token) if new_parent_id else ""
        to_path = f"{parent_path}/{file_name}" if parent_path else f"/{file_name}"
        r = http_requests.post(
            f"{DROPBOX_API}/files/move_v2",
            headers=self._dbx_headers(access_token),
            json={"from_path": f"id:{file_id}", "to_path": to_path, "autorename": True},
        )
        return r.status_code == 200

    def create_folder_in_drive(self, folder_name, parent_id_or_config, config=None, parent_one_drive_id=None, file_record=None):
        """Create a folder in Dropbox and return its metadata dict.
        Supports both signatures:
        1. (folder_name, parent_id, config)
        2. (folder_name, config, parent_one_drive_id=..., file_record=...)
        """
        if parent_id_or_config and isinstance(parent_id_or_config, models.Model) and parent_id_or_config._name == 'one.drive.config':
            resolved_config = parent_id_or_config
            resolved_parent_id = parent_one_drive_id
        else:
            resolved_config = config
            resolved_parent_id = parent_id_or_config

        access_token = self._get_access_token(resolved_config)
        if not access_token:
            return False
        parent_path = self._resolve_parent_path(resolved_parent_id, access_token) if resolved_parent_id else ""
        folder_path = f"{parent_path}/{folder_name}" if parent_path else f"/{folder_name}"
        r = http_requests.post(
            f"{DROPBOX_API}/files/create_folder_v2",
            headers=self._dbx_headers(access_token),
            json={"path": folder_path, "autorename": False},
        )
        if r.status_code == 200:
            meta = r.json().get("metadata", {})
            raw_id = meta.get("id", "").lstrip("id:")
            return {
                "one_drive_file_id": raw_id,
                "name": meta.get("name", folder_name),
                "one_drive_url": f"https://www.dropbox.com/home{meta.get('path_display','')}",
            }
        # 409 = folder already exists
        if r.status_code == 409:
            return self.find_folder_by_name(folder_name, resolved_parent_id, resolved_config)
        raise Exception(f"Dropbox folder creation failed (HTTP {r.status_code}): {r.text[:300]}")


    def find_folder_by_name(self, folder_name, parent_id, config):
        """List parent folder contents and find a folder matching folder_name."""
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        parent_path = self._resolve_parent_path(parent_id, access_token) if parent_id else ""
        r = http_requests.post(
            f"{DROPBOX_API}/files/list_folder",
            headers=self._dbx_headers(access_token),
            json={"path": parent_path or "", "recursive": False},
        )
        if r.status_code != 200:
            return False
        entries = r.json().get("entries", [])
        for entry in entries:
            if entry.get(".tag") == "folder" and entry.get("name", "").lower() == folder_name.lower():
                raw_id = entry.get("id", "").lstrip("id:")
                return {
                    "one_drive_file_id": raw_id,
                    "name": entry.get("name", folder_name),
                    "one_drive_url": f"https://www.dropbox.com/home{entry.get('path_display','')}",
                }
        return False

    def find_or_create_folder(self, folder_name, parent_id, config):
        """Find folder by name or create it. Returns metadata dict."""
        existing = self.find_folder_by_name(folder_name, parent_id, config)
        if existing:
            return existing
        return self.create_folder_in_drive(folder_name, parent_id, config)


    # ─── Backward Sync (Dropbox → Odoo) ─────────────────────────────────────────

    @api.model
    def fetch_and_sync_files(self):
        """Cron entry point — sync all active drives."""
        configs = self.env["one.drive.config"].sudo().search([("active","=",True)])
        for config in configs:
            if not config.refresh_token:
                continue
            # 1. PUSH: Odoo to Dropbox
            try:
                self.env["one.drive.file"].sudo().with_context(sync_type=self.env.context.get("sync_type", "auto")).sync_pending_to_drive(drive_config_id=config.id)
            except Exception as e:
                _logger.error("Push Sync error for config %s: %s", config.name, e)

            # 2. PULL: Dropbox to Odoo
            for root in config.root_ids.filtered(lambda r: r.active):
                try:
                    self._sync_config_files(config, root_folder_id=root.id,
                                            one_drive_parent_id=root.root_id)
                except Exception as e:
                    _logger.error("Sync error for root %s: %s", root.name, e)

    def _sync_config_files(self, config, root_folder_id=None, one_drive_parent_id=None,
                           parent_local_id=None, depth=0, max_depth=8):
        if depth > max_depth:
            return
        access_token = self._get_access_token(config)
        if not access_token:
            return

        root_rec = self.env["one.drive.root.folder"].sudo().browse(root_folder_id)             if root_folder_id else None

        path = f"id:{one_drive_parent_id}" if one_drive_parent_id else ""
        cursor = None
        while True:
            if cursor:
                r = http_requests.post(
                    f"{DROPBOX_API}/files/list_folder/continue",
                    headers=self._dbx_headers(access_token),
                    json={"cursor": cursor},
                )
            else:
                r = http_requests.post(
                    f"{DROPBOX_API}/files/list_folder",
                    headers=self._dbx_headers(access_token),
                    json={"path": path, "recursive": False, "include_deleted": False},
                )
            if r.status_code != 200:
                _logger.warning("list_folder failed: %s", r.text[:200])
                break

            data = r.json()
            entries = data.get("entries", [])
            for entry in entries:
                self._process_drive_entry(
                    entry, config, root_folder_id, parent_local_id or False
                )

            if not data.get("has_more"):
                break
            cursor = data.get("cursor")

        # Recurse into subfolders
        local_subfolders = self.env["one.drive.file"].sudo().search([
            ("root_folder_id","=",root_folder_id),
            ("parent_folder_id","=",parent_local_id or False),
            ("file_type","=","folder"),
            ("one_drive_file_id","!=",False),
        ])
        for subfolder in local_subfolders:
            self._sync_config_files(
                config, root_folder_id=root_folder_id,
                one_drive_parent_id=subfolder.one_drive_file_id,
                parent_local_id=subfolder.id, depth=depth+1, max_depth=max_depth,
            )

    def _process_drive_entry(self, entry, config, root_folder_id, parent_local_id):
        """Upsert a Dropbox entry into one.drive.file."""
        tag = entry.get(".tag", "file")
        raw_id = entry.get("id", "").lstrip("id:")
        if not raw_id:
            return
        name = entry.get("name", "")
        is_folder = (tag == "folder")
        size = entry.get("size", 0)
        modified = entry.get("server_modified") or entry.get("client_modified")
        content_hash = entry.get("content_hash", "")
        path_display = entry.get("path_display", "")
        web_url = f"https://www.dropbox.com/home?select={path_display}"

        existing = self.env["one.drive.file"].sudo().search([
            ("one_drive_file_id","=",raw_id),
            ("drive_config_id","=",config.id),
        ], limit=1)

        import mimetypes
        guessed_mime, _ = mimetypes.guess_type(name)
        
        vals = {
            "name": name,
            "file_type": "folder" if is_folder else "file",
            "mime_type": guessed_mime or "application/octet-stream",
            "drive_config_id": config.id,
            "root_folder_id": root_folder_id,
            "parent_folder_id": parent_local_id,
            "one_drive_file_id": raw_id,
            "one_drive_url": web_url,
            "file_size": size,
            "md5_checksum": content_hash,
            "sync_state": "synced",
            "last_synced": fields.Datetime.now(),
        }
        if modified:
            try:
                import dateutil.parser
                vals["last_modified"] = dateutil.parser.parse(modified).replace(tzinfo=None)
            except Exception:
                pass

        if existing:
            existing.sudo().write(vals)
        else:
            try:
                self.env["one.drive.file"].sudo().create(vals)
            except Exception as e:
                _logger.warning("Could not create file record %s: %s", name, e)

    # ─── Real-time notifications ─────────────────────────────────────────────────

    def _notify_folder_sync(self, folder_id):
        self.env["bus.bus"]._sendone(
            self.env.user, "one.drive.sync",
            {"type":"folder_sync","folder_id": folder_id}
        )


    # ─── Permissions / Sharing ───────────────────────────────────────────────────

    def get_file_permissions(self, file_id, config):
        access_token = self._get_access_token(config)
        if not access_token:
            return []
        r = http_requests.post(
            f"{DROPBOX_API}/sharing/list_file_members",
            headers=self._dbx_headers(access_token),
            json={"file": f"id:{file_id}", "include_inherited": True, "limit": 100},
        )
        
        is_not_shared = False
        if r.status_code == 409:
            try:
                err_tag = r.json().get("error", {}).get(".tag", "")
                if err_tag == "not_shared":
                    is_not_shared = True
            except Exception:
                pass

        if r.status_code != 200 and not is_not_shared:
            _logger.warning("list_file_members failed: %s", r.text[:300])
            return []

        permissions = []

        if is_not_shared:
            ac_r = http_requests.post(
                f"{DROPBOX_API}/users/get_current_account",
                headers=self._dbx_headers(access_token),
            )
            if ac_r.status_code == 200:
                acc_data = ac_r.json()
                permissions.append({
                    "id": acc_data.get("account_id", ""),
                    "type": "user",
                    "emailAddress": acc_data.get("email", ""),
                    "displayName": acc_data.get("name", {}).get("display_name", ""),
                    "role": "owner",
                })
            return permissions

        data = r.json()
        for member in data.get("users", []):
            u = member.get("user", {})
            acc = member.get("access_type", {}).get(".tag", "viewer")
            permissions.append({
                "id": u.get("account_id",""),
                "type": "user",
                "emailAddress": u.get("email",""),
                "displayName": u.get("display_name",""),
                "role": "owner" if acc == "owner" else ("writer" if acc in ("editor", "owner") else "reader"),
            })
        for group in data.get("groups", []):
            g = group.get("group", {})
            acc = group.get("access_type", {}).get(".tag", "viewer")
            permissions.append({
                "id": g.get("group_id",""),
                "type": "group",
                "displayName": g.get("group_name",""),
                "emailAddress": "",
                "role": "writer" if acc == "editor" else "reader",
            })
        for invitee in data.get("invitees", []):
            inv = invitee.get("invitee", {})
            acc = invitee.get("access_type", {}).get(".tag", "viewer")
            email = inv.get("email", "")
            permissions.append({
                "id": f"invitee:{email}",
                "type": "user",
                "emailAddress": email,
                "displayName": email,
                "role": "writer" if acc in ("editor", "owner") else "reader",
            })
        return permissions

    def get_shared_links(self, file_id, config):
        """Fetch all shared links for a file and return wizard-compatible list."""
        access_token = self._get_access_token(config)
        if not access_token:
            return []
        r = http_requests.post(
            f"{DROPBOX_API}/sharing/list_shared_links",
            headers=self._dbx_headers(access_token),
            json={"path": f"id:{file_id}", "direct_only": True},
        )
        if r.status_code != 200:
            _logger.warning("list_shared_links failed: %s", r.text[:300])
            return []
        links = []
        for lnk in r.json().get("links", []):
            tag = lnk.get(".tag", "")
            url = lnk.get("url", "")
            link_perms = lnk.get("link_permissions", {})
            resolved_vis = lnk.get("resolved_visibility", {}).get(".tag", "public")
            can_edit = link_perms.get("can_edit", False)
            scope = "anonymous" if resolved_vis == "public" else "organization"
            team_only = lnk.get("team_member_info") is not None
            if team_only:
                scope = "organization"
            role = "writer" if can_edit else "reader"
            link_id = lnk.get("id", url)  # use URL as fallback ID
            if not link_id:
                link_id = url
            desc_map = {
                "anonymous": "Anyone with the link can view",
                "organization": "Anyone in your team can view",
            }
            if role == "writer":
                desc_map = {
                    "anonymous": "Anyone with the link can edit",
                    "organization": "Anyone in your team can edit",
                }
            links.append({
                "id": link_id,
                "url": url,
                "scope": scope,
                "role": role,
                "description": desc_map.get(scope, "Shared link"),
                "recipients": [],
            })
        return links

    def create_permission(self, file_id, email, role, config, send_notification=True):
        """Add a Dropbox member to a shared file.

        Strategy:
        1. First call sharing/share_file to ensure the file is in Dropbox's sharing system.
        2. Then call sharing/add_file_member with the correct format.
        3. If anything fails, fall back to shared link + Odoo email notification.
        """
        access_token = self._get_access_token(config)
        if not access_token:
            return {'ok': False, 'error': 'Could not obtain Dropbox access token.'}

        # access_level for add_file_member is a plain string, NOT a tagged union
        level = "editor" if role in ("writer", "editor") else "viewer"

        # Step 1: Ensure the file is in Dropbox sharing system
        share_resp = http_requests.post(
            f"{DROPBOX_API}/sharing/share_file",
            headers=self._dbx_headers(access_token),
            json={"path": f"id:{file_id}"},
        )
        _logger.info("share_file -> HTTP %s: %s", share_resp.status_code, share_resp.text[:300])
        # 409 means already shared - that's OK. 200 = just shared. Other codes = problem.
        if share_resp.status_code not in (200, 409):
            _logger.warning("share_file failed (HTTP %s), will still try add_file_member", share_resp.status_code)

        # Step 2: Add the member
        # NOTE: members is a list of MemberSelector objects directly.
        # NOTE: access_level is a tagged union, e.g. {".tag": "viewer"} or {".tag": "editor"}.
        payload = {
            "file": f"id:{file_id}",
            "members": [
                {".tag": "email", "email": email}
            ],
            "access_level": {".tag": level},
            "quiet": not send_notification,
        }
        r = http_requests.post(
            f"{DROPBOX_API}/sharing/add_file_member",
            headers=self._dbx_headers(access_token),
            json=payload,
        )

        _logger.info("add_file_member -> HTTP %s | full body: %s", r.status_code, r.text[:1000])

        if r.status_code == 200:
            try:
                body = r.json()
                results = body if isinstance(body, list) else [body]
            except Exception:
                return {'ok': True}  # got 200 with non-JSON body = treat as success

            errors = []
            for item in results:
                # item is FileMemberActionResult: contains 'member' and 'result'
                res = item.get("result", {})
                if not isinstance(res, dict):
                    continue
                tag = res.get(".tag", "")
                if tag == "success":
                    return {'ok': True}
                elif tag == "member_error":
                    member_err = res.get("member_error", {})
                    err_tag = member_err.get(".tag", "") if isinstance(member_err, dict) else str(member_err)
                    errors.append(self._dbx_sharing_error_message(err_tag))
                elif tag == "access_error":
                    acc_err = res.get("access_error", {})
                    err_tag = acc_err.get(".tag", "") if isinstance(acc_err, dict) else str(acc_err)
                    errors.append(self._dbx_sharing_error_message(err_tag))
                elif tag:
                    errors.append(f"Dropbox result: {tag}")

            if errors:
                return self._fallback_share_via_link(file_id, email, role, config, access_token, reason=errors[0])
            # Empty results list = success (Dropbox sometimes returns [])
            return {'ok': True}

        if r.status_code == 400:
            _logger.warning("add_file_member 400 (bad request): %s", r.text)
            try:
                msg = r.json().get("error_summary", r.text[:300])
            except Exception:
                msg = r.text[:300]
            return self._fallback_share_via_link(file_id, email, role, config, access_token, reason=f"Dropbox API 400: {msg[:120]}")

        if r.status_code == 409:
            try:
                err_data = r.json()
                err_tag = err_data.get("error", {}).get(".tag", "") if isinstance(err_data.get("error"), dict) else ""
                msg = self._dbx_sharing_error_message(err_tag) or f"Dropbox error: {err_data.get('error_summary', 'unknown')}"
            except Exception:
                msg = f"Dropbox error (HTTP 409): {r.text[:200]}"
            _logger.warning("add_file_member 409: %s", msg)
            return self._fallback_share_via_link(file_id, email, role, config, access_token, reason=msg)

        if r.status_code == 403:
            _logger.warning("add_file_member 403 - missing sharing.write scope or insufficient token permissions")
            return self._fallback_share_via_link(
                file_id, email, role, config, access_token,
                reason="The Dropbox app token lacks the 'sharing.write' permission scope."
            )

        _logger.warning("add_file_member HTTP %s: %s", r.status_code, r.text[:500])
        return self._fallback_share_via_link(
            file_id, email, role, config, access_token,
            reason=f"Dropbox HTTP {r.status_code}: {r.text[:200]}"
        )

    def _dbx_sharing_error_message(self, err_tag):
        """Map Dropbox error tags to human-readable messages."""
        messages = {
            "email_unverified":   "The invitee's Dropbox email is not verified.",
            "invalid_email":      "The email address is invalid.",
            "no_account":         "The email is not associated with a Dropbox account.",
            "unverified_dropout": "The invitee's account is not fully verified.",
            "no_permission":      "You don't have permission to share this file.",
            "not_shareable":      "This file cannot be shared directly (team policy or file type).",
            "over_quota":         "The recipient's Dropbox is over quota.",
            "rate_limit":         "Too many sharing requests. Please try again later.",
            "team_policy":        "Team sharing policy prevents adding this member.",
            "is_osp_member":      "Cannot share with this type of account.",
            "outside_team":       "Your team policy prevents sharing outside your team.",
        }
        return messages.get(err_tag, f"Dropbox sharing error: {err_tag}" if err_tag else "")

    def _fallback_share_via_link(self, file_id, email, role, config, access_token, reason=""):
        """Fallback: create/get a shared link and notify via Odoo email.
        Returns dict with ok, fallback, link, warning keys.
        """
        _logger.info("Falling back to shared-link invite for %s (reason: %s)", email, reason)
        link_url = self._get_share_link(file_id, access_token)
        if not link_url:
            return {'ok': False, 'error': reason or "Could not create a Dropbox shared link."}

        # Send notification email via Odoo
        try:
            role_label = "edit" if role in ("writer", "editor") else "view"
            self.env['mail.mail'].sudo().create({
                'subject': "You've been invited to access a shared Dropbox file",
                'body_html': (
                    f"<div style='font-family:sans-serif;max-width:500px;'>"
                    f"<h3 style='color:#0061FE;'>File Shared With You</h3>"
                    f"<p>You have been granted <strong>{role_label}</strong> access to a file.</p>"
                    f"<p><a href='{link_url}' style='background:#0061FE;color:#fff;padding:10px 20px;"
                    f"border-radius:6px;text-decoration:none;display:inline-block;margin:10px 0;'>"
                    f"Open in Dropbox</a></p>"
                    f"<p style='color:#888;font-size:12px;'>Shared via Odoo &amp; Dropbox Integration</p>"
                    f"</div>"
                ),
                'email_to': email,
                'auto_delete': True,
            }).send()
        except Exception as e:
            _logger.warning("Fallback email send failed: %s", e)
            return {
                'ok': True,
                'fallback': True,
                'link': link_url,
                'warning': f"Shared link created but email failed: {str(e)[:100]}. Copy and send this link manually.",
            }

        return {
            'ok': True,
            'fallback': True,
            'link': link_url,
            'warning': (
                f"Direct Dropbox invite not available ({reason[:80] if reason else 'API limitation'}). "
                f"A shareable link was sent to {email} via email."
            ),
        }

    def update_permission(self, file_id, member_id, role, config):
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        level = "editor" if role in ("writer","editor") else "viewer"
        
        if member_id.startswith("invitee:"):
            email = member_id.split(":", 1)[1]
            member_selector = {".tag": "email", "email": email}
        else:
            member_selector = {".tag": "dropbox_id", "dropbox_id": member_id}
            
        r = http_requests.post(
            f"{DROPBOX_API}/sharing/update_file_member",
            headers=self._dbx_headers(access_token),
            json={
                "file": f"id:{file_id}",
                "member": member_selector,
                "access_level": {".tag": level},
            },
        )
        return r.status_code == 200

    def delete_permission(self, file_id, member_id, config):
        access_token = self._get_access_token(config)
        if not access_token:
            return False
            
        if member_id.startswith("invitee:"):
            email = member_id.split(":", 1)[1]
            member_selector = {".tag": "email", "email": email}
        else:
            member_selector = {".tag": "dropbox_id", "dropbox_id": member_id}
            
        r = http_requests.post(
            f"{DROPBOX_API}/sharing/remove_file_member_2",
            headers=self._dbx_headers(access_token),
            json={
                "file": f"id:{file_id}",
                "member": member_selector,
            },
        )
        return r.status_code == 200

    def set_general_access(self, file_id, access_type, role, config, block_download=None, password=None):
        """Set general access: 'anyone' creates a shared link, 'restricted' revokes all links.
        Returns a dict with the resulting link info or True on success.
        """
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        if access_type == "restricted":
            # Revoke all shared links for this file
            r = http_requests.post(
                f"{DROPBOX_API}/sharing/list_shared_links",
                headers=self._dbx_headers(access_token),
                json={"path": f"id:{file_id}", "direct_only": True},
            )
            if r.status_code == 200:
                for link in r.json().get("links", []):
                    url = link.get("url","")
                    if url:
                        http_requests.post(
                            f"{DROPBOX_API}/sharing/revoke_shared_link",
                            headers=self._dbx_headers(access_token),
                            json={"url": url},
                        )
            return True
        else:
            # Build settings for the shared link
            link_settings = {"requested_visibility": {".tag": "public"}}
            if block_download:
                # Note: disabling downloads requires Dropbox Business/Professional
                link_settings["audience"] = {".tag": "public"}
                link_settings["allow_download"] = False
            if password:
                link_settings["link_password"] = password

            r = http_requests.post(
                f"{DROPBOX_API}/sharing/create_shared_link_with_settings",
                headers=self._dbx_headers(access_token),
                json={"path": f"id:{file_id}", "settings": link_settings},
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "success": True,
                    "link": {"webUrl": data.get("url", ""), "url": data.get("url", "")}
                }
            # 409 = link already exists: fetch existing
            if r.status_code == 409:
                r2 = http_requests.post(
                    f"{DROPBOX_API}/sharing/list_shared_links",
                    headers=self._dbx_headers(access_token),
                    json={"path": f"id:{file_id}", "direct_only": True},
                )
                if r2.status_code == 200:
                    links = r2.json().get("links", [])
                    if links:
                        existing_url = links[0].get("url", "")
                        return {
                            "success": True,
                            "link": {"webUrl": existing_url, "url": existing_url}
                        }
            return False

    def get_drive_quota(self, config):
        """Fetch Dropbox storage quota."""
        access_token = self._get_access_token(config)
        if not access_token:
            return {"available": False, "error": "Token refresh failed"}
        r = http_requests.post(
            f"{DROPBOX_API}/users/get_space_usage",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            data="null",
        )
        if r.status_code == 200:
            data = r.json()
            used = data.get("used", 0)
            alloc = data.get("allocation", {})
            tag = alloc.get(".tag","")
            limit = alloc.get("allocated", 0) if tag == "individual" else 0
            has_limit = limit > 0
            usage_pct = round(used / limit * 100, 1) if has_limit else 0
            def fmt(b):
                for u in ["B","KB","MB","GB","TB"]:
                    if b < 1024: return f"{b:.1f} {u}"
                    b /= 1024
                return f"{b:.1f} PB"
            return {
                "available": True,
                "limit_formatted": fmt(limit) if has_limit else "Unlimited",
                "usage_formatted": fmt(used),
                "usage_pct": usage_pct,
                "limit_bytes": limit,
                "usage_bytes": used,
            }
        return {"available": False, "error": f"API Error {r.status_code}"}

    # ─── Helpers used by other models ───────────────────────────────────────────

    def download_file(self, file_id, config):
        """Download file bytes from Dropbox."""
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        import json as _json
        dbx_arg = _json.dumps({"path": f"id:{file_id}"})
        r = http_requests.post(
            f"{DROPBOX_CONTENT}/files/download",
            headers={"Authorization": f"Bearer {access_token}", "Dropbox-API-Arg": dbx_arg},
        )
        if r.status_code == 200:
            return r.content
        return False

    def copy_file(self, file_id, new_name, parent_id, config):
        """Copy a file to a new location in Dropbox."""
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        parent_path = self._resolve_parent_path(parent_id, access_token) if parent_id else ""
        to_path = f"{parent_path}/{new_name}" if parent_path else f"/{new_name}"
        r = http_requests.post(
            f"{DROPBOX_API}/files/copy_v2",
            headers=self._dbx_headers(access_token),
            json={"from_path": f"id:{file_id}", "to_path": to_path, "autorename": True},
        )
        if r.status_code == 200:
            meta = r.json().get("metadata", {})
            raw_id = meta.get("id","").lstrip("id:")
            return {
                "one_drive_file_id": raw_id,
                "name": meta.get("name", new_name),
                "one_drive_url": f"https://www.dropbox.com/home{meta.get('path_display','')}",
            }
        return False
