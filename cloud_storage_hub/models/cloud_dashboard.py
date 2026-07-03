# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


def _fmt(size_bytes):
    if not size_bytes:
        return '0 B'
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f'{size_bytes:.1f} {unit}'
        size_bytes /= 1024.0
    return f'{size_bytes:.1f} PB'


class CloudDashboard(models.AbstractModel):
    _name = 'cloud.dashboard'
    _description = 'Cloud Hub Dashboard Data'

    @api.model
    def get_kpi_data(self, provider_type=False):
        """Fetch primary KPIs, health status, and fleet details.
        If provider_type is supplied, scope stats to that provider only.
        """
        try:
            CFile = self.env['cloud.file'].sudo()
            SyncConfig = self.env['cloud.attachment.config'].sudo()
            SyncLog = self.env['cloud.sync.log'].sudo()
            now = fields.Datetime.now()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

            base_domain = [('active', '=', True)]
            if provider_type:
                base_domain.append(('provider_type', '=', provider_type))

            total_files_synced  = CFile.search_count(base_domain + [('sync_state', '=', 'synced'), ('file_type', '=', 'file')])
            total_folders_synced = CFile.search_count(base_domain + [('sync_state', '=', 'synced'), ('file_type', '=', 'folder')])

            log_domain = []
            if provider_type:
                configs = self.env['cloud.provider.config'].sudo().search([('provider_type', '=', provider_type)])
                log_domain = [('drive_config_id', 'in', configs.ids)]

            synced_today = SyncLog.search_count(log_domain + [
                ('state', '=', 'success'), ('operation', '=', 'upload'),
                ('create_date', '>=', today_start),
            ])

            active_configs = SyncConfig.search([('state', '=', 'active')])
            if provider_type:
                active_configs = active_configs.filtered(lambda c: c.cloud_provider_id.provider_type == provider_type)

            pending_count   = CFile.search_count(base_domain + [('sync_state', '=', 'pending')])
            error_count     = CFile.search_count(base_domain + [('sync_state', '=', 'error')])
            uploading_count = CFile.search_count(base_domain + [('sync_state', '=', 'uploading')])
            attachment_pending = sum(active_configs.mapped('unsynced_attachment_count'))
            total_pending = pending_count + error_count + uploading_count + attachment_pending

            # Fleet — all provider configs
            configs_domain = [('active', '=', True)]
            if provider_type:
                configs_domain.append(('provider_type', '=', provider_type))
            configs = self.env['cloud.provider.config'].sudo().search(configs_domain)

            total_drives  = len(configs)
            online_drives = len(configs.filtered(lambda c: c.state == 'connected'))

            fleet = []
            for c in configs:
                d = [('drive_config_id', '=', c.id), ('file_type', '=', 'file'),
                     ('sync_state', '=', 'synced'), ('active', '=', True)]
                files = CFile.search(d)
                fleet.append({
                    'id': c.id,
                    'name': c.name,
                    'provider_type': c.provider_type,
                    'provider_label': c.provider_label,
                    'state': c.state,
                    'is_connected': c.state == 'connected',
                    'folders_count': len(c.root_ids),
                    'odoo_file_count': len(files),
                    'odoo_storage_formatted': _fmt(sum(files.mapped('file_size'))),
                })

            fails_today = SyncLog.search_count(log_domain + [
                ('state', '=', 'fail'), ('create_date', '>=', today_start),
            ])
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            files_this_month = CFile.search_count(base_domain + [
                ('file_type', '=', 'file'), ('create_date', '>=', month_start),
            ])

            model_details = []
            for cfg in active_configs:
                mode = dict(cfg._fields['storage_mode'].selection).get(cfg.storage_mode, cfg.storage_mode)
                model_details.append({
                    'id': cfg.id,
                    'name': cfg.name,
                    'model_name': cfg.model_name or '',
                    'storage_mode': mode,
                    'state': cfg.state,
                })

            return {
                'kpis': {
                    'total_files_synced':   total_files_synced,
                    'total_folders_synced': total_folders_synced,
                    'synced_today':         synced_today,
                    'pending_syncs':        total_pending,
                    'pending_count':        pending_count,
                    'error_count':          error_count,
                    'uploading_count':      uploading_count,
                    'fails_today':          fails_today,
                    'files_this_month':     files_this_month,
                },
                'health': {
                    'total_drives':   total_drives,
                    'online_drives':  online_drives,
                    'total_configs':  len(active_configs) + SyncConfig.search_count([('state', '=', 'paused')]),
                    'active_configs': len(active_configs),
                    'fleet':          fleet,
                    'model_details':  model_details,
                },
            }
        except Exception as e:
            _logger.exception('Error in get_kpi_data: %s', e)
            return {'kpis': {}, 'health': {'fleet': []}}

    @api.model
    def get_storage_stats(self, provider_type=False):
        try:
            base = [('sync_state', '=', 'synced'), ('file_type', '=', 'file'), ('active', '=', True)]
            if provider_type:
                base.append(('provider_type', '=', provider_type))
            files = self.env['cloud.file'].sudo().search(base)
            used = sum(files.mapped('file_size'))
            drive_only_cfgs = self.env['cloud.attachment.config'].sudo().search([
                ('storage_mode', '=', 'drive')
            ]).mapped('cloud_provider_id.id')
            saved = sum(files.filtered(lambda f: f.drive_config_id.id in drive_only_cfgs).mapped('file_size'))
            return {
                'drive_space_used_formatted': _fmt(used),
                'storage_saved_formatted':    _fmt(saved),
            }
        except Exception as e:
            _logger.exception('Error in get_storage_stats: %s', e)
            return {'drive_space_used_formatted': '0 B', 'storage_saved_formatted': '0 B'}

    @api.model
    def get_activity_logs(self, period='today', operation=False, limit=1000, provider_type=False):
        try:
            SyncLog = self.env['cloud.sync.log'].sudo()
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
            if provider_type:
                domain.append(('provider_type', '=', provider_type))

            logs = SyncLog.search(domain, order='create_date desc', limit=limit)
            return [{
                'id':            log.id,
                'file_name':     log.file_name,
                'operation':     log.operation,
                'state':         log.state,
                'provider_type': log.provider_type or '',
                'drive_name':    log.drive_name or '',
                'date':          fields.Datetime.context_timestamp(self, log.create_date).strftime('%Y-%m-%d %H:%M:%S') if log.create_date else '',
                'error_message': log.error_message or '',
            } for log in logs]
        except Exception as e:
            _logger.exception('Error in get_activity_logs: %s', e)
            return []

    @api.model
    def get_sync_trend_data(self, days=7, start_date=False, end_date=False, provider_type=False):
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

            cutoff   = datetime.combine(s_dt, datetime.min.time())
            end_lim  = datetime.combine(e_dt, datetime.max.time())

            extra = ''
            params = [cutoff, end_lim]
            if provider_type:
                extra = "AND provider_type = %s"
                params.append(provider_type)

            self.env.cr.execute(f"""
                SELECT TO_CHAR(create_date AT TIME ZONE 'UTC','YYYY-MM-DD') AS sync_date,
                       state, COUNT(*) AS cnt
                FROM cloud_sync_log
                WHERE create_date >= %s AND create_date <= %s {extra}
                GROUP BY sync_date, state
                ORDER BY sync_date
            """, params)

            rows = self.env.cr.dictfetchall()
            labels = []
            success_map = {}
            failed_map  = {}
            for i in range((e_dt - s_dt).days + 1):
                d = (s_dt + timedelta(days=i)).isoformat()
                labels.append(d)
                success_map[d] = 0
                failed_map[d]  = 0
            for row in rows:
                d = row['sync_date']
                if d in success_map:
                    if row['state'] == 'success':
                        success_map[d] = row['cnt']
                    else:
                        failed_map[d] = row['cnt']
            return {
                'labels':  labels,
                'success': [success_map[d] for d in labels],
                'failed':  [failed_map[d]  for d in labels],
            }
        except Exception as e:
            _logger.exception('Error in get_sync_trend_data: %s', e)
            return {'labels': [], 'success': [], 'failed': []}

    @api.model
    def get_model_breakdown(self, provider_type=False):
        try:
            configs = self.env['cloud.attachment.config'].sudo().search([('state', '=', 'active')])
            if provider_type:
                configs = configs.filtered(lambda c: c.cloud_provider_id.provider_type == provider_type)
            return [{
                'id':           cfg.id,
                'name':         cfg.name,
                'model_name':   cfg.model_name or '',
                'storage_mode': dict(cfg._fields['storage_mode'].selection).get(cfg.storage_mode, cfg.storage_mode),
                'synced':       cfg.synced_attachment_count,
                'unsynced':     cfg.unsynced_attachment_count,
                'dual':         cfg.dual_attachment_count,
                'drive_only':   cfg.drive_only_attachment_count,
                'sync_pct':     round(cfg.sync_percentage, 1),
                'total':        cfg.model_attachment_count,
            } for cfg in configs]
        except Exception as e:
            _logger.exception('Error in get_model_breakdown: %s', e)
            return []

    @api.model
    def get_top_file_types(self, limit=8, provider_type=False):
        try:
            extra = ''
            params = []
            if provider_type:
                extra = 'AND provider_type = %s'
                params.append(provider_type)
            params.append(limit)
            self.env.cr.execute(f"""
                SELECT COALESCE(
                    CASE
                        WHEN mime_type LIKE 'image/%%'           THEN 'Images'
                        WHEN mime_type LIKE 'video/%%'           THEN 'Videos'
                        WHEN mime_type = 'application/pdf'       THEN 'PDF'
                        WHEN mime_type LIKE '%%spreadsheet%%'
                          OR mime_type LIKE '%%excel%%'          THEN 'Spreadsheets'
                        WHEN mime_type LIKE '%%wordprocessingml%%'
                          OR mime_type LIKE '%%word%%'           THEN 'Documents'
                        WHEN mime_type LIKE 'text/%%'            THEN 'Text'
                        WHEN mime_type LIKE '%%zip%%'
                          OR mime_type LIKE '%%compressed%%'     THEN 'Archives'
                        ELSE 'Other'
                    END, 'Unknown'
                ) AS file_type_label, COUNT(*) AS cnt
                FROM cloud_file
                WHERE active=TRUE AND file_type='file' AND mime_type IS NOT NULL {extra}
                GROUP BY file_type_label ORDER BY cnt DESC LIMIT %s
            """, params)
            return self.env.cr.dictfetchall()
        except Exception as e:
            _logger.exception('Error in get_top_file_types: %s', e)
            return []

    @api.model
    def get_largest_files(self, limit=10, provider_type=False):
        try:
            domain = [('file_type', '=', 'file'), ('active', '=', True), ('file_size', '>', 0)]
            if provider_type:
                domain.append(('provider_type', '=', provider_type))
            files = self.env['cloud.file'].sudo().search(domain, order='file_size desc', limit=limit)
            result = []
            for f in files:
                result.append({
                    'id':             f.id,
                    'name':           f.name,
                    'size':           f.file_size_fmt,
                    'size_bytes':     f.file_size,
                    'res_model':      f.res_model or '',
                    'mime_type':      f.mime_type or '',
                    'drive_url':      f.cloud_url or '',
                    'display_path':   f.display_path or '',
                    'drive_config_id': f.drive_config_id.id,
                    'provider_type':  f.provider_type or '',
                })
            return result
        except Exception:
            return []

    @api.model
    def get_recent_files(self, limit=1000, provider_type=False):
        try:
            domain = [('file_type', '=', 'file'), ('active', '=', True)]
            if provider_type:
                domain.append(('provider_type', '=', provider_type))
            files = self.env['cloud.file'].sudo().search(domain, order='write_date desc', limit=limit)
            return [{
                'id':           f.id,
                'name':         f.name,
                'size':         f.file_size_fmt,
                'mime_type':    f.mime_type or '',
                'drive_url':    f.cloud_url or '',
                'date':         fields.Datetime.context_timestamp(self, f.write_date).strftime('%b %d, %H:%M') if f.write_date else '',
                'res_model':    f.res_model or '',
                'sync_state':   f.sync_state or '',
                'provider_type': f.provider_type or '',
            } for f in files]
        except Exception as e:
            _logger.exception('Error in get_recent_files: %s', e)
            return []

    @api.model
    def get_provider_summary(self):
        """Return connection status and file counts for each provider."""
        providers = [
            ('aws', 'AWS S3'),
            ('gdrive', 'Google Drive'),
            ('onedrive', 'Microsoft OneDrive'),
            ('nextcloud', 'Nextcloud'),
            ('dropbox', 'Dropbox'),
        ]
        result = []
        for ptype, plabel in providers:
            configs = self.env['cloud.provider.config'].sudo().search([
                ('provider_type', '=', ptype), ('active', '=', True),
            ])
            connected = any(c.state == 'connected' for c in configs)
            file_count = self.env['cloud.file'].sudo().search_count([
                ('provider_type', '=', ptype), ('file_type', '=', 'file'), ('active', '=', True),
            ])
            result.append({
                'provider_type': ptype,
                'label':        plabel,
                'connected':    connected,
                'config_count': len(configs),
                'file_count':   file_count,
            })
        return result

    @api.model
    def load_dashboard_layout(self):
        layout = self.env['cloud.dashboard.layout'].sudo().search(
            [('user_id', '=', self.env.uid)], limit=1
        )
        return layout.layout_json if layout else None

    @api.model
    def save_dashboard_layout(self, layout_json):
        Layout = self.env['cloud.dashboard.layout'].sudo()
        existing = Layout.search([('user_id', '=', self.env.uid)], limit=1)
        if existing:
            existing.write({'layout_json': layout_json})
        else:
            Layout.create({'user_id': self.env.uid, 'layout_json': layout_json})
        return True

    @api.model
    def get_duplicate_summary(self):
        try:
            groups = self.env['cloud.file'].sudo().get_duplicate_groups()
            return {'duplicate_count': sum(len(g['duplicate_ids']) for g in groups)}
        except Exception:
            return {'duplicate_count': 0}

    @api.model
    def get_error_summary(self, provider_type=False):
        try:
            SyncLog = self.env['cloud.sync.log'].sudo()
            now = fields.Datetime.now()
            domain_base = [('state', '=', 'fail')]
            if provider_type:
                domain_base.append(('provider_type', '=', provider_type))
            fails_24h = SyncLog.search_count(domain_base + [('create_date', '>=', now - timedelta(hours=24))])
            fails_7d  = SyncLog.search_count(domain_base + [('create_date', '>=', now - timedelta(days=7))])
            return {'fails_24h': fails_24h, 'fails_7d': fails_7d, 'top_errors': []}
        except Exception:
            return {'fails_24h': 0, 'fails_7d': 0, 'top_errors': []}

    # Stubs kept for JS compatibility
    @api.model
    def get_files_by_model(self): return []
    @api.model
    def get_storage_by_model(self): return []
    @api.model
    def get_orphan_attachments(self): return []
    @api.model
    def get_top_uploaders(self, limit=10): return []
    @api.model
    def get_active_shares(self, limit=20, provider_type=False): return []
    @api.model
    def get_drive_quota(self, config_id=False): return {'available': False}
