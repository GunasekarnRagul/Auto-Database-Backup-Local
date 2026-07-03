# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime

_logger = logging.getLogger(__name__)


class CloudAttachmentConfig(models.Model):
    _name = 'cloud.attachment.config'
    _description = 'Cloud Attachment Sync Configuration'
    _rec_name = 'name'

    name = fields.Char('Configuration Name', required=True)

    # Step 1 — Odoo model
    module_config_id = fields.Many2one('cloud.model.config', string='Module', required=True,
                                       ondelete='cascade',
                                       domain="[('id', 'not in', existing_module_ids)]")
    model_name = fields.Char('Model', compute='_compute_model_name', store=True, readonly=True, index=True)

    # Step 2 — Cloud provider config
    cloud_provider_id = fields.Many2one('cloud.provider.config', string='Cloud Provider', required=True, ondelete='cascade')
    provider_type     = fields.Selection(related='cloud_provider_id.provider_type', store=True)

    # Step 3 — Destination folder
    cloud_folder_id = fields.Many2one('cloud.file', string='Folder',
                                      domain="[('file_type','=','folder'),('drive_config_id','=',cloud_provider_id),('parent_folder_id','=',False)]",
                                      required=True, ondelete='cascade')

    # Step 4 — File type filter
    file_type = fields.Selection([
        ('pdf', 'PDF Documents'),
        ('doc', 'Word Documents'),
        ('xls', 'Excel Files'),
        ('ppt', 'PowerPoint Files'),
        ('img', 'Images'),
        ('all', 'All File Types'),
    ], string='File Type', default='all', required=True)

    auto_sync_mode = fields.Boolean('Auto Sync Mode', default=False)

    storage_mode = fields.Selection([
        ('drive', 'Drive Only'),
        ('dual',  'Dual (Drive + Odoo)'),
        ('odoo',  'Odoo Only (no sync)'),
    ], string='Storage Mode', default='dual', required=True)

    last_synced = fields.Char('Last Synced', default='Never')
    sync_count  = fields.Integer('Files Synced', default=0, readonly=True)
    state = fields.Selection([
        ('draft',  'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
    ], string='State', default='active')

    # Sync statistics
    synced_attachment_count     = fields.Integer('Synced Files',     compute='_compute_sync_statistics')
    unsynced_attachment_count   = fields.Integer('Unsynced Files',   compute='_compute_sync_statistics')
    dual_attachment_count       = fields.Integer('Dual Sync Files',  compute='_compute_sync_statistics')
    drive_only_attachment_count = fields.Integer('Drive Only Files', compute='_compute_sync_statistics')
    sync_percentage             = fields.Float('Sync %',             compute='_compute_sync_statistics')
    model_attachment_count      = fields.Integer('Total Attachments', compute='_compute_model_attachment_count')
    folder_path                 = fields.Char('Folder Path',         compute='_compute_folder_path')

    existing_module_ids = fields.Many2many('cloud.model.config', compute='_compute_existing_module_ids',
                                           string='Existing Modules')

    _sql_constraints = [
        ('model_name_unique', 'unique(model_name)',
         'This module is already configured. Only one configuration per model is allowed.'),
    ]

    # ─────────────────────────────────────────────────────────────────────────
    # Computed helpers
    # ─────────────────────────────────────────────────────────────────────────

    @api.depends('module_config_id')
    def _compute_model_name(self):
        for r in self:
            r.model_name = r.module_config_id.res_model if r.module_config_id else False

    def _compute_existing_module_ids(self):
        all_cfgs = self.search([])
        configured = all_cfgs.mapped('module_config_id').ids
        for r in self:
            if r.id and r.module_config_id:
                r.existing_module_ids = [(6, 0, [mid for mid in configured if mid != r.module_config_id.id])]
            else:
                r.existing_module_ids = [(6, 0, configured)]

    @api.onchange('module_config_id')
    def _onchange_module_config_id(self):
        if self.module_config_id:
            self.model_name = self.module_config_id.res_model
            if not self.name or self.name == 'New Configuration':
                self.name = f'{self.module_config_id.model_label} Sync'

    @api.depends('cloud_provider_id', 'cloud_folder_id')
    def _compute_folder_path(self):
        for r in self:
            parts = []
            if r.cloud_provider_id:
                parts.append(r.cloud_provider_id.name)
            if r.cloud_folder_id:
                ancestors = []
                parent = r.cloud_folder_id.parent_folder_id
                while parent:
                    ancestors.insert(0, parent.name)
                    parent = parent.parent_folder_id
                parts.extend(ancestors)
                parts.append(r.cloud_folder_id.name)
            r.folder_path = ' / '.join(parts) if parts else ''

    # ─────────────────────────────────────────────────────────────────────────
    # Attachment helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _get_target_attachments(self, extra_domain=None):
        """Return ir.attachment records for this model (direct + chatter copies, deduped)."""
        self.ensure_one()
        Att = self.env['ir.attachment'].sudo()
        if not self.model_name:
            return Att.browse()
        m = self.model_name.strip()
        messages = self.env['mail.message'].sudo().search([('model', '=', m)], limit=10000)
        mid_to_rid = {msg.id: msg.res_id for msg in messages if msg.res_id}

        all_mail = Att.browse()
        if mid_to_rid:
            all_mail = Att.search([('res_model', '=', 'mail.message'), ('res_id', 'in', list(mid_to_rid.keys()))])
        mail_keys = {(mid_to_rid[a.res_id], a.name) for a in all_mail if a.res_id in mid_to_rid}

        mail_atts = Att.browse()
        if mid_to_rid:
            dom = (extra_domain or []) + [('res_model', '=', 'mail.message'), ('res_id', 'in', list(mid_to_rid.keys()))]
            mail_atts = Att.search(dom)

        direct = Att.search((extra_domain or []) + [('res_model', '=', m)])
        direct_filtered = direct.filtered(lambda a: (a.res_id, a.name) not in mail_keys)
        return mail_atts | direct_filtered

    @api.depends('model_name', 'sync_count', 'file_type', 'storage_mode', 'cloud_provider_id', 'cloud_folder_id')
    def _compute_model_attachment_count(self):
        for r in self:
            if r.model_name:
                atts = r._get_target_attachments()
                atts = atts.filtered(lambda a: a.cloud_file_id or (a.datas or a.raw) and a.type != 'url')
                if r.file_type != 'all':
                    ir = self.env['ir.attachment']
                    atts = atts.filtered(lambda a: ir._matches_file_type_filter(a, r))
                r.model_attachment_count = len(atts)
            else:
                r.model_attachment_count = 0

    @api.depends('model_name', 'sync_count', 'file_type', 'storage_mode', 'cloud_provider_id', 'cloud_folder_id')
    def _compute_sync_statistics(self):
        for r in self:
            if r.model_name:
                atts = r._get_target_attachments()
                atts = atts.filtered(lambda a: a.cloud_file_id or (a.datas or a.raw) and a.type != 'url')
                if r.file_type != 'all':
                    ir = self.env['ir.attachment']
                    atts = atts.filtered(lambda a: ir._matches_file_type_filter(a, r))
                synced   = atts.filtered(lambda a: a.cloud_file_id)
                unsynced = atts - synced
                dual     = synced.filtered(lambda a: a.type != 'url')
                dronly   = synced.filtered(lambda a: a.type == 'url')
                total    = len(atts)
                r.synced_attachment_count     = len(synced)
                r.unsynced_attachment_count   = len(unsynced)
                r.dual_attachment_count       = len(dual)
                r.drive_only_attachment_count = len(dronly)
                r.sync_percentage             = (len(synced) / total * 100) if total > 0 else 0
            else:
                r.synced_attachment_count     = 0
                r.unsynced_attachment_count   = 0
                r.dual_attachment_count       = 0
                r.drive_only_attachment_count = 0
                r.sync_percentage             = 0

    # ─────────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────────

    def action_manual_drive_sync(self):
        self.ensure_one()
        if not self.cloud_provider_id:
            raise UserError('⚠️ No Cloud Provider selected. Please choose one in Step 2.')
        if not self.cloud_folder_id:
            raise UserError('⚠️ No destination folder selected. Please choose a folder in Step 3.')
        if self.storage_mode == 'odoo':
            return self._notify('🗄️ Sync Skipped — Odoo Only Mode',
                                'Storage Mode is "Odoo Only". Change to Drive or Dual to sync.', 'warning')

        atts = self._get_target_attachments([('cloud_file_id', '=', False)])
        atts = atts.filtered(lambda a: a.datas or a.raw)
        if self.file_type != 'all':
            ir = self.env['ir.attachment']
            atts = atts.filtered(lambda a: ir._matches_file_type_filter(a, self))

        if not atts:
            return self._notify('✅ Nothing to Sync', 'All files are already synced.', 'info')

        ok = err = 0
        for att in atts:
            try:
                with self.env.cr.savepoint():
                    att.with_context(sync_type='manual')._auto_sync_to_cloud(self)
                    ok += 1
            except Exception as e:
                err += 1
                _logger.error("Manual sync failed for '%s': %s", att.name, e)

        msg = f'✅ {ok} file(s) synced to {self.cloud_provider_id.name}.'
        if err:
            msg += f' ⚠️ {err} failed — check Activity Log.'
        return self._notify(f'{self.module_config_id.model_label or self.model_name} Sync Result', msg,
                            'success' if ok else 'warning')

    def action_pause_config(self):
        self.ensure_one()
        self.write({'state': 'paused', 'auto_sync_mode': False})
        return self._notify('Configuration Paused', 'Auto-sync has been paused.', 'warning')

    def action_activate_config(self):
        self.ensure_one()
        self.write({'state': 'active'})
        return self._notify('Configuration Activated', 'This configuration is now active.', 'success')

    def action_apply_sync(self):
        return self.action_manual_drive_sync()

    def _notify(self, title, message, ntype='info'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'type': ntype, 'sticky': False},
        }
