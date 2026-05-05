# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from odoo import models, fields, api

import json
import logging
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
    _name = 'google.drive.dashboard'
    _description = 'Google Drive Sync Dashboard Data'

    # ──────────────────────────────────────────────
    # 1. Fast KPIs & Health (loads first)
    # ──────────────────────────────────────────────

    @api.model
    def get_kpi_data(self):
        """Fetch primary KPIs, health status, and detailed sub-metrics."""
        try:
            GDFile = self.env['google.drive.file'].sudo()
            SyncConfig = self.env['attachment.sync.config'].sudo()
            SyncLog = self.env['google.drive.sync.log'].sudo()
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

            # ── Pending (breakdown by state) ──
            pending_count = GDFile.search_count([
                ('sync_state', '=', 'pending'), ('active', '=', True),
            ])
            error_count = GDFile.search_count([
                ('sync_state', '=', 'error'), ('active', '=', True),
            ])
            uploading_count = GDFile.search_count([
                ('sync_state', '=', 'uploading'), ('active', '=', True),
            ])
            total_pending = pending_count + error_count + uploading_count

            # ── System Health (detailed) ──
            configs = self.env['google.drive.config'].sudo().search([('active', '=', True)])
            primary_config = configs[:1] if configs else self.env['google.drive.config']
            connection_status = 'online' if primary_config and primary_config.refresh_token else 'offline'

            drive_name = primary_config.name if primary_config else 'Not Connected'
            total_drives = len(configs)
            online_drives = len(configs.filtered(lambda c: c.refresh_token))
            total_roots = sum(len(c.root_ids.filtered(lambda r: r.active)) for c in configs)

            # ── Configuration Overview ──
            active_configs = SyncConfig.search([('state', '=', 'active')])
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
            for c in configs:
                # Stats for files specifically belonging to this drive in Odoo
                odoo_files = GDFile.search([
                    ('drive_config_id', '=', c.id),
                    ('file_type', '=', 'file'),
                    ('sync_state', '=', 'synced'),
                    ('active', '=', True),
                ])
                odoo_count = len(odoo_files)
                odoo_size = sum(odoo_files.mapped('file_size'))

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

            # ── Last Sync Timestamp ──
            last_log = SyncLog.search([('state', '=', 'success')], order='create_date desc', limit=1)
            last_sync_time = last_log.create_date.strftime("%b %d, %H:%M") if last_log and last_log.create_date else 'Never'

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
                ('permission_type', '!=', False),
                ('active', '=', True),
            ]) if 'permission_type' in GDFile._fields else 0

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
                    'last_sync_time': last_sync_time,
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

        # ──────────────────────────────────────────────
        # 2. Storage Stats (lazy loaded — heavy)
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_kpi_data: %s", e)
            return {'kpis': {}, 'health': {'fleet': []}}

    @api.model
    def get_storage_stats(self):
        """Fetch and aggregate heavy storage metrics."""
        try:
            GDFile = self.env['google.drive.file'].sudo()
            SyncConfig = self.env['attachment.sync.config'].sudo()

            synced_files = GDFile.search([
                ('sync_state', '=', 'synced'),
                ('file_type', '=', 'file'),
                ('active', '=', True),
            ])
            space_used_bytes = sum(synced_files.mapped('file_size'))

            drive_only_configs = SyncConfig.search([
                ('storage_mode', '=', 'drive'),
            ]).mapped('google_drive_id.id')
            saved_files = synced_files.filtered(
                lambda f: f.drive_config_id.id in drive_only_configs
            )
            storage_saved_bytes = sum(saved_files.mapped('file_size'))

            return {
                'storage_saved_formatted': _format_size(storage_saved_bytes),
                'drive_space_used_formatted': _format_size(space_used_bytes),
            }

        # ──────────────────────────────────────────────
        # 3. Activity Logs (with filters)
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_storage_stats: %s", e)
            return {'storage_saved_formatted': '0 B', 'drive_space_used_formatted': '0 B'}

    @api.model
    def get_activity_logs(self, period='today', operation=False, limit=1000):
        """Fetch sync logs with optional date-range and operation filters."""
        try:
            SyncLog = self.env['google.drive.sync.log'].sudo()
            domain = []
            now = fields.Datetime.now()

            if period == 'today':
                domain.append(('create_date', '>=', now.replace(hour=0, minute=0, second=0)))
            elif period == '7d':
                domain.append(('create_date', '>=', now - timedelta(days=7)))
            elif period == '30d':
                domain.append(('create_date', '>=', now - timedelta(days=30)))
            # 'all' → no date filter

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

        # ──────────────────────────────────────────────
        # 4. Duplicate Summary (lazy loaded — heavy)
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_activity_logs: %s", e)
            return []

    @api.model
    def get_duplicate_summary(self):
        """Heavy scan for duplicated files."""
        try:
            GDFile = self.env['google.drive.file'].sudo()
            duplicate_count = 0
            if hasattr(GDFile, 'get_duplicate_groups'):
                duplicate_groups = GDFile.get_duplicate_groups()
                duplicate_count = sum(len(group['duplicate_ids']) for group in duplicate_groups)

            return {
                'duplicate_count': duplicate_count,
            }

        # ──────────────────────────────────────────────
        # 5. Sync Trend Chart (7-day bar chart)
        # ──────────────────────────────────────────────

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
                FROM google_drive_sync_log
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

        # ──────────────────────────────────────────────
        # 6. Per-Model Breakdown
        # ──────────────────────────────────────────────

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

        # ──────────────────────────────────────────────
        # 7. Error / Failure Summary
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_model_breakdown: %s", e)
            return []

    @api.model
    def get_error_summary(self):
        """Aggregate failure stats from sync logs."""
        try:
            SyncLog = self.env['google.drive.sync.log'].sudo()
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
                FROM   google_drive_sync_log
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

        # ──────────────────────────────────────────────
        # 8. Drive Quota (Google API call)
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_error_summary: %s", e)
            return {'fails_24h': 0, 'fails_7d': 0, 'top_errors': []}

    @api.model
    def get_drive_quota(self, config_id=False):
        """Fetch Google Drive storage quota via the about.get API."""
        try:
            if config_id:
                # Ensure we have an integer ID
                cid = int(config_id)
                config = self.env['google.drive.config'].sudo().browse(cid)
            else:
                config = self.env['google.drive.config'].sudo().search([], limit=1)

            if not config or not config.exists():
                _logger.warning("get_drive_quota: Config not found for ID %s", config_id)
                return {'available': False, 'error': 'Config not found'}

            if not config.refresh_token:
                _logger.warning("get_drive_quota: No refresh token for drive %s", config.name)
                return {'available': False, 'error': 'No refresh token'}

            sync = self.env['google.drive.sync'].sudo()
            access_token = sync._get_access_token(config)
            if not access_token:
                _logger.warning("get_drive_quota: Failed to get access token for %s", config.name)
                return {'available': False, 'error': 'Token refresh failed'}

            import requests as http_requests
            resp = http_requests.get(
                'https://www.googleapis.com/drive/v3/about?fields=storageQuota',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=15,
            )
            
            if resp.status_code == 200:
                quota = resp.json().get('storageQuota', {})
                # Note: 'limit' might be missing for Unlimited (e.g. Workspace) accounts
                limit_bytes = int(quota.get('limit', 0))
                usage_bytes = int(quota.get('usage', 0))
                
                # Handling for Unlimited Drives (no limit or very high limit)
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
                _logger.warning("get_drive_quota: Google API returned %s: %s", resp.status_code, resp.text)
                return {'available': False, 'error': f'API Error {resp.status_code}'}
        except Exception as e:
            _logger.exception("Failed to fetch Drive quota for config %s: %s", config_id, str(e))
            return {'available': False, 'error': str(e)}

    # ──────────────────────────────────────────────
    # 10. NEW — Files by Model (attachment counts)
    # ──────────────────────────────────────────────

    @api.model
    def get_files_by_model(self):
        """Return attachment counts grouped by res_model from google.drive.file."""
        try:
            self.env.cr.execute("""
                SELECT res_model, COUNT(*) AS cnt
                FROM   google_drive_file
                WHERE  active = TRUE
                  AND  res_model IS NOT NULL
                  AND  res_model != ''
                  AND  file_type = 'file'
                GROUP  BY res_model
                ORDER  BY cnt DESC
                LIMIT  15
            """)
            rows = self.env.cr.dictfetchall()

            result = []
            for row in rows:
                # Try to get a human-friendly model description
                model_rec = self.env['ir.model'].sudo().search(
                    [('model', '=', row['res_model'])], limit=1
                )
                label = model_rec.name if model_rec else row['res_model']
                result.append({
                    'model': row['res_model'],
                    'label': label,
                    'count': row['cnt'],
                })
            return result

        # ──────────────────────────────────────────────
        # 11. NEW — Top Uploaders (by file count)
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_files_by_model: %s", e)
            return []

    @api.model
    def get_top_uploaders(self, limit=10):
        """Return top users by number of sync log uploads."""
        try:
            self.env.cr.execute("""
                SELECT l.user_id AS user_id,
                       u.name       AS user_name,
                       COUNT(*)     AS upload_count,
                       COALESCE(SUM(l.file_size), 0) AS total_bytes
                FROM   google_drive_sync_log l
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

        # ──────────────────────────────────────────────
        # 12. NEW — Top File Types (MIME breakdown)
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_top_uploaders: %s", e)
            return []

    @api.model
    def get_top_file_types(self, limit=8):
        """Return file-count distribution by MIME type."""
        try:
            self.env.cr.execute("""
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
                FROM   google_drive_file
                WHERE  active     = TRUE
                  AND  file_type  = 'file'
                  AND  mime_type  IS NOT NULL
                GROUP  BY file_type_label
                ORDER  BY cnt DESC
                LIMIT  %s
            """, [limit])
            return self.env.cr.dictfetchall()

        # ──────────────────────────────────────────────
        # 13. NEW — Largest Files
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_top_file_types: %s", e)
            return []

    @api.model
    def get_largest_files(self, limit=10):
        """Return the top N largest files by size."""
        try:
            files = self.env['google.drive.file'].sudo().search([
                ('file_type', '=', 'file'),
                ('active', '=', True),
                ('file_size', '>', 0),
            ], order='file_size desc', limit=limit)

            result = []
            for f in files:
                model_rec = self.env['ir.model'].sudo().search(
                    [('model', '=', f.res_model)], limit=1
                ) if f.res_model else None
                result.append({
                    'id': f.id,
                    'name': f.name,
                    'size': _format_size(f.file_size),
                    'size_bytes': f.file_size,
                    'res_model': f.res_model or '',
                    'res_model_label': model_rec.name if model_rec else (f.res_model or ''),
                    'mime_type': f.mime_type or '',
                    'drive_url': f.drive_url or '',
                })
            return result

        # ──────────────────────────────────────────────
        # 14. NEW — Recent Files
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_largest_files: %s", e)
            return []

    @api.model
    def get_recent_files(self, limit=1000):
        """Return recently synced files."""
        try:
            files = self.env['google.drive.file'].sudo().search([
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
                    'drive_url': f.google_url or '',
                    'google_file_id': f.google_file_id or '',
                    'date': fields.Datetime.context_timestamp(self, f.write_date).strftime("%b %d, %H:%M") if f.write_date else '',
                    'res_model': f.res_model or '',
                    'sync_state': f.sync_state or '',
                })
            return result

        except Exception as e:
            _logger.exception("Error in get_recent_files: %s", e)
            return []

    @api.model
    def get_storage_by_model(self, limit=8):
        """Return storage consumed per Odoo model."""
        try:
            self.env.cr.execute("""
                SELECT res_model,
                       COUNT(*)            AS file_count,
                       SUM(file_size)      AS total_bytes
                FROM   google_drive_file
                WHERE  active    = TRUE
                  AND  file_type = 'file'
                  AND  res_model IS NOT NULL
                  AND  res_model != ''
                  AND  file_size IS NOT NULL
                GROUP  BY res_model
                ORDER  BY total_bytes DESC
                LIMIT  %s
            """, [limit])
            rows = self.env.cr.dictfetchall()

            labels = []
            data = []
            for row in rows:
                model_rec = self.env['ir.model'].sudo().search(
                    [('model', '=', row['res_model'])], limit=1
                )
                labels.append(model_rec.name if model_rec else row['res_model'])
                data.append(round((row['total_bytes'] or 0) / (1024 * 1024), 2))  # MB

            return {'labels': labels, 'data': data}

        # ──────────────────────────────────────────────
        # 17. NEW — Orphan Attachments
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_storage_by_model: %s", e)
            return {'labels': [], 'data': []}

    @api.model
    def get_orphan_attachments(self):
        """Files with no linked Odoo record."""
        try:
            GDFile = self.env['google.drive.file'].sudo()
            orphan_count = GDFile.search_count([
                ('file_type', '=', 'file'),
                ('active', '=', True),
                '|',
                ('res_model', '=', False),
                ('res_id', '=', 0),
            ])
            return {'orphan_count': orphan_count}

        # ──────────────────────────────────────────────
        # 18. NEW — Layout Persistence
        # ──────────────────────────────────────────────

        except Exception as e:
            _logger.exception("Error in get_orphan_attachments: %s", e)
            return {'orphan_count': 0}

    @api.model
    def load_dashboard_layout(self):
        """Return the current user's saved layout JSON, or None."""
        layout = self.env['gdrive.dashboard.layout'].sudo().search(
            [('user_id', '=', self.env.uid)], limit=1
        )
        return layout.layout_json if layout else None

    @api.model
    def save_dashboard_layout(self, layout_json):
        """Upsert the current user's dashboard layout."""
        Layout = self.env['gdrive.dashboard.layout'].sudo()
        existing = Layout.search([('user_id', '=', self.env.uid)], limit=1)
        if existing:
            existing.write({'layout_json': layout_json})
        else:
            Layout.create({
                'user_id': self.env.uid,
                'layout_json': layout_json,
            })
        return True
