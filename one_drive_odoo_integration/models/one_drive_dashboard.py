# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from odoo import models, fields, api

import json
import logging
import requests as http_requests
_logger = logging.getLogger(__name__)


def _format_size(size_bytes):
    """Human-readable file-size formatter."""
    if not size_bytes:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


class GoogleDriveDashboard(models.AbstractModel):
    _name = 'one.drive.dashboard'
    _description = 'OneDrive Sync Dashboard Data'

    # ──────────────────────────────────────────────
    # 1. Fast KPIs & Health (loads first)
    # ──────────────────────────────────────────────

    @api.model
    def get_kpi_data(self):
        """Fetch primary KPIs, health status, and detailed sub-metrics."""
        try:
            GDFile = self.env['one.drive.file'].sudo()
            SyncConfig = self.env['attachment.sync.config'].sudo()
            SyncLog = self.env['one.drive.sync.log'].sudo()
            now = fields.Datetime.now()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

            # ── Files Synced (detailed) ──
            total_files_synced = GDFile.search_count([
                ('sync_state', '=', 'synced'),
                ('file_type', '=', 'file'),
                ('active', '=', True),
            ])
            total_folders_synced = GDFile.search_count([
                ('sync_state', '=', 'synced'),
                ('file_type', '=', 'folder'),
                ('active', '=', True),
            ])
            synced_today = SyncLog.search_count([
                ('state', '=', 'success'),
                ('operation', '=', 'upload'),
                ('create_date', '>=', today_start),
            ])

            # ── Configuration Overview ──
            SyncConfig = self.env['attachment.sync.config'].sudo()
            active_configs = SyncConfig.search([('state', '=', 'active')])
            
            # ── Pending (breakdown by state) ──
            # Improved: Combine both types of pending items:
            # 1. Manual uploads/folders from File Explorer ('pending' state)
            # 2. Unsynced business attachments from active rules (auto-sync queue)
            explorer_pending = GDFile.search_count([
                ('sync_state', '=', 'pending'), ('active', '=', True),
            ])
            attachment_pending = sum(active_configs.mapped('unsynced_attachment_count'))
            pending_count = explorer_pending + attachment_pending
            
            error_count = GDFile.search_count([
                ('sync_state', '=', 'error'), ('active', '=', True),
            ])
            uploading_count = GDFile.search_count([
                ('sync_state', '=', 'uploading'), ('active', '=', True),
            ])
            total_pending = pending_count + error_count + uploading_count

            # ── System Health (detailed) ──
            configs = self.env['one.drive.config'].sudo().search([('active', '=', True)])
            primary_config = configs[:1] if configs else self.env['one.drive.config']
            connection_status = 'online' if primary_config and primary_config.refresh_token else 'offline'

            drive_name = primary_config.name if primary_config else 'Not Connected'
            total_drives = len(configs)
            online_drives = len(configs.filtered(lambda c: c.refresh_token))
            total_roots = sum(len(c.root_ids.filtered(lambda r: r.active)) for c in configs)

            # ── Configuration Overview ──
            paused_configs = SyncConfig.search_count([('state', '=', 'paused')])
            total_configs = len(active_configs) + paused_configs

            modes = {}
            model_details = []
            for cfg in active_configs:
                mode = dict(cfg._fields['storage_mode'].selection).get(
                    cfg.storage_mode, cfg.storage_mode
                )
                modes[cfg.storage_mode] = modes.get(cfg.storage_mode, 0) + 1
                model_details.append({
                    'id': cfg.id,
                    'name': cfg.name,
                    'model_name': cfg.model_name or '',
                    'storage_mode': mode,
                    'state': cfg.state,
                })

            active_mode_display = " / ".join(
                f"{v.title()} ({c})" for v, c in modes.items()
            ) if modes else "Not Configured"

            # ── Fleet Details (with per-drive Odoo stats) ──
            fleet = []
            self.env.cr.execute("""
                SELECT 
                    drive_config_id, 
                    COUNT(*) as file_count, 
                    COALESCE(SUM(file_size), 0) as total_size
                FROM one_drive_file
                WHERE file_type = 'file' AND sync_state = 'synced' AND active = TRUE
                GROUP BY drive_config_id
            """)
            stats_by_drive = {row['drive_config_id']: row for row in self.env.cr.dictfetchall()}

            for c in configs:
                stats = stats_by_drive.get(c.id, {'file_count': 0, 'total_size': 0})
                odoo_count = stats['file_count']
                odoo_size = stats['total_size']

                fleet.append({
                    'id': c.id,
                    'name': c.name,
                    'state': c.state,
                    'is_connected': bool(c.refresh_token),
                    'folders_count': len(c.root_ids),
                    'odoo_file_count': odoo_count,
                    'odoo_storage_size': odoo_size,
                    'odoo_storage_formatted': _format_size(odoo_size),
                })

            # ── Today's Failures ──
            fails_today = SyncLog.search_count([
                ('state', '=', 'fail'),
                ('create_date', '>=', today_start),
            ])

            # ── Files Added This Month ──
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            files_this_month = GDFile.search_count([
                ('file_type', '=', 'file'),
                ('active', '=', True),
                ('create_date', '>=', month_start),
            ])

            # ── Active Share Links ──
            active_share_links = GDFile.search_count([
                '|', ('permission_type', '!=', 'restricted'), ('shared_people_count', '>', 0),
                ('active', '=', True),
            ])

            return {
                'kpis': {
                    'total_files_synced': total_files_synced,
                    'total_folders_synced': total_folders_synced,
                    'synced_today': synced_today,
                    'pending_syncs': total_pending,
                    'pending_count': pending_count,
                    'error_count': error_count,
                    'uploading_count': uploading_count,
                    'fails_today': fails_today,
                    'files_this_month': files_this_month,
                    'active_share_links': active_share_links,
                },
                'health': {
                    'connection_status': connection_status,
                    'drive_name': drive_name,
                    'total_drives': total_drives,
                    'online_drives': online_drives,
                    'total_roots': total_roots,
                    'active_mode': active_mode_display,
                    'total_configs': total_configs,
                    'active_configs': len(active_configs),
                    'paused_configs': paused_configs,
                    'model_details': model_details,
                    'fleet': fleet,
                },
            }

        except Exception as e:
            _logger.exception("Error in get_kpi_data: %s", e)
            return {'kpis': {}, 'health': {'fleet': []}}

    @api.model
    def get_storage_stats(self):
        """Fetch and aggregate heavy storage metrics."""
        try:
            # 1. Total space used by synced files
            self.env.cr.execute("""
                SELECT COALESCE(SUM(file_size), 0) AS total_size
                FROM one_drive_file
                WHERE sync_state = 'synced' AND file_type = 'file' AND active = TRUE
            """)
            space_used_bytes = self.env.cr.fetchone()[0]

            # 2. Storage saved (files uploaded under configurations with storage_mode = 'drive')
            self.env.cr.execute("""
                SELECT COALESCE(SUM(f.file_size), 0) AS saved_size
                FROM one_drive_file f
                JOIN attachment_sync_config c ON f.drive_config_id = c.one_drive_id
                WHERE f.sync_state = 'synced' AND f.file_type = 'file' AND f.active = TRUE
                  AND c.storage_mode = 'drive'
            """)
            storage_saved_bytes = self.env.cr.fetchone()[0]

            return {
                'storage_saved_formatted': _format_size(storage_saved_bytes),
                'drive_space_used_formatted': _format_size(space_used_bytes),
            }

        except Exception as e:
            _logger.exception("Error in get_storage_stats: %s", e)
            return {'storage_saved_formatted': '0 B', 'drive_space_used_formatted': '0 B'}

    @api.model
    def get_activity_logs(self, period='today', operation=False, limit=1000):
        """Fetch sync logs with optional date-range and operation filters."""
        try:
            SyncLog = self.env['one.drive.sync.log'].sudo()
            domain = []
            now = fields.Datetime.now()

            if period == 'today':
                domain.append(('create_date', '>=', now.replace(hour=0, minute=0, second=0)))
            elif period == '7d':
                domain.append(('create_date', '>=', now - timedelta(days=7)))
            elif period == '30d':
                domain.append(('create_date', '>=', now - timedelta(days=30)))

            if operation:
                domain.append(('operation', '=', operation))

            recent_logs = []
            logs = SyncLog.search(domain, order='create_date desc', limit=limit)
            for log in logs:
                recent_logs.append({
                    'id': log.id,
                    'file_name': log.file_name,
                    'operation': log.operation,
                    'state': log.state,
                    'date': fields.Datetime.context_timestamp(self, log.create_date).strftime("%Y-%m-%d %H:%M:%S") if log.create_date else '',
                    'error_message': log.error_message or '',
                })
            return recent_logs

        except Exception as e:
            _logger.exception("Error in get_activity_logs: %s", e)
            return []

    @api.model
    def get_duplicate_summary(self):
        """Heavy scan for duplicated files."""
        try:
            GDFile = self.env['one.drive.file'].sudo()
            duplicate_count = 0
            if hasattr(GDFile, 'get_duplicate_groups'):
                duplicate_groups = GDFile.get_duplicate_groups()
                duplicate_count = sum(len(group['duplicate_ids']) for group in duplicate_groups)

            return {
                'duplicate_count': duplicate_count,
            }

        except Exception as e:
            _logger.exception("Error in get_duplicate_summary: %s", e)
            return {'duplicate_count': 0}

    @api.model
    def get_sync_trend_data(self, days=7, start_date=False, end_date=False):
        """Aggregate sync-log counts per day for a date range."""
        try:
            today_dt = fields.Date.today()
            if start_date and end_date:
                s_dt = fields.Date.from_string(start_date)
                e_dt = fields.Date.from_string(end_date)
            else:
                e_dt = today_dt
                s_dt = today_dt - timedelta(days=days - 1)

            if e_dt < s_dt:
                e_dt = s_dt

            cutoff = datetime.combine(s_dt, datetime.min.time())
            end_limit = datetime.combine(e_dt, datetime.max.time())

            self.env.cr.execute("""
                SELECT
                    TO_CHAR(create_date AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS sync_date,
                    state,
                    COUNT(*)                                              AS cnt
                FROM one_drive_sync_log
                WHERE create_date >= %s AND create_date <= %s
                GROUP BY sync_date, state
                ORDER BY sync_date
            """, [cutoff, end_limit])

            rows = self.env.cr.dictfetchall()

            labels = []
            success_map = {}
            failed_map = {}
            
            delta = (e_dt - s_dt).days
            for i in range(delta + 1):
                d = (s_dt + timedelta(days=i)).isoformat()
                labels.append(d)
                success_map[d] = 0
                failed_map[d] = 0

            for row in rows:
                d = row['sync_date']
                if d in success_map:
                    if row['state'] == 'success':
                        success_map[d] = row['cnt']
                    else:
                        failed_map[d] = row['cnt']

            return {
                'labels': labels,
                'success': [success_map[d] for d in labels],
                'failed': [failed_map[d] for d in labels],
            }

        except Exception as e:
            _logger.exception("Error in get_sync_trend_data: %s", e)
            return {'labels': [], 'success': [], 'failed': []}

    @api.model
    def get_model_breakdown(self):
        """Return per-model sync statistics from active configs."""
        try:
            configs = self.env['attachment.sync.config'].sudo().search([
                ('state', '=', 'active'),
            ])
            result = []
            for cfg in configs:
                result.append({
                    'id': cfg.id,
                    'name': cfg.name,
                    'model_name': cfg.model_name or '',
                    'storage_mode': dict(cfg._fields['storage_mode'].selection).get(
                        cfg.storage_mode, cfg.storage_mode
                    ),
                    'synced': cfg.synced_attachment_count,
                    'unsynced': cfg.unsynced_attachment_count,
                    'dual': cfg.dual_attachment_count,
                    'drive_only': cfg.drive_only_attachment_count,
                    'sync_pct': round(cfg.sync_percentage, 1),
                    'total': cfg.model_attachment_count,
                })
            return result

        except Exception as e:
            _logger.exception("Error in get_model_breakdown: %s", e)
            return []

    @api.model
    def get_error_summary(self):
        """Aggregate failure stats from sync logs."""
        try:
            SyncLog = self.env['one.drive.sync.log'].sudo()
            now = fields.Datetime.now()

            fails_24h = SyncLog.search_count([
                ('state', '=', 'fail'),
                ('create_date', '>=', now - timedelta(hours=24)),
            ])
            fails_7d = SyncLog.search_count([
                ('state', '=', 'fail'),
                ('create_date', '>=', now - timedelta(days=7)),
            ])

            self.env.cr.execute("""
                SELECT COALESCE(LEFT(error_message, 120), 'Unknown error') AS error_message,
                       COUNT(*) AS cnt
                FROM   one_drive_sync_log
                WHERE  state = 'fail'
                  AND  error_message IS NOT NULL
                  AND  create_date >= %s
                GROUP  BY 1
                ORDER  BY cnt DESC
                LIMIT  3
            """, [now - timedelta(days=7)])
            top_errors = self.env.cr.dictfetchall()

            return {
                'fails_24h': fails_24h,
                'fails_7d': fails_7d,
                'top_errors': top_errors,
            }

        except Exception as e:
            _logger.exception("Error in get_error_summary: %s", e)
            return {'fails_24h': 0, 'fails_7d': 0, 'top_errors': []}

    @api.model
    def get_drive_quota(self, config_id=False):
        """Fetch OneDrive storage quota via the about.get API."""
        try:
            if config_id:
                cid = int(config_id)
                config = self.env['one.drive.config'].sudo().browse(cid)
            else:
                config = self.env['one.drive.config'].sudo().search([], limit=1)

            if not config or not config.exists():
                return {'available': False, 'error': 'Config not found'}

            if not config.refresh_token:
                return {'available': False, 'error': 'No refresh token'}

            sync = self.env['one.drive.sync'].sudo()
            access_token = sync._get_access_token(config)
            if not access_token:
                return {'available': False, 'error': 'Token refresh failed'}

            resp = http_requests.get(
                'https://graph.microsoft.com/v1.0/me/drive',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=15,
            )
            
            if resp.status_code == 200:
                quota = resp.json().get('quota', {})
                limit_bytes = int(quota.get('total', 0))
                usage_bytes = int(quota.get('used', 0))
                
                has_limit = limit_bytes > 0
                usage_pct = round(usage_bytes / limit_bytes * 100, 1) if has_limit else 0
                
                return {
                    'available': True,
                    'limit_formatted': _format_size(limit_bytes) if has_limit else "Unlimited",
                    'usage_formatted': _format_size(usage_bytes),
                    'usage_pct': usage_pct,
                    'limit_bytes': limit_bytes,
                    'usage_bytes': usage_bytes,
                }
            else:
                return {'available': False, 'error': f'API Error {resp.status_code}'}
        except Exception as e:
            _logger.exception("Failed to fetch Drive quota for config %s: %s", config_id, str(e))
            return {'available': False, 'error': str(e)}

    @api.model
    def get_top_uploaders(self, limit=10):
        """Return top users by number of sync log uploads."""
        try:
            self.env.cr.execute("""
                SELECT l.user_id AS user_id,
                       u.name       AS user_name,
                       COUNT(*)     AS upload_count,
                       COALESCE(SUM(l.file_size), 0) AS total_bytes
                FROM   one_drive_sync_log l
                LEFT   JOIN res_users ru ON ru.id = l.user_id
                LEFT   JOIN res_partner u ON u.id = ru.partner_id
                WHERE  l.operation = 'upload'
                  AND  l.state     = 'success'
                GROUP  BY l.user_id, u.name
                ORDER  BY upload_count DESC
                LIMIT  %s
            """, [limit])
            rows = self.env.cr.dictfetchall()
            for row in rows:
                row['total_formatted'] = _format_size(row['total_bytes'] or 0)
            return rows
        except Exception as e:
            _logger.exception("Error in get_top_uploaders: %s", e)
            return []

    @api.model
    def get_top_file_types(self, limit=8, config_id=False):
        """Return file-count distribution by MIME type."""
        try:
            params = []
            where_clause = ""
            if config_id:
                where_clause = " AND drive_config_id = %s "
                params.append(int(config_id))
            
            params.append(limit)

            self.env.cr.execute(f"""
                SELECT
                    COALESCE(
                        CASE
                            WHEN mime_type LIKE 'image/%%'                    THEN 'Images'
                            WHEN mime_type LIKE 'video/%%'                    THEN 'Videos'
                            WHEN mime_type = 'application/pdf'                THEN 'PDF'
                            WHEN mime_type LIKE '%%spreadsheet%%'
                              OR mime_type = 'application/vnd.ms-excel'
                              OR mime_type LIKE '%%excel%%'                   THEN 'Spreadsheets'
                            WHEN mime_type LIKE '%%wordprocessingml%%'
                              OR mime_type = 'application/msword'
                              OR mime_type LIKE '%%word%%'                    THEN 'Documents'
                            WHEN mime_type LIKE 'text/%%'                     THEN 'Text'
                            WHEN mime_type LIKE '%%zip%%'
                              OR mime_type LIKE '%%compressed%%'              THEN 'Archives'
                            ELSE 'Other'
                        END,
                        'Unknown'
                    ) AS file_type_label,
                    COUNT(*) AS cnt
                FROM   one_drive_file
                WHERE  active     = TRUE
                  AND  file_type  = 'file'
                  AND  mime_type  IS NOT NULL
                  {where_clause}
                GROUP  BY file_type_label
                ORDER  BY cnt DESC
                LIMIT  %s
            """, params)
            return self.env.cr.dictfetchall()
        except Exception as e:
            _logger.exception("Error in get_top_file_types: %s", e)
            return []

    @api.model
    def get_largest_files(self, limit=10, config_id=False):
        """Return the top N largest files by size, optionally filtered by drive."""
        try:
            domain = [
                ('file_type', '=', 'file'),
                ('active', '=', True),
                ('file_size', '>', 0),
            ]
            if config_id:
                domain.append(('drive_config_id', '=', int(config_id)))

            files = self.env['one.drive.file'].sudo().search(
                domain, order='file_size desc', limit=limit
            )

            # Prefetch model names in batch
            models_to_fetch = list(set(f.res_model for f in files if f.res_model))
            model_names = {}
            if models_to_fetch:
                model_recs = self.env['ir.model'].sudo().search([('model', 'in', models_to_fetch)])
                model_names = {m.model: m.name for m in model_recs}

            result = []
            for f in files:
                res_model_label = model_names.get(f.res_model, f.res_model or '')
                result.append({
                    'id': f.id,
                    'name': f.name,
                    'size': _format_size(f.file_size),
                    'size_bytes': f.file_size,
                    'res_model': f.res_model or '',
                    'res_model_label': res_model_label,
                    'mime_type': f.mime_type or '',
                    'drive_url': f.one_drive_url or '',
                    'display_path': f.display_path or '',
                    'drive_config_id': f.drive_config_id.id,
                    'parent_folder_id': f.parent_folder_id.id if f.parent_folder_id else False,
                    'root_folder_id': f.root_folder_id.id if f.root_folder_id else False,
                })
            return result
        except Exception:
            return []

    @api.model
    def get_active_shares(self, limit=20, config_id=False):
        """Return files/folders that have active sharing permissions."""
        try:
            GDFile = self.env['one.drive.file'].sudo()
            domain = [
                '|', ('permission_type', '!=', 'restricted'), ('shared_people_count', '>', 0),
                ('active', '=', True),
            ]
            if config_id:
                domain.append(('drive_config_id', '=', int(config_id)))

            shared_records = GDFile.search(domain, order='write_date desc', limit=limit)

            # Prefetch model names in batch
            models_to_fetch = list(set(f.res_model for f in shared_records if f.res_model))
            model_names = {}
            if models_to_fetch:
                model_recs = self.env['ir.model'].sudo().search([('model', 'in', models_to_fetch)])
                model_names = {m.model: m.name for m in model_recs}

            # Group record IDs by model and prefetch display names in batch
            by_model = {}
            for f in shared_records:
                if f.res_model and f.res_id:
                    by_model.setdefault(f.res_model, set()).add(f.res_id)
            
            display_names = {}
            for model, ids in by_model.items():
                try:
                    recs = self.env[model].sudo().browse(list(ids)).exists()
                    for r in recs:
                        display_names[(model, r.id)] = r.display_name
                except Exception:
                    pass

            result = []
            for f in shared_records:
                access_label = "Restricted"
                if f.permission_type == 'anyone':
                    access_label = f"Anyone ({f.anyone_role.capitalize() if f.anyone_role else 'Reader'})"
                elif f.permission_type == 'domain':
                    access_label = "Domain"
                
                if f.shared_people_count > 0:
                    access_label += f" + {f.shared_people_count} people"

                res_model_label = model_names.get(f.res_model, f.res_model or '')
                record_name = display_names.get((f.res_model, f.res_id), '')

                result.append({
                    'id': f.id,
                    'name': f.name,
                    'file_type': f.file_type,
                    'display_path': f.display_path or '',
                    'permission_type': f.permission_type,
                    'access_label': access_label,
                    'sync_state': f.sync_state,
                    'drive_url': f.one_drive_url or '',
                    'one_drive_file_id': f.one_drive_file_id or '',
                    'one_drive_url': f.one_drive_url or '',
                    'writers_can_share': f.writers_can_share,
                    'copy_requires_writer': f.copy_requires_writer,
                    'drive_config_id': f.drive_config_id.id,
                    'parent_folder_id': f.parent_folder_id.id if f.parent_folder_id else False,
                    'root_folder_id': f.root_folder_id.id if f.root_folder_id else False,
                    'res_model': f.res_model or '',
                    'res_model_label': res_model_label,
                    'res_id': f.res_id or False,
                    'record_name': record_name or '',
                })
            return result
        except Exception as e:
            _logger.error("Error in get_active_shares: %s", str(e))
            return []

    @api.model
    def get_recent_files(self, limit=1000):
        """Return recently synced files."""
        try:
            files = self.env['one.drive.file'].sudo().search([
                ('file_type', '=', 'file'),
                ('active', '=', True),
            ], order='write_date desc', limit=limit)

            result = []
            for f in files:
                result.append({
                    'id': f.id,
                    'name': f.name,
                    'size': _format_size(f.file_size or 0),
                    'mime_type': f.mime_type or '',
                    'drive_url': f.one_drive_url or '',
                    'one_drive_file_id': f.one_drive_file_id or '',
                    'date': fields.Datetime.context_timestamp(self, f.write_date).strftime("%b %d, %H:%M") if f.write_date else '',
                    'res_model': f.res_model or '',
                    'sync_state': f.sync_state or '',
                })
            return result
        except Exception as e:
            _logger.exception("Error in get_recent_files: %s", e)
            return []

    @api.model
    def get_files_by_model(self):
        return []

    @api.model
    def get_storage_by_model(self):
        return []

    @api.model
    def get_orphan_attachments(self):
        return []

    @api.model
    def load_dashboard_layout(self):
        """Return the current user's saved layout JSON, or None."""
        layout = self.env['one_drive.dashboard.layout'].sudo().search(
            [('user_id', '=', self.env.uid)], limit=1
        )
        return layout.layout_json if layout else None

    @api.model
    def save_dashboard_layout(self, layout_json):
        """Upsert the current user's dashboard layout."""
        Layout = self.env['one_drive.dashboard.layout'].sudo()
        existing = Layout.search([('user_id', '=', self.env.uid)], limit=1)
        if existing:
            existing.write({'layout_json': layout_json})
        else:
            Layout.create({
                'user_id': self.env.uid,
                'layout_json': layout_json,
            })
        return True
