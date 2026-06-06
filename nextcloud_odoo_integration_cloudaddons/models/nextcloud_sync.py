# -*- coding: utf-8 -*-
import base64, io, json, logging, time
import requests as http_requests
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

NEXTCLOUD_API  = "https://api.nextcloudapi.com/2"
NEXTCLOUD_CONTENT = "https://content.nextcloudapi.com/2"
TOKEN_URL = "https://api.nextcloudapi.com/oauth2/token"

WEBDAV_NS = "DAV:"                        # WebDAV XML namespace
WEBDAV_OC_NS = "http://owncloud.org/ns"   # Nextcloud/OwnCloud extension namespace


class NextcloudSync(models.Model):
    _name = "nextcloud.sync"
    _description = "Nextcloud Synchronization Logic"

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
        """Return Nextcloud access token, or None for Basic Auth configs."""
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
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "refresh_token": config.refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            response = http_requests.post(TOKEN_URL, data=data, timeout=15)
            if response.status_code == 200:
                return response.json().get("access_token")
            _logger.warning("Failed to get Nextcloud access token: %s", response.text)
        except Exception as e:
            _logger.error("Error fetching Nextcloud access token: %s", e)
        return False

    def _dbx_headers(self, access_token):
        return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    def _webdav_auth(self, config):
        """Return (username, password) tuple for Basic Auth WebDAV calls."""
        return (config.client_id or "", config.client_secret or "")

    def _webdav_base(self, config):
        """Return the base WebDAV URL for this config's user."""
        base = (config.nextcloud_url or "").rstrip("/")
        return f"{base}/remote.php/webdav"

    def _safe_webdav_url(self, config, decoded_path):
        """
        Build a fully-encoded WebDAV URL from a *decoded* path string.
        This is the single place where URL-encoding is applied, preventing
        double-encoding when paths are retrieved from the Odoo database.

        decoded_path must be a plain string like:
            /attach/CRM/lead_id_Administrators opportunity/file.png
        (i.e. NO %20 — real spaces are fine).
        """
        import urllib.parse
        base = self._webdav_base(config).rstrip("/")
        # Strip any accidental %xx sequences that may have been stored historically
        # so we always start from a true decoded string before re-encoding.
        try:
            clean = urllib.parse.unquote(decoded_path)
        except Exception:
            clean = decoded_path
        encoded = urllib.parse.quote(clean.lstrip("/"), safe="/")
        return f"{base}/{encoded}"

    def _webdav_list(self, config, remote_path="/"):
        """
        PROPFIND a Nextcloud WebDAV path (Depth: 1).
        Returns a list of dicts: {name, path, is_folder, size, last_modified, etag}
        The first entry is the folder itself — it is skipped in results.

        IMPORTANT: remote_path stored in nextcloud_file_id is always a *decoded*
        string (spaces, not %20).  We encode it once here for the HTTP request.
        """
        import xml.etree.ElementTree as ET
        import urllib.parse
        url  = self._safe_webdav_url(config, remote_path)
        auth = self._webdav_auth(config)
        try:
            r = http_requests.request(
                "PROPFIND", url, auth=auth,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                data="""<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:prop>
    <d:displayname/>
    <d:getcontentlength/>
    <d:getlastmodified/>
    <d:getetag/>
    <d:resourcetype/>
    <oc:fileid/>
  </d:prop>
</d:propfind>""",
                timeout=30,
            )
        except Exception as e:
            _logger.error("WebDAV PROPFIND error at %s: %s", url, e)
            return []
        if r.status_code not in (200, 207):
            _logger.warning("WebDAV PROPFIND %s → HTTP %s", url, r.status_code)
            return []

        entries = []
        try:
            root = ET.fromstring(r.content)
            responses = root.findall("{DAV:}response")
            for idx, resp in enumerate(responses):
                if idx == 0:          # first entry is the folder itself
                    continue
                href = resp.findtext("{DAV:}href") or ""
                propstat = resp.find("{DAV:}propstat")
                if propstat is None:
                    continue
                prop = propstat.find("{DAV:}prop")
                if prop is None:
                    continue
                resourcetype = prop.find("{DAV:}resourcetype")
                is_folder = resourcetype is not None and resourcetype.find("{DAV:}collection") is not None
                # displayname is already a decoded string; use it as the authoritative name
                name     = prop.findtext("{DAV:}displayname") or \
                           urllib.parse.unquote(href.rstrip("/").split("/")[-1])
                size     = int(prop.findtext("{DAV:}getcontentlength") or 0)
                modified = prop.findtext("{DAV:}getlastmodified") or ""
                etag     = (prop.findtext("{DAV:}getetag") or "").strip('"')
                fileid   = prop.findtext("{http://owncloud.org/ns}fileid") or etag or href
                # remote_path must always be a DECODED path (no %xx).  Join with decoded
                # parent path so spaces remain as spaces — encoding happens only at HTTP layer.
                decoded_parent = urllib.parse.unquote(remote_path.rstrip("/"))
                decoded_remote = decoded_parent + "/" + name
                entries.append({
                    "name":          name,
                    "path":          href,           # original encoded href from server
                    "remote_path":   decoded_remote, # decoded path stored in Odoo DB
                    "is_folder":     is_folder,
                    "size":          size,
                    "last_modified": modified,
                    "fileid":        decoded_remote, # use decoded path as stable ID
                    "etag":          etag,
                })
        except Exception as e:
            _logger.error("WebDAV PROPFIND XML parse error: %s", e)
        return entries

    def _webdav_mkdir(self, config, remote_path):
        """
        MKCOL to create a folder on Nextcloud, creating all missing parent
        segments first (recursive mkdir).  Returns True on success.
        remote_path must be a DECODED string — encoding is applied here.
        """
        import urllib.parse
        auth = self._webdav_auth(config)

        # Normalise: strip any stray %xx that might have been stored historically
        try:
            remote_path = urllib.parse.unquote(remote_path)
        except Exception:
            pass

        # Build list of every ancestor path that needs to exist
        segments = [s for s in remote_path.strip("/").split("/") if s]
        for depth in range(1, len(segments) + 1):
            partial = "/".join(segments[:depth])
            url = self._safe_webdav_url(config, "/" + partial)
            try:
                r = http_requests.request("MKCOL", url, auth=auth, timeout=15)
                if r.status_code in (201, 405):   # 201 Created, 405 = already exists
                    continue
                _logger.warning("WebDAV MKCOL %s → HTTP %s: %s", url, r.status_code, r.text[:200])
                if depth == len(segments):
                    # Only fail on the leaf folder
                    return False
            except Exception as e:
                _logger.error("WebDAV MKCOL error at %s: %s", url, e)
                return False
        return True

    # ─── Preview URL ────────────────────────────────────────────────────────────

    @api.model
    def get_preview_url(self, file_id, config_id):
        """Return the internal Odoo preview route which forces inline display."""
        file_record = self.env["nextcloud.file"].sudo().search([("nextcloud_file_id", "=", file_id)], limit=1)
        if file_record:
            return f"/nextcloud/preview/{file_record.id}"
        return False

    # ─── Upload helpers ─────────────────────────────────────────────────────────

    def _resolve_parent_path(self, parent_id, access_token=None):
        """Convert stored parent_id to a Nextcloud path prefix for API calls."""
        if not parent_id or parent_id == "root":
            return ""
        # If it looks like a Nextcloud ID (no leading /), fetch its absolute path
        if not parent_id.startswith("/"):
            if access_token:
                r = http_requests.post(
                    f"{NEXTCLOUD_API}/files/get_metadata",
                    headers=self._dbx_headers(access_token),
                    json={"path": f"id:{parent_id}"},
                )
                if r.status_code == 200:
                    return r.json().get("path_display", "")
            return f"id:{parent_id}"
        return parent_id

    def upload_file(self, attachment):
        configs = self.env["nextcloud.config"].search([("active","=",True),("readonly","=",False)])
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
            "Nextcloud-API-Arg": dbx_arg,
        }
        t0 = time.time()
        try:
            response = http_requests.post(f"{NEXTCLOUD_CONTENT}/files/upload",
                                          headers=headers, data=attachment.raw, timeout=60)
        except Exception as e:
            _logger.error("Network error uploading %s: %s", attachment.name, e)
            return
        elapsed = time.time() - t0
        if response.status_code == 200:
            data = response.json()
            file_id = data.get("id","").lstrip("id:")
            web_url = f"https://www.nextcloud.com/home?select={data.get('path_display','')}"
            if file_id:
                self._register_uploaded_file(attachment, config, file_id, nextcloud_url=web_url)
                self._log(config, attachment.name, "upload", nextcloud_file_id=file_id,
                          file_size=len(attachment.raw) if attachment.raw else 0,
                          root_folder_name=first_root.name if first_root else False, duration=elapsed)
        else:
            self._log(config, attachment.name, "upload", state="fail",
                      error_message=f"HTTP {response.status_code}: {response.text}",
                      root_folder_name=first_root.name if first_root else False, duration=elapsed)

    def _register_uploaded_file(self, attachment, config, file_id, nextcloud_url=False):
        attachment.with_context(skip_nextcloud_sync=True).write({"nextcloud_file_id": file_id})
        self.env["nextcloud.file"].sudo().create({
            "name": attachment.name, "drive_config_id": config.id,
            "file_type": "file", "mime_type": attachment.mimetype,
            "nextcloud_file_id": file_id,
            "nextcloud_url": nextcloud_url or f"https://www.nextcloud.com/home?select={file_id}",
            "owner_name": self.env.user.name,
            "last_modified": fields.Datetime.now(),
            "sync_state": "synced", "last_synced": fields.Datetime.now(),
        })


    # ─── Upload to Drive (called from ir.attachment) ────────────────────────────

    def upload_file_to_drive(self, file_name, file_content, mime_type, config,
                             parent_nextcloud_id=None, conflict_behavior="rename", file_record=None):
        """Upload a file to Nextcloud via WebDAV PUT."""
        import urllib.parse

        # Build the correct Nextcloud path using the Odoo record hierarchy
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
            # Fallback (may be numeric ID if called without file_record)
            if parent_nextcloud_id and not str(parent_nextcloud_id).startswith('/'):
                remote_parent = self._get_remote_path_from_id(parent_nextcloud_id)
            else:
                remote_parent = parent_nextcloud_id or "/"

        # Always work with decoded paths (unquote strips any legacy %20 stored in DB)
        remote_parent = urllib.parse.unquote(str(remote_parent)).rstrip("/")
        if not remote_parent.startswith('/'):
            remote_parent = f'/{remote_parent}'
        remote_path = f"{remote_parent}/{file_name}"
        
        # _safe_webdav_url encodes exactly once (handles any legacy %20 in remote_parent)
        url  = self._safe_webdav_url(config, remote_path)
        auth = self._webdav_auth(config)

        r = http_requests.put(url, auth=auth, data=file_content, timeout=120)

        if r.status_code in (200, 201, 204):
            # Prefer the decoded remote_path as fileid; _webdav_list returns decoded paths too
            fileid = remote_path
            try:
                entries = self._webdav_list(config, remote_path=remote_parent or "/")
                for entry in entries:
                    if entry["name"] == file_name:
                        fileid = entry["fileid"]  # already decoded
                        break
            except Exception:
                pass
            web_url = (config.nextcloud_url or "").rstrip("/") + "/apps/files/?dir=" + urllib.parse.quote(remote_parent)
            return {"nextcloud_file_id": fileid, "nextcloud_url": web_url, "mime_type": mime_type}
            
        raise Exception(f"Nextcloud upload failed (HTTP {r.status_code}): {r.text[:300]}")

    def upload_file_to_drive_resumable(self, file_name, file_content, mime_type,
                                       config, parent_nextcloud_id=None):
        """Fallback for compatibility, routes to the standard WebDAV upload."""
        return self.upload_file_to_drive(file_name, file_content, mime_type, config, parent_nextcloud_id)

    def _get_share_link(self, file_id, access_token=None):
        """Fallback until Nextcloud OCS Sharing API is fully implemented."""
        return ""



    # ─── File Management ────────────────────────────────────────────────────────

    def _get_remote_path_from_id(self, file_id, override_name=None):
        """Resolve a nextcloud_file_id to its absolute WebDAV path on Nextcloud.

        If file_id is already a full WebDAV path (starts with /), use it directly.
        Otherwise walk the Odoo record's parent chain to reconstruct the path.

        Always returns a DECODED string (spaces, not %20). Callers must use
        _safe_webdav_url() before placing this in an HTTP request.
        """
        import urllib.parse
        # Normalise: decode any legacy %20 that old code stored in the DB
        file_id = urllib.parse.unquote(str(file_id)) if file_id else ""

        # Fast path: file_id IS the WebDAV path (set by _process_drive_entry)
        if file_id.startswith("/"):
            if override_name:
                # Replace only the filename component, keep the directory
                parent_dir = file_id.rsplit("/", 1)[0] or "/"
                return (parent_dir.rstrip("/") + "/" + override_name).replace("//", "/")
            return file_id

        # Slow path: reconstruct path from Odoo record hierarchy
        record = self.env["nextcloud.file"].sudo().search([("nextcloud_file_id", "=", file_id)], limit=1)
        if record:
            parts = []
            parent = record.parent_folder_id
            while parent:
                parts.append(parent.name)
                parent = parent.parent_folder_id

            # Prepend root folder path (use root_id if set, else name)
            if record.root_folder_id:
                root_path = record.root_folder_id.root_id or record.root_folder_id.name
                parts.append(root_path.strip("/"))

            segments = list(reversed(parts))
            remote_parent = "/".join(s.strip("/") for s in segments if s)
            remote_parent = "/" + remote_parent if remote_parent else "/"

            file_name = override_name or record.name
            return (remote_parent.rstrip("/") + "/" + file_name).replace("//", "/")

        # Fallback: treat the id itself as a literal path
        return f"/{file_id}"

    def rename_file(self, file_id, new_name, config, current_path=None):
        """
        Rename a file/folder on Nextcloud using WebDAV MOVE.

        Fix for HTTP 409 "destination node is not found":
          The Nextcloud SabreDAV server returns 409 when the *parent directory*
          of the destination does not exist.  This is a server-side cache/lock
          artefact: the folder is really there, but SabreDAV still rejects the
          request if the parent URL is not pre-confirmed.  We guard against this
          by issuing a silent MKCOL on the parent directory first (MKCOL on an
          existing dir returns 405, which we treat as success).  This is safe
          and idempotent.

        Fix for double-encoding:
          All paths stored in nextcloud_file_id are decoded strings (spaces, not
          %20).  We use _safe_webdav_url() which encodes exactly once.
        """
        import posixpath
        import urllib.parse
        auth = self._webdav_auth(config)

        rename_old_name = self.env.context.get("rename_old_name")
        if not current_path:
            current_path = self._get_remote_path_from_id(file_id, override_name=rename_old_name)

        # Ensure current_path is fully decoded (no stray %xx)
        current_path = urllib.parse.unquote(current_path)

        parent_dir = posixpath.dirname(current_path)
        new_path   = (parent_dir.rstrip("/") + "/" + new_name).replace("//", "/")

        src_url = self._safe_webdav_url(config, current_path)
        dst_url = self._safe_webdav_url(config, new_path)

        # Pre-confirm the destination parent directory exists (avoids 409)
        if parent_dir and parent_dir != "/":
            self._webdav_mkdir(config, parent_dir)

        r = http_requests.request(
            "MOVE", src_url, auth=auth,
            headers={"Destination": dst_url, "Overwrite": "F"},
            timeout=60,
        )
        if r.status_code in (201, 204, 207):
            return new_path
        raise Exception(
            f"Nextcloud RENAME failed (HTTP {r.status_code}). "
            f"SRC: {src_url} | DST: {dst_url} | ERR: {r.text[:300]}"
        )

    def trash_file(self, file_id, config):
        return self.delete_file_from_drive(file_id, config)

    def delete_file_from_drive(self, file_id, config):
        import urllib.parse
        auth = self._webdav_auth(config)

        current_path = urllib.parse.unquote(self._get_remote_path_from_id(file_id))
        src_url = self._safe_webdav_url(config, current_path)

        r = http_requests.request("DELETE", src_url, auth=auth, timeout=60)
        return r.status_code in (200, 204)

    def move_file(self, file_id, new_parent_id, config, file_name=None):
        """
        Move a file/folder to a new parent directory on Nextcloud.

        Fix for HTTP 404 "could not be located":
          The stored nextcloud_file_id may contain spaces (decoded path) or
          legacy %20-encoded strings.  We normalise both the source and
          destination to decoded strings and encode them exactly once via
          _safe_webdav_url().

          Additionally we pre-create the destination parent directory so that
          SabreDAV does not reject the request with 409/404 when the folder
          exists on disk but is missing from its internal cache.
        """
        import urllib.parse
        auth = self._webdav_auth(config)

        current_path = urllib.parse.unquote(self._get_remote_path_from_id(file_id))
        file_name = file_name or current_path.rstrip("/").split("/")[-1]

        if new_parent_id and new_parent_id != "root":
            parent_path = urllib.parse.unquote(self._get_remote_path_from_id(new_parent_id))
        else:
            parent_path = ""

        # Strip filename from parent_path if _get_remote_path_from_id returned a file path
        if parent_path and not parent_path.endswith("/") and "." in parent_path.split("/")[-1]:
            parent_path = "/".join(parent_path.split("/")[:-1])

        new_path = (parent_path.rstrip("/") + "/" + file_name).replace("//", "/")

        src_url = self._safe_webdav_url(config, current_path)
        dst_url = self._safe_webdav_url(config, new_path)

        # Pre-create the destination parent directory (idempotent — 405 = already exists)
        dest_parent = "/".join(new_path.rstrip("/").split("/")[:-1])
        if dest_parent and dest_parent != "/":
            self._webdav_mkdir(config, dest_parent)

        r = http_requests.request(
            "MOVE", src_url, auth=auth,
            headers={"Destination": dst_url, "Overwrite": "T"},
            timeout=60,
        )
        if r.status_code in (201, 204, 207):
            return new_path
        raise Exception(
            f"Nextcloud MOVE failed (HTTP {r.status_code}): {r.text[:300]}"
        )

    def create_folder_in_drive(self, folder_name, parent_id_or_config=None, config=None, parent_nextcloud_id=None, file_record=None, parent_path=None):
        """Create a folder on Nextcloud via WebDAV MKCOL.
        Supports flexible arguments for backward compatibility with sync jobs.
        """
        # Resolve flexible arguments
        if parent_id_or_config and isinstance(parent_id_or_config, models.Model) and parent_id_or_config._name == 'nextcloud.config':
            resolved_config = parent_id_or_config
            resolved_parent_id = parent_nextcloud_id
        else:
            resolved_config = config or parent_id_or_config
            resolved_parent_id = parent_id_or_config if not isinstance(parent_id_or_config, models.Model) else parent_nextcloud_id

        # Prefer parent_path if passed directly, otherwise fallback to resolved_parent_id
        remote_parent = parent_path or resolved_parent_id or ""
        remote_path = remote_parent.rstrip("/") + "/" + folder_name

        ok = self._webdav_mkdir(resolved_config, remote_path)
        if ok:
            # Re-list to get the fileid assigned by Nextcloud
            existing = self.find_folder_by_name(folder_name, remote_parent or "/", resolved_config)
            if existing:
                return existing
            # Fallback if listing fails
            return {
                "nextcloud_file_id": remote_path,
                "name":              folder_name,
                "remote_path":       remote_path,
                "nextcloud_url":     (resolved_config.nextcloud_url or "").rstrip("/")
                                     + "/apps/files/?dir=" + remote_path,
            }
        raise Exception(f"Nextcloud folder creation failed for path: {remote_path}")

    def find_folder_by_name(self, folder_name, parent_path, config):
        """List parent path on Nextcloud via WebDAV and find a folder matching folder_name.

        Returns nextcloud_file_id as the clean WebDAV *path* (e.g. '/atttach/CRM')
        rather than the numeric oc:fileid.  This ensures the value can be used
        directly as a parent path in subsequent find_or_create_folder calls
        without a database round-trip to resolve the ID.
        """
        entries = self._webdav_list(config, remote_path=parent_path or "/")
        for entry in entries:
            if entry["is_folder"] and entry["name"].lower() == folder_name.lower():
                remote_path = entry["remote_path"]  # always a clean path like /atttach/CRM
                return {
                    "nextcloud_file_id": remote_path,
                    "name":              entry["name"],
                    "remote_path":       remote_path,
                    "nextcloud_url":     (config.nextcloud_url or "").rstrip("/")
                                         + "/apps/files/?dir=" + remote_path,
                }
        return False

    def find_or_create_folder(self, folder_name, parent_path, config):
        """Find folder by name or create it on Nextcloud. Returns metadata dict."""
        if parent_path and not str(parent_path).startswith('/'):
            parent_path = self._get_remote_path_from_id(parent_path)
        
        parent_path = (parent_path or "/").rstrip("/")
        if not parent_path.startswith('/'):
            parent_path = f'/{parent_path}'
            
        existing = self.find_folder_by_name(folder_name, parent_path, config)
        if existing:
            return existing
        return self.create_folder_in_drive(folder_name, parent_path, config)


    # ─── Backward Sync (Nextcloud → Odoo) ─────────────────────────────────────────

    @api.model
    def fetch_and_sync_files(self):
        """Cron entry point — sync all active drives."""
        configs = self.env["nextcloud.config"].sudo().search([("active","=",True)])
        for config in configs:
            if not config.refresh_token:
                continue
            # 1. PUSH: Odoo to Nextcloud
            try:
                self.env["nextcloud.file"].sudo().with_context(sync_type=self.env.context.get("sync_type", "auto")).sync_pending_to_drive(drive_config_id=config.id)
            except Exception as e:
                _logger.error("Push Sync error for config %s: %s", config.name, e)

            # 2. PULL: Nextcloud to Odoo
            for root in config.root_ids.filtered(lambda r: r.active):
                try:
                    self._sync_config_files(config, root_folder_id=root.id,
                                            nextcloud_parent_id=root.root_id)
                except Exception as e:
                    _logger.error("Sync error for root %s: %s", root.name, e)

    def _sync_config_files(self, config, root_folder_id=None, nextcloud_parent_id=None,
                           parent_local_id=None, depth=0, max_depth=8):
        """
        Recursively list Nextcloud WebDAV folder and upsert records into nextcloud.file.
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

        entries = self._webdav_list(config, remote_path=remote_path)
        if not entries and depth == 0:
            _logger.info("WebDAV list returned no entries for path: %s", remote_path)
            return

        for entry in entries:
            self._process_drive_entry(
                entry, config, root_folder_id, parent_local_id or False
            )

        # Recurse into subfolders found on Nextcloud
        local_subfolders = self.env["nextcloud.file"].sudo().search([
            ("root_folder_id",  "=", root_folder_id),
            ("parent_folder_id","=", parent_local_id or False),
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
        """Upsert a Nextcloud WebDAV entry into nextcloud.file."""
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

        web_url = (config.nextcloud_url or "").rstrip("/") + "/apps/files/?dir=" + remote_path

        existing = self.env["nextcloud.file"].sudo().search([
            ("nextcloud_file_id", "=", fileid),
            ("drive_config_id",   "=", config.id),
        ], limit=1)

        guessed_mime, _ = mimetypes.guess_type(name)

        vals = {
            "name":             name,
            "file_type":        "folder" if is_folder else "file",
            "mime_type":        guessed_mime or "application/octet-stream",
            "drive_config_id": config.id,
            "root_folder_id":  root_folder_id,
            "parent_folder_id":parent_local_id,
            "nextcloud_file_id":fileid,
            "nextcloud_url":    web_url,
            "file_size":        size,
            "md5_checksum":     etag,
            "sync_state":       "synced",
            "last_synced":      fields.Datetime.now(),
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
            existing.sudo().write(vals)
        else:
            try:
                self.env["nextcloud.file"].sudo().create(vals)
            except Exception as e:
                _logger.warning("Could not create file record %s: %s", name, e)

    # ─── Real-time notifications ─────────────────────────────────────────────────

    def _notify_folder_sync(self, folder_id):
        self.env["bus.bus"]._sendone(
            self.env.user, "nextcloud.sync",
            {"type":"folder_sync","folder_id": folder_id}
        )


    # ─── Permissions / Sharing (Nextcloud OCS API) ─────────────────────────────

    def _ocs_base(self, config):
        """Return base URL for Nextcloud OCS Sharing API."""
        return (config.nextcloud_url or '').rstrip('/') + '/ocs/v2.php/apps/files_sharing/api/v1'

    def _ocs_headers(self):
        return {'OCS-APIRequest': 'true', 'Accept': 'application/json'}

    def _ocs_auth(self, config):
        return (config.client_id or '', config.client_secret or '')

    def _ocs_get_shares(self, path, config):
        """GET /shares?path=... — returns list of OCS share dicts for a remote path."""
        r = http_requests.get(
            self._ocs_base(config) + '/shares',
            auth=self._ocs_auth(config),
            headers=self._ocs_headers(),
            params={'path': path, 'reshares': True},
            timeout=15,
        )
        if r.status_code != 200:
            _logger.warning('OCS get_shares failed HTTP %s: %s', r.status_code, r.text[:300])
            return []
        try:
            return r.json().get('ocs', {}).get('data', []) or []
        except Exception:
            return []

    def _ocs_create_share(self, path, share_type, share_with='', permissions=17,
                          password=None, expire_date=None, config=None):
        """POST /shares — create a new OCS share. Returns share dict or None."""
        payload = {
            'path': path,
            'shareType': share_type,
            'permissions': permissions,
        }
        if share_with:
            payload['shareWith'] = share_with
        if password:
            payload['password'] = password
        if expire_date:
            payload['expireDate'] = expire_date
        r = http_requests.post(
            self._ocs_base(config) + '/shares',
            auth=self._ocs_auth(config),
            headers=self._ocs_headers(),
            data=payload,
            timeout=15,
        )
        _logger.info('OCS create_share type=%s path=%s -> HTTP %s', share_type, path, r.status_code)
        if r.status_code in (200, 201, 100):
            try:
                return r.json().get('ocs', {}).get('data', {})
            except Exception:
                return {}
        _logger.warning('OCS create_share failed: %s', r.text[:300])
        return None

    def _ocs_update_share(self, share_id, permissions=None, password=None, expire_date=None, config=None):
        """PUT /shares/{id} — update an existing OCS share."""
        payload = {}
        if permissions is not None:
            payload['permissions'] = permissions
        if password is not None:
            payload['password'] = password
        if expire_date is not None:
            payload['expireDate'] = expire_date
        r = http_requests.put(
            self._ocs_base(config) + f'/shares/{share_id}',
            auth=self._ocs_auth(config),
            headers=self._ocs_headers(),
            data=payload,
            timeout=15,
        )
        return r.status_code in (200, 100)

    def _ocs_delete_share(self, share_id, config):
        """DELETE /shares/{id} — revoke an OCS share."""
        r = http_requests.delete(
            self._ocs_base(config) + f'/shares/{share_id}',
            auth=self._ocs_auth(config),
            headers=self._ocs_headers(),
            timeout=15,
        )
        return r.status_code in (200, 100)

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
        path = self._get_remote_path_from_id(file_id)
        if not str(path).startswith('/'):
            path = f'/{path}'
        shares = self._ocs_get_shares(path, config)
        permissions = []
        # Add owner (current user)
        permissions.append({
            'id': 'owner',
            'type': 'user',
            'emailAddress': config.client_id or '',
            'displayName': config.client_id or 'Owner',
            'role': 'owner',
        })
        for s in shares:
            share_type = s.get('share_type', -1)
            if share_type == 3:  # public link — shown in links section
                continue
            permissions.append({
                'id': str(s.get('id', '')),
                'type': 'group' if share_type == 1 else 'user',
                'emailAddress': s.get('share_with', ''),
                'displayName': s.get('share_with_displayname') or s.get('share_with', ''),
                'role': self._perm_to_role(s.get('permissions', 17)),
            })
        return permissions

    def get_shared_links(self, file_id, config):
        """Return list of public-link shares for wizard display."""
        path = self._get_remote_path_from_id(file_id)
        if not str(path).startswith('/'):
            path = f'/{path}'
        shares = self._ocs_get_shares(path, config)
        links = []
        for s in shares:
            if s.get('share_type') != 3:  # 3 = public link
                continue
            url = s.get('url', '')
            role = self._perm_to_role(s.get('permissions', 17))
            desc_map = {
                'writer': 'Anyone with the link can edit',
                'reader': 'Anyone with the link can view',
            }
            links.append({
                'id': str(s.get('id', '')),
                'url': url,
                'scope': 'anonymous',
                'role': role,
                'description': desc_map.get(role, 'Shared link'),
                'recipients': [],
            })
        return links

    def create_permission(self, file_id, email, role, config, send_notification=True):
        """Share file/folder with a Nextcloud user or email via OCS API."""
        path = self._get_remote_path_from_id(file_id)
        if not str(path).startswith('/'):
            path = f'/{path}'
        permissions = self._role_to_perm(role)
        # Try shareType=0 (user) first, fall back to shareType=4 (email/federated)
        share_data = self._ocs_create_share(
            path, share_type=0, share_with=email,
            permissions=permissions, config=config
        )
        if share_data is None:
            # Fallback: email share (type 4)
            share_data = self._ocs_create_share(
                path, share_type=4, share_with=email,
                permissions=permissions, config=config
            )
        if share_data is None:
            return self._fallback_share_via_link(path, email, role, config)

        share_url = share_data.get('url', '')
        if send_notification:
            try:
                role_label = 'edit' if role in ('writer', 'editor') else 'view'
                self.env['mail.mail'].sudo().create({
                    'subject': "You've been invited to access a shared Nextcloud file",
                    'body_html': (
                        f"<div style='font-family:sans-serif;max-width:500px;'>"
                        f"<h3 style='color:#0082C9;'>File Shared With You</h3>"
                        f"<p>You have been granted <strong>{role_label}</strong> access.</p>"
                        + (f"<p><a href='{share_url}' style='background:#0082C9;color:#fff;"
                           f"padding:10px 20px;border-radius:6px;text-decoration:none;'"
                           f">Open in Nextcloud</a></p>" if share_url else '')
                        + "<p style='color:#888;font-size:12px;'>Shared via Odoo &amp; Nextcloud Integration</p>"
                          "</div>"
                    ),
                    'email_to': email,
                    'auto_delete': True,
                }).send()
            except Exception as e:
                _logger.warning('Share notification email failed: %s', e)
        return {'ok': True}

    def update_permission(self, file_id, member_id, role, config):
        """Update an existing OCS share's permissions."""
        permissions = self._role_to_perm(role)
        return self._ocs_update_share(member_id, permissions=permissions, config=config)

    def delete_permission(self, file_id, member_id, config):
        """Delete an OCS share by share ID."""
        return self._ocs_delete_share(member_id, config)

    def set_general_access(self, file_id, access_type, role, config,
                           block_download=None, password=None):
        """Create or revoke a public link share via OCS API."""
        path = self._get_remote_path_from_id(file_id)
        if not str(path).startswith('/'):
            path = f'/{path}'
        if access_type == 'restricted':
            # Revoke all public link shares for this path
            shares = self._ocs_get_shares(path, config)
            for s in shares:
                if s.get('share_type') == 3:
                    self._ocs_delete_share(str(s['id']), config)
            return True
        else:
            permissions = self._role_to_perm(role)
            share_data = self._ocs_create_share(
                path, share_type=3,
                permissions=permissions,
                password=password or None,
                config=config,
            )
            if not share_data:
                return {'error': 'Could not create public link on Nextcloud.'}
            link_url = share_data.get('url', '')
            return {'success': True, 'link': {'webUrl': link_url, 'url': link_url}}

    def _get_share_link(self, path, config):
        """Get or create a public share link for a Nextcloud path."""
        if not path:
            return ''
        norm_path = path if str(path).startswith('/') else f'/{path}'
        # Check for existing public link
        shares = self._ocs_get_shares(norm_path, config)
        for s in shares:
            if s.get('share_type') == 3 and s.get('url'):
                return s['url']
        # Create a new public link
        share_data = self._ocs_create_share(norm_path, share_type=3, permissions=17, config=config)
        return share_data.get('url', '') if share_data else ''

    def _fallback_share_via_link(self, path, email, role, config, reason=''):
        """Fallback: create a public link and notify via Odoo email."""
        _logger.info('Falling back to shared-link invite for %s (reason: %s)', email, reason)
        link_url = self._get_share_link(path, config)
        if not link_url:
            return {'ok': False, 'error': reason or 'Could not create a Nextcloud shared link.'}
        try:
            role_label = 'edit' if role in ('writer', 'editor') else 'view'
            self.env['mail.mail'].sudo().create({
                'subject': "You've been invited to access a shared Nextcloud file",
                'body_html': (
                    f"<div style='font-family:sans-serif;max-width:500px;'>"
                    f"<h3 style='color:#0082C9;'>File Shared With You</h3>"
                    f"<p>You have been granted <strong>{role_label}</strong> access.</p>"
                    f"<p><a href='{link_url}' style='background:#0082C9;color:#fff;"
                    f"padding:10px 20px;border-radius:6px;text-decoration:none;display:inline-block;"
                    f"margin:10px 0;'>Open in Nextcloud</a></p>"
                    f"<p style='color:#888;font-size:12px;'>Shared via Odoo &amp; Nextcloud Integration</p>"
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
                f'Direct Nextcloud invite not available ({reason[:80] if reason else "API limitation"}). '
                f'A shareable link was sent to {email} via email.'
            ),
        }

    def get_drive_quota(self, config):
        """Fetch Nextcloud storage quota via OCS Provisioning API (DAV quota)."""
        import xml.etree.ElementTree as ET
        base = self._webdav_base(config)
        auth = self._webdav_auth(config)
        r = http_requests.request(
            'PROPFIND', base, auth=auth,
            headers={'Depth': '0', 'Content-Type': 'application/xml'},
            data="""<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:quota-available-bytes/>
    <d:quota-used-bytes/>
  </d:prop>
</d:propfind>""",
            timeout=15,
        )
        if r.status_code not in (200, 207):
            return {'available': False, 'error': f'HTTP {r.status_code}'}
        try:
            root_el = ET.fromstring(r.content)
            prop = root_el.find('.//{DAV:}prop')
            used = int(prop.findtext('{DAV:}quota-used-bytes') or 0)
            avail = int(prop.findtext('{DAV:}quota-available-bytes') or -1)

            def fmt(b):
                for u in ['B', 'KB', 'MB', 'GB', 'TB']:
                    if abs(b) < 1024:
                        return f'{b:.1f} {u}'
                    b /= 1024
                return f'{b:.1f} PB'

            if avail < 0:
                return {
                    'available': True,
                    'limit_formatted': 'Unlimited',
                    'usage_formatted': fmt(used),
                    'usage_pct': 0,
                    'limit_bytes': 0,
                    'usage_bytes': used,
                }
            total = used + avail
            usage_pct = round(used / total * 100, 1) if total > 0 else 0
            return {
                'available': True,
                'limit_formatted': fmt(total),
                'usage_formatted': fmt(used),
                'usage_pct': usage_pct,
                'limit_bytes': total,
                'usage_bytes': used,
            }
        except Exception as e:
            _logger.warning('quota parse error: %s', e)
            return {'available': False, 'error': str(e)}

    def download_file(self, file_id, config):
        """Download file bytes from Nextcloud via WebDAV GET."""
        import urllib.parse
        auth = self._webdav_auth(config)

        current_path = urllib.parse.unquote(self._get_remote_path_from_id(file_id))
        src_url = self._safe_webdav_url(config, current_path)

        r = http_requests.get(src_url, auth=auth, timeout=60)
        if r.status_code == 200:
            return r.content
        _logger.warning("WebDAV GET %s → HTTP %s", src_url, r.status_code)
        return False

    def copy_file(self, file_id, new_name, parent_id, config):
        """Copy a file to a new location in Nextcloud."""
        access_token = self._get_access_token(config)
        if not access_token:
            return False
        parent_path = self._resolve_parent_path(parent_id, access_token) if parent_id else ""
        to_path = f"{parent_path}/{new_name}" if parent_path else f"/{new_name}"
        r = http_requests.post(
            f"{NEXTCLOUD_API}/files/copy_v2",
            headers=self._dbx_headers(access_token),
            json={"from_path": f"id:{file_id}", "to_path": to_path, "autorename": True},
        )
        if r.status_code == 200:
            meta = r.json().get("metadata", {})
            raw_id = meta.get("id","").lstrip("id:")
            return {
                "nextcloud_file_id": raw_id,
                "name": meta.get("name", new_name),
                "nextcloud_url": f"https://www.nextcloud.com/home{meta.get('path_display','')}",
            }
        return False
