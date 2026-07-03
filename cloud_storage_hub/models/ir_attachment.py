# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    # Unified cloud file link — replaces the per-module nextcloud_file_id, google_drive_file_id, etc.
    cloud_file_id = fields.Many2one('cloud.file', string='Cloud File', ondelete='set null',
                                    index=True, copy=False)
    cloud_url     = fields.Char('Cloud URL', related='cloud_file_id.cloud_url', store=False, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._trigger_auto_sync()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'datas' in vals or 'raw' in vals:
            self._trigger_auto_sync()
        return res

    def _trigger_auto_sync(self):
        """Auto-sync new/updated attachments to the cloud if a matching config exists."""
        sync_type = self.env.context.get('sync_type')
        if sync_type == 'skip':
            return
        for att in self:
            if not att.res_model or att.cloud_file_id:
                continue
            if not (att.datas or att.raw) or att.type == 'url':
                continue
            # Find an active attachment sync config for this model
            config = self.env['cloud.attachment.config'].sudo().search([
                ('model_name', '=', att.res_model),
                ('state', '=', 'active'),
                ('auto_sync_mode', '=', True),
            ], limit=1)
            if not config or config.storage_mode == 'odoo':
                continue
            try:
                att.with_context(sync_type='auto')._auto_sync_to_cloud(config)
            except Exception as e:
                _logger.debug("Auto-sync skipped for '%s': %s", att.name, e)

    def _auto_sync_to_cloud(self, config):
        """Upload this attachment to the cloud provider specified by config."""
        self.ensure_one()
        provider = config.cloud_provider_id
        if not provider or provider.state != 'connected':
            return
        engine = self.env['cloud.sync.engine'].sudo()._get_engine(provider.provider_type)
        engine.upload_attachment(self, config)

    @api.model
    def _matches_file_type_filter(self, att, config):
        """Check if an attachment matches the file type filter of a sync config."""
        ft = config.file_type
        if ft == 'all':
            return True
        name = (att.name or '').lower()
        mime = (att.mimetype or '').lower()
        if ft == 'pdf':
            return 'pdf' in mime or name.endswith('.pdf')
        if ft == 'doc':
            return any(x in mime for x in ('word', 'document')) or name.endswith(('.doc', '.docx'))
        if ft == 'xls':
            return any(x in mime for x in ('excel', 'spreadsheet')) or name.endswith(('.xls', '.xlsx'))
        if ft == 'ppt':
            return any(x in mime for x in ('powerpoint', 'presentation')) or name.endswith(('.ppt', '.pptx'))
        if ft == 'img':
            return 'image/' in mime or name.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))
        return True
