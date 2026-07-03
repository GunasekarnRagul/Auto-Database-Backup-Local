# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


def _format_size(size_bytes):
    """Human-readable file-size formatter."""
    if not size_bytes:
        return '0 B'
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f'{size_bytes:.1f} {unit}'
        size_bytes /= 1024.0
    return f'{size_bytes:.1f} PB'


class CloudFile(models.Model):
    _name = 'cloud.file'
    _description = 'Cloud Storage File'
    _order = 'name asc'

    name = fields.Char('Name', required=True, index=True)
    file_type = fields.Selection([
        ('file', 'File'),
        ('folder', 'Folder'),
    ], string='Type', default='file', index=True)

    # Provider linkage
    drive_config_id = fields.Many2one('cloud.provider.config', string='Provider', ondelete='cascade', index=True)
    provider_type   = fields.Selection(related='drive_config_id.provider_type', store=True, index=True)

    # Remote identity (varies by provider)
    cloud_file_id = fields.Char('Remote File / Object ID', index=True)
    cloud_url     = fields.Char('Cloud URL / Presigned URL')

    # Folder hierarchy
    root_folder_id   = fields.Many2one('cloud.root.folder', string='Root Folder', ondelete='cascade', index=True)
    parent_folder_id = fields.Many2one('cloud.file', string='Parent Folder', ondelete='cascade', index=True)

    # File metadata
    mime_type     = fields.Char('MIME Type')
    file_size     = fields.Float('File Size (bytes)', default=0)
    file_size_fmt = fields.Char('Size', compute='_compute_size_fmt')

    # ── Fields used by the JS file explorer ──────────────────────────────────
    owner_name      = fields.Char('Owner', default='Me')
    last_modified   = fields.Datetime('Last Modified')
    last_synced     = fields.Datetime('Last Synced')
    starred         = fields.Boolean('Starred', default=False, index=True)
    upload_progress = fields.Integer('Upload Progress (%)', default=0)

    # Sync state
    sync_state = fields.Selection([
        ('pending',   'Pending'),
        ('uploading', 'Uploading'),
        ('synced',    'Synced'),
        ('error',     'Error'),
    ], string='Sync State', default='pending', index=True)
    sync_error_msg = fields.Text('Sync Error')

    # Sharing
    permission_type = fields.Selection([
        ('restricted', 'Restricted'),
        ('anyone',     'Anyone with link'),
        ('domain',     'Domain'),
    ], string='Access', default='restricted')
    anyone_role          = fields.Char('Anyone Role')
    shared_people_count  = fields.Integer('Shared With (count)', default=0)
    writers_can_share    = fields.Boolean('Writers Can Share', default=False)
    copy_requires_writer = fields.Boolean('Copy Requires Writer', default=False)

    # Odoo model linkage (for attachment sync)
    res_model     = fields.Char('Linked Model')
    res_id        = fields.Integer('Linked Record ID')
    attachment_id = fields.Many2one('ir.attachment', string='Linked Attachment', ondelete='set null', index=True)
    display_path  = fields.Char('Display Path')

    active = fields.Boolean('Active', default=True)

    @api.depends('file_size')
    def _compute_size_fmt(self):
        for rec in self:
            rec.file_size_fmt = _format_size(rec.file_size)

    # ─── Activity Log Helper ──────────────────────────────────────────────────

    def _log_activity(self, operation, state='success', error_message=False,
                      sync_type='manual', file_type=None, folder_path=False,
                      cloud_file_id=False, duration=0, sync_details=False):
        """Log a user-initiated file-explorer operation to cloud.sync.log."""
        for rec in self:
            try:
                ft = file_type or rec.file_type or 'file'
                root = rec.root_folder_id
                self.env['cloud.sync.log'].sudo().log_operation(
                    config=rec.drive_config_id,
                    file_name=rec.name,
                    operation=operation,
                    state=state,
                    error_message=error_message,
                    sync_type=sync_type,
                    file_type=ft,
                    root_folder_name=root.name if root else False,
                    folder_path=folder_path or rec.display_path or False,
                    cloud_file_id=cloud_file_id or rec.cloud_file_id or False,
                    file_size=rec.file_size or 0,
                    duration=duration,
                    sync_details=sync_details,
                )
            except Exception as e:
                _logger.warning("_log_activity failed for '%s': %s", rec.name, e)

    # ─── ORM hooks ────────────────────────────────────────────────────────────
    # NOTE: We do NOT log 'success' on create() because creating a cloud.file
    # record in Odoo does NOT mean it has been synced to the cloud provider.
    # Only action_sync_single_record() logs real cloud success/failure.

    def write(self, vals):
        # Capture current names before the write
        old_names = {rec.id: rec.name for rec in self}
        result = super().write(vals)
        ctx = self.env.context
        if ctx.get('sync_type') in ('cron', 'auto', 'skip'):
            return result
        # Log rename (local rename reflected to cloud by rename_on_drive_by_id)
        if 'name' in vals:
            for rec in self:
                old = old_names.get(rec.id, rec.name)
                if old != vals['name']:
                    rec._log_activity('rename', state='success', sync_type='manual',
                                      sync_details="Renamed '%s' → '%s'" % (old, vals['name']))
        # Log trash (archive)
        if 'active' in vals and not vals['active']:
            for rec in self:
                rec._log_activity('trash', state='success', sync_type='manual',
                                  sync_details="Moved '%s' to Trash" % rec.name)
        return result

    def unlink(self):
        ctx = self.env.context
        if ctx.get('sync_type') not in ('cron', 'auto', 'skip'):
            for rec in self:
                rec._log_activity('delete', state='success', sync_type='manual',
                                  sync_details="Permanently deleted '%s'" % rec.name)
        return super().unlink()

    # ─── RPC endpoint for JS to log cloud-side operations ────────────────────

    @api.model
    def log_operation_from_js(self, file_id, operation, state='success',
                              error_message=False, sync_details=False,
                              drive_config_id=False, file_name=False,
                              file_type='file', folder_path=False,
                              cloud_file_id=False, duration=0):
        """
        Called by the JS file explorer to record operations (upload, download,
        share, move, etc.) that execute entirely on the cloud side.
        """
        try:
            config = False
            if file_id:
                rec = self.sudo().browse(int(file_id))
                if rec.exists():
                    config = rec.drive_config_id
                    file_name    = file_name    or rec.name
                    file_type    = file_type    or rec.file_type
                    cloud_file_id = cloud_file_id or rec.cloud_file_id
                    folder_path  = folder_path  or rec.display_path
            elif drive_config_id:
                config = self.env['cloud.provider.config'].sudo().browse(int(drive_config_id))

            self.env['cloud.sync.log'].sudo().log_operation(
                config=config,
                file_name=file_name or 'Unknown',
                operation=operation,
                state=state,
                error_message=error_message,
                sync_type='manual',
                file_type=file_type,
                folder_path=folder_path,
                cloud_file_id=cloud_file_id,
                duration=duration,
                sync_details=sync_details,
            )
            return True
        except Exception as e:
            _logger.warning("log_operation_from_js failed: %s", e)
            return False

    # ─── Methods called by JS file explorer ──────────────────────────────────

    @api.model
    def get_folder_breadcrumbs(self, folder_id):
        """Return list of {id, name} dicts from root to current folder."""
        crumbs = []
        rec = self.sudo().browse(int(folder_id))
        while rec and rec.exists():
            crumbs.insert(0, {'id': rec.id, 'name': rec.name})
            rec = rec.parent_folder_id
        return crumbs

    @api.model
    def get_trash_roots(self):
        """Return all archived (trashed) root-level items."""
        recs = self.sudo().with_context(active_test=False).search(
            [('active', '=', False), ('parent_folder_id', '=', False)]
        )
        return recs.read(['id', 'name', 'file_type', 'mime_type', 'cloud_url',
                          'file_size', 'owner_name', 'last_modified', 'sync_state',
                          'upload_progress', 'starred', 'drive_config_id',
                          'cloud_file_id', 'attachment_id', 'display_path',
                          'parent_folder_id', 'last_synced'])

    def action_archive_recursive(self):
        """Move file(s) and their children to trash (archive)."""
        all_ids = set(self.ids)
        stack = list(self.ids)
        while stack:
            children = self.sudo().search(
                [('parent_folder_id', 'in', stack), ('active', '=', True)]
            )
            stack = children.ids
            all_ids.update(stack)
        recs = self.sudo().browse(list(all_ids))
        for rec in recs:
            rec._log_activity('trash', state='success', sync_type='manual',
                              sync_details="Moved '%s' to Trash" % rec.name)
        recs.with_context(sync_type='skip').write({'active': False})
        return True

    def action_unarchive(self):
        """Restore file(s) from trash."""
        self.sudo().with_context(active_test=False, sync_type='skip').write({'active': True})
        return True

    def delete_on_drive_and_unlink(self):
        """Permanently delete from cloud provider then remove local record."""
        import requests as http_requests
        for rec in self.sudo():
            config = rec.drive_config_id
            if config and config.state == 'connected' and rec.cloud_file_id:
                try:
                    ptype = config.provider_type
                    engine = self.env['cloud.sync.engine'].sudo()._get_engine(ptype)
                    if ptype == 'gdrive':
                        token = engine._get_access_token(config)
                        if token:
                            http_requests.delete(
                                f'https://www.googleapis.com/drive/v3/files/{rec.cloud_file_id}',
                                headers={'Authorization': f'Bearer {token}'}, timeout=15)
                    elif ptype == 'onedrive':
                        token = engine._get_access_token(config)
                        if token:
                            http_requests.delete(
                                f'https://graph.microsoft.com/v1.0/me/drive/items/{rec.cloud_file_id}',
                                headers={'Authorization': f'Bearer {token}'}, timeout=15)
                    elif ptype == 'dropbox':
                        token = engine._get_access_token(config)
                        if token:
                            http_requests.post('https://api.dropboxapi.com/2/files/delete_v2',
                                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
                                json={'path': f'id:{rec.cloud_file_id}'}, timeout=15)
                    rec._log_activity('delete', state='success', sync_type='manual',
                                      sync_details="Deleted '%s' from cloud" % rec.name)
                except Exception as e:
                    _logger.error("delete_on_drive_and_unlink error for '%s': %s", rec.name, e)
                    rec._log_activity('delete', state='fail', error_message=str(e),
                                      sync_type='manual',
                                      sync_details="Failed to delete '%s' from cloud" % rec.name)
        self.sudo().with_context(sync_type='skip').unlink()
        return True

    @api.model
    def rename_on_drive_by_id(self, file_id, new_name, provider_type=None):
        """Rename a file on the cloud provider (Nextcloud WebDAV)."""
        import requests as http_requests
        rec = self.sudo().browse(int(file_id))
        if not rec.exists():
            return False
        config = rec.drive_config_id
        if not config or config.state != 'connected':
            return False
        try:
            ptype = provider_type or config.provider_type
            if ptype == 'nextcloud':
                engine = self.env['cloud.sync.nextcloud'].sudo()
                old_url = engine._dav_url(config, rec.cloud_file_id)
                # Build new URL with new name
                parts = rec.cloud_file_id.rsplit('/', 1)
                new_path = (parts[0] + '/' + new_name) if len(parts) > 1 else new_name
                new_url = engine._dav_url(config, new_path)
                resp = http_requests.request('MOVE', old_url,
                    headers={'Destination': new_url, 'Overwrite': 'F'},
                    auth=engine._auth(config), timeout=15)
                if resp.status_code in (201, 204):
                    rec.with_context(sync_type='skip').write({'cloud_file_id': new_path})
                    rec._log_activity('rename', state='success', sync_type='manual',
                                      sync_details="Renamed to '%s' on Nextcloud" % new_name)
                    return True
            # For other providers, rename happens via local write already captured
            return True
        except Exception as e:
            _logger.error("rename_on_drive_by_id failed: %s", e)
            return False

    @api.model
    def action_upload_from_explorer(self, drive_config_id, root_folder_id,
                                     parent_folder_id=False, file_name=False,
                                     file_data_b64=False, mime_type=False):
        """Upload a file (base64 encoded) directly from the file explorer UI."""
        import base64, time
        config = self.env['cloud.provider.config'].sudo().browse(int(drive_config_id))
        if not config.exists() or config.state != 'connected':
            return {'error': 'Provider not connected'}
        t0 = time.time()
        try:
            file_bytes = base64.b64decode(file_data_b64) if file_data_b64 else b''
            engine = self.env['cloud.sync.engine'].sudo()._get_engine(config.provider_type)

            parent_remote_id = None
            if parent_folder_id:
                parent_rec = self.sudo().browse(int(parent_folder_id))
                if parent_rec.exists():
                    parent_remote_id = parent_rec.cloud_file_id
            if not parent_remote_id and root_folder_id:
                root = self.env['cloud.root.folder'].sudo().browse(int(root_folder_id))
                if root.exists():
                    parent_remote_id = root.root_id

            # Delegate to engine upload
            import tempfile, os
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name or 'file')[1]) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name

            # Create ir.attachment and use upload_attachment
            att = self.env['ir.attachment'].sudo().create({
                'name': file_name or 'upload',
                'datas': file_data_b64,
                'mimetype': mime_type or 'application/octet-stream',
            })
            os.unlink(tmp_path)

            duration = round(time.time() - t0, 3)
            cf = self.sudo().create({
                'name': file_name or 'upload',
                'file_type': 'file',
                'drive_config_id': config.id,
                'root_folder_id': root_folder_id or False,
                'parent_folder_id': parent_folder_id or False,
                'sync_state': 'pending',
                'mime_type': mime_type or 'application/octet-stream',
                'file_size': len(file_bytes),
                'attachment_id': att.id,
            })
            self.env['cloud.sync.log'].sudo().log_operation(
                config=config, file_name=file_name, operation='upload',
                state='success', sync_type='manual', file_type='file',
                file_size=len(file_bytes), duration=duration,
                sync_details="Queued '%s' for upload" % file_name,
            )
            return {'success': True, 'id': cf.id}
        except Exception as e:
            _logger.error("action_upload_from_explorer failed: %s", e)
            duration = round(time.time() - t0, 3)
            self.env['cloud.sync.log'].sudo().log_operation(
                config=config, file_name=file_name, operation='upload',
                state='fail', error_message=str(e), sync_type='manual',
                file_type='file', duration=duration,
                sync_details="Upload failed: %s" % str(e)[:200],
            )
            return {'error': str(e)}

    def get_recursive_files_for_zip(self, file_ids):
        """Return list of (cloud.file record, relative_path) for ZIP download."""
        result = []
        records = self.browse(file_ids)
        for rec in records:
            if rec.file_type == 'folder':
                children = self.search([('parent_folder_id', '=', rec.id), ('file_type', '=', 'file')])
                for child in children:
                    result.append((child, f'{rec.name}/{child.name}'))
            else:
                result.append((rec, rec.name))
        return result

    def get_duplicate_groups(self):
        """Find files with identical names and sizes."""
        self.env.cr.execute("""
            SELECT name, file_size, array_agg(id) AS ids
            FROM cloud_file
            WHERE file_type = 'file' AND active = TRUE AND file_size > 0
            GROUP BY name, file_size
            HAVING COUNT(*) > 1
        """)
        groups = []
        for row in self.env.cr.dictfetchall():
            ids = row['ids']
            groups.append({
                'name': row['name'],
                'file_size': row['file_size'],
                'duplicate_ids': ids[1:],  # keep first, flag rest
            })
        return groups

    @api.model
    def sync_pending_to_drive(self, drive_config_id=None):
        """Push all pending cloud.file records (folders AND files) to the cloud provider."""
        import time as _time
        domain = [('sync_state', 'in', ['pending', 'error']), ('active', '=', True)]
        if drive_config_id:
            domain.append(('drive_config_id', '=', drive_config_id))
        pending = self.search(domain)
        # Process folders first so parent folders exist remotely before child files
        folders = pending.filtered(lambda r: r.file_type == 'folder')
        files   = pending.filtered(lambda r: r.file_type != 'folder')
        ordered = folders + files

        success = 0
        error = 0
        sync_type = self.env.context.get('sync_type', 'manual')

        for rec in ordered:
            config = rec.drive_config_id
            if not config or config.state != 'connected':
                continue
            t0 = _time.time()
            try:
                engine = self.env['cloud.sync.engine'].sudo()._get_engine(config.provider_type)

                if rec.file_type == 'folder':
                    # Resolve remote parent ID
                    parent_remote_id = '/'
                    if rec.parent_folder_id and rec.parent_folder_id.cloud_file_id:
                        parent_remote_id = rec.parent_folder_id.cloud_file_id
                    elif rec.root_folder_id and rec.root_folder_id.root_id:
                        parent_remote_id = rec.root_folder_id.root_id

                    result = engine.find_or_create_folder(rec.name, parent_remote_id, config)
                    if result and result.get('remote_id'):
                        rec.sudo().with_context(sync_type='skip').write({
                            'sync_state': 'synced',
                            'cloud_file_id': result['remote_id'],
                            'cloud_url': result.get('cloud_url', ''),
                            'last_synced': fields.Datetime.now(),
                            'sync_error_msg': False,
                        })
                        self.env['cloud.sync.log'].sudo().log_operation(
                            config=config,
                            file_name=rec.name,
                            operation='create_folder',
                            state='success',
                            sync_type=sync_type,
                            file_type='folder',
                            root_folder_name=rec.root_folder_id.name if rec.root_folder_id else False,
                            cloud_file_id=result['remote_id'],
                            duration=round(_time.time() - t0, 3),
                            sync_details="Folder '%s' synced to %s (ID: %s)" % (
                                rec.name, config.name, result['remote_id']),
                        )
                        success += 1
                    else:
                        raise Exception("find_or_create_folder returned no remote_id")
                else:
                    # File: delegate to engine upload_file
                    engine.upload_file(rec, config)
                    if rec.sync_state != 'synced':
                        rec.sudo().with_context(sync_type='skip').write({
                            'sync_state': 'synced',
                            'last_synced': fields.Datetime.now(),
                            'sync_error_msg': False,
                        })
                    success += 1

            except Exception as e:
                _logger.error("sync_pending_to_drive failed for '%s' [%s]: %s",
                              rec.name, config.name if config else '?', e)
                rec.sudo().with_context(sync_type='skip').write({
                    'sync_state': 'error',
                    'sync_error_msg': str(e),
                })
                self.env['cloud.sync.log'].sudo().log_operation(
                    config=config,
                    file_name=rec.name,
                    operation='create_folder' if rec.file_type == 'folder' else 'upload',
                    state='fail',
                    error_message=str(e),
                    sync_type=sync_type,
                    file_type=rec.file_type or 'file',
                    root_folder_name=rec.root_folder_id.name if rec.root_folder_id else False,
                    duration=round(_time.time() - t0, 3),
                    sync_details="FAILED: '%s' — %s" % (rec.name, str(e)[:200]),
                )
                error += 1
        return {'success': success, 'error': error}

    @api.model
    def get_pending_sync_ids(self, drive_config_id=None):
        """
        Return IDs of all pending cloud.file records for a given drive.
        Called by the JS triggerAutoSync loop.
        """
        domain = [('sync_state', 'in', ['pending', 'error']), ('active', '=', True)]
        if drive_config_id:
            domain.append(('drive_config_id', '=', drive_config_id))
        return self.sudo().search(domain).ids

    def action_sync_single_record(self):
        """
        Sync a single cloud.file record to its cloud provider.
        Called per-record by the JS triggerAutoSync loop.

        - For folders  → calls engine.find_or_create_folder()
        - For files    → calls engine.upload_file()

        Updates sync_state and logs the operation to cloud.sync.log.
        """
        import time
        for rec in self.sudo():
            config = rec.drive_config_id
            if not config or config.state != 'connected':
                _logger.warning("action_sync_single_record: skipping '%s' — provider not connected", rec.name)
                continue

            t0 = time.time()
            try:
                engine = self.env['cloud.sync.engine'].sudo()._get_engine(config.provider_type)

                if rec.file_type == 'folder':
                    # Determine remote parent ID
                    parent_remote_id = '/'
                    if rec.parent_folder_id and rec.parent_folder_id.cloud_file_id:
                        parent_remote_id = rec.parent_folder_id.cloud_file_id
                    elif rec.root_folder_id and rec.root_folder_id.root_id:
                        parent_remote_id = rec.root_folder_id.root_id

                    result = engine.find_or_create_folder(rec.name, parent_remote_id, config)
                    if result and result.get('remote_id'):
                        rec.with_context(sync_type='skip').write({
                            'sync_state': 'synced',
                            'cloud_file_id': result['remote_id'],
                            'cloud_url': result.get('cloud_url', ''),
                            'last_synced': fields.Datetime.now(),
                            'sync_error_msg': False,
                        })
                        duration = round(time.time() - t0, 3)
                        self.env['cloud.sync.log'].sudo().log_operation(
                            config=config,
                            file_name=rec.name,
                            operation='create_folder',
                            state='success',
                            sync_type=self.env.context.get('sync_type', 'manual'),
                            file_type='folder',
                            root_folder_name=rec.root_folder_id.name if rec.root_folder_id else False,
                            cloud_file_id=result['remote_id'],
                            duration=duration,
                            sync_details="Folder '%s' created on %s" % (rec.name, config.name),
                        )
                    else:
                        raise Exception("find_or_create_folder returned no remote_id")

                else:
                    # File: use the existing upload_file dispatcher
                    engine.upload_file(rec, config)
                    # upload_file sets sync_state=synced internally; log if it didn't fail
                    rec.with_context(sync_type='skip').write({'sync_error_msg': False})

            except Exception as e:
                duration = round(time.time() - t0, 3)
                _logger.error("action_sync_single_record failed for '%s' [%s]: %s",
                              rec.name, config.name, e)
                rec.with_context(sync_type='skip').write({
                    'sync_state': 'error',
                    'sync_error_msg': str(e),
                })
                self.env['cloud.sync.log'].sudo().log_operation(
                    config=config,
                    file_name=rec.name,
                    operation='create_folder' if rec.file_type == 'folder' else 'upload',
                    state='fail',
                    error_message=str(e),
                    sync_type=self.env.context.get('sync_type', 'manual'),
                    file_type=rec.file_type or 'file',
                    root_folder_name=rec.root_folder_id.name if rec.root_folder_id else False,
                    duration=duration,
                    sync_details="Failed to sync '%s'" % rec.name,
                )
        return True
