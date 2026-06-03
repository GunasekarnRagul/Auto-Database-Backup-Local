# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime

class AttachmentSyncConfig(models.Model):
    _name = 'attachment.sync.config'
    _description = 'Attachment Sync Configuration'
    _rec_name = 'name'

    # Configuration Name
    name = fields.Char('Configuration Name', required=True)

    # Step 1: Module/Model Selection
    module_config_id = fields.Many2one('one_drive.model.config', string='Module', required=True,
        domain="[('id', 'not in', existing_module_ids)]",
        help="Select the module for which you want to configure attachment synchronization.")
    
    model_name = fields.Char('Model', compute='_compute_model_name', store=True, readonly=True, index=True)

    # Step 2: Driver Selection
    one_drive_id = fields.Many2one('one.drive.config', string='Dropbox', required=True, ondelete='cascade')

    # Step 3: Folder Selection (filtered by driver)
    google_folder_id = fields.Many2one('one.drive.file',
        string='Folder',
        domain="[('file_type', '=', 'folder'), ('drive_config_id', '=', one_drive_id)]",
        required=True,
        ondelete='cascade')

    # Step 4: File Type Selection
    file_type = fields.Selection([
        ('pdf', 'PDF Documents'),
        ('doc', 'Word Documents'),
        ('xls', 'Excel Files'),
        ('ppt', 'PowerPoint Files'),
        ('img', 'Images'),
        ('all', 'All File Types'),
    ], string='File Type', default='all', required=True)

    # Sync mode toggle
    auto_sync_mode = fields.Boolean('Auto Sync Mode', default=False,
        help='Turn on to configure auto-sync; turn off to sync manually.')

    # Storage Mode
    storage_mode = fields.Selection([
        ('drive', 'Drive only'),
        ('dual', 'Dual (Drive + Odoo)'),
        ('odoo', 'Odoo only (no sync)'),
    ], string='Storage Mode', default='dual', required=True,
    help='Choose where the attachments should be stored.')

    # Configuration metadata
    last_synced = fields.Char('Last Synced', default='Never')
    sync_count = fields.Integer('Files Synced', default=0, readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
    ], string='State', default='active')

    # Sync statistics computed fields
    synced_attachment_count = fields.Integer('Synced Files', compute='_compute_sync_statistics')
    unsynced_attachment_count = fields.Integer('Unsynced Files', compute='_compute_sync_statistics')
    dual_attachment_count = fields.Integer('Dual Sync Files', compute='_compute_sync_statistics')
    drive_only_attachment_count = fields.Integer('Drive Only Files', compute='_compute_sync_statistics')
    sync_percentage = fields.Float('Sync %', compute='_compute_sync_statistics')

    @api.model
    def _get_model_selection(self):
        """Dynamic selection of models from one_drive.model.config."""
        configs = self.env['one_drive.model.config'].sudo().search([])
        return [(c.res_model, c.model_label) for c in configs]

    @api.model
    def get_config_for_model(self, res_model):
        """Helper to get the sync configuration for a specific model."""
        if not res_model:
            return False
        return self.search([
            ('model_name', '=', res_model),
            ('state', '=', 'active')
        ], limit=1)

    # Computed field to filter available modules in the UI
    existing_module_ids = fields.Many2many('one_drive.model.config', 
        compute='_compute_existing_module_ids', 
        string="Existing Modules")

    _sql_constraints = [
        ('model_name_unique', 'unique(model_name)', 'This module is already configured. You cannot create multiple configurations for the same module.')
    ]

    @api.depends('module_config_id')
    def _compute_model_name(self):
        """Automatically set the technical model name from the module configuration."""
        for record in self:
            if record.module_config_id:
                record.model_name = record.module_config_id.res_model
            else:
                record.model_name = False

    def _compute_existing_module_ids(self):
        """Get list of module configurations already used."""
        all_configs = self.search([])
        configured_module_ids = all_configs.mapped('module_config_id').ids
        for record in self:
            # When editing, don't hide the current module from its own record
            if record.id and record.module_config_id:
                record.existing_module_ids = [(6, 0, [mid for mid in configured_module_ids if mid != record.module_config_id.id])]
            else:
                record.existing_module_ids = [(6, 0, configured_module_ids)]

    @api.onchange('module_config_id')
    def _onchange_module_config_id(self):
        """Auto-fill model name and default configuration title when module is selected."""
        if self.module_config_id:
            self.model_name = self.module_config_id.res_model
            if not self.name or self.name == 'New Configuration':
                self.name = f"{self.module_config_id.model_label} Sync"

    # Computed field for folder path display
    folder_path = fields.Char('Folder Path', compute='_compute_folder_path')

    # Count of attachments for the selected model
    model_attachment_count = fields.Integer('Total Attachments', compute='_compute_model_attachment_count')

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to set default state to active for new configurations."""
        for vals in vals_list:
            if 'state' not in vals:
                vals['state'] = 'active'
        return super().create(vals_list)

    def action_save_and_return(self):
        """Save the configuration and return to kanban view."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'view_mode': 'kanban,tree,form',
            'view_type': 'kanban',
            'target': 'current',
        }

    @api.depends('one_drive_id', 'google_folder_id')
    def _compute_folder_path(self):
        for wizard in self:
            path_parts = []
            if wizard.one_drive_id:
                path_parts.append(wizard.one_drive_id.name)
            if wizard.google_folder_id:
                # Build ancestor chain from root → direct parent first, then append selected folder
                ancestors = []
                parent = wizard.google_folder_id.parent_folder_id
                while parent:
                    ancestors.insert(0, parent.name)
                    parent = parent.parent_folder_id
                path_parts.extend(ancestors)
                path_parts.append(wizard.google_folder_id.name)
            wizard.folder_path = ' / '.join(path_parts) if path_parts else ''

    def _get_target_attachments(self, extra_domain=None):
        """Get ir.attachment records for this model.

        Includes mail.message chatter copies so Drive link icons appear in the chatter.
        Deduplicates by (parent_record_id, filename): if both a direct attachment and a
        mail.message copy exist for the SAME record and filename, only the mail.message
        copy is returned — prevents double-counting AND correctly blocks a direct copy
        from showing as 'unsynced' when the chatter copy is already on Drive.
        """
        self.ensure_one()
        Attachment = self.env['ir.attachment'].sudo()
        if not self.model_name:
            return Attachment.browse()

        m_name = self.model_name.strip()

        # 1. Fetch messages for this model (capped to avoid memory issues on large DBs)
        messages = self.env['mail.message'].sudo().search(
            [('model', '=', m_name)], limit=10000
        )
        msg_id_to_res_id = {msg.id: msg.res_id for msg in messages if msg.res_id}

        # 2. Build dedup keys from ALL mail.message attachments (no extra_domain).
        #    This ensures a synced chatter copy blocks the direct copy even when
        #    extra_domain filters out the chatter copy (e.g. one_drive_file_id = False).
        all_mail_atts = Attachment.browse()
        if msg_id_to_res_id:
            all_mail_atts = Attachment.search([
                ('res_model', '=', 'mail.message'),
                ('res_id', 'in', list(msg_id_to_res_id.keys())),
            ])
        mail_dedup_keys = {
            (msg_id_to_res_id[att.res_id], att.name)
            for att in all_mail_atts
            if att.res_id in msg_id_to_res_id
        }

        # 3. Mail.message attachments WITH extra_domain applied
        mail_atts = Attachment.browse()
        if msg_id_to_res_id:
            domain = (extra_domain or []) + [
                ('res_model', '=', 'mail.message'),
                ('res_id', 'in', list(msg_id_to_res_id.keys())),
            ]
            mail_atts = Attachment.search(domain)

        # 4. Direct attachments WITH extra_domain, excluding any covered by a chatter copy
        direct_domain = (extra_domain or []) + [('res_model', '=', m_name)]
        direct_atts = Attachment.search(direct_domain)
        direct_filtered = direct_atts.filtered(
            lambda a: (a.res_id, a.name) not in mail_dedup_keys
        )

        return mail_atts | direct_filtered

    @api.depends('model_name', 'sync_count', 'file_type', 'storage_mode', 'one_drive_id', 'google_folder_id')
    def _compute_model_attachment_count(self):
        """Count total attachments in Odoo matching the configuration criteria."""
        for record in self:
            if record.model_name:
                all_attachments = record._get_target_attachments()
                all_attachments = all_attachments.filtered(
                    lambda a: a.one_drive_file_id or (a.datas or a.raw) and a.type != 'url'
                )
                if record.file_type != 'all':
                    ir_att = self.env['ir.attachment']
                    all_attachments = all_attachments.filtered(
                        lambda att: ir_att._matches_file_type_filter(att, record)
                    )
                record.model_attachment_count = len(all_attachments)
            else:
                record.model_attachment_count = 0

    @api.depends('model_name', 'sync_count', 'file_type', 'storage_mode', 'one_drive_id', 'google_folder_id')
    def _compute_sync_statistics(self):
        """Compute detailed sync statistics for the dashboard.
        Fetches all attachments in a SINGLE call and filters in Python
        to avoid 8 separate SQL queries per record on every page load.
        """
        for record in self:
            if record.model_name:
                # Single fetch — all subsequent stats filter this in-memory recordset
                all_attachments = record._get_target_attachments()
                # Exclude ghosts: purely web links with no ID, or any attachment missing physical data
                # Also exclude url-type records with no binary data and no one_drive_file_id — these
                # are broken ghost links, not real pending syncs.
                all_attachments = all_attachments.filtered(
                    lambda a: a.one_drive_file_id or (a.datas or a.raw) and a.type != 'url'
                )

                # Apply the specific file-type filter (so XMLs don't get counted when configured for PDFs)
                if record.file_type != 'all':
                    ir_att = self.env['ir.attachment']
                    all_attachments = all_attachments.filtered(
                        lambda att: ir_att._matches_file_type_filter(att, record)
                    )

                synced = all_attachments.filtered(lambda a: a.one_drive_file_id)
                unsynced = all_attachments - synced
                dual = synced.filtered(lambda a: a.type != 'url')
                drive_only = synced.filtered(lambda a: a.type == 'url')

                record.synced_attachment_count = len(synced)
                record.unsynced_attachment_count = len(unsynced)
                record.dual_attachment_count = len(dual)
                record.drive_only_attachment_count = len(drive_only)

                total = len(all_attachments)
                record.sync_percentage = (len(synced) / total * 100) if total > 0 else 0
            else:
                record.synced_attachment_count = 0
                record.unsynced_attachment_count = 0
                record.dual_attachment_count = 0
                record.drive_only_attachment_count = 0
                record.sync_percentage = 0

    def _get_action_domain(self, base_extra=None):
        """Build a correct Odoo domain for ir.attachment covering both direct
        attachments and mail.message chatter copies for this model.
        """
        from odoo.osv import expression
        self.ensure_one()
        m_name = self.model_name.strip() if self.model_name else ''
        messages = self.env['mail.message'].sudo().search([('model', '=', m_name)])

        # Core: direct model attachments OR chatter copies
        core_domain = [
            '|',
            ('res_model', '=', m_name),
            '&', ('res_model', '=', 'mail.message'), ('res_id', 'in', messages.ids)
        ]

        final_domain = core_domain

        # Inject File Type filtering into the visual layout (so XML files don't show when PDF is selected)
        type_domain = []
        if self.file_type == 'pdf':
            type_domain = ['|', ('mimetype', 'ilike', 'pdf'), ('name', 'ilike', '.pdf')]
        elif self.file_type == 'doc':
            type_domain = ['|', '|', ('mimetype', 'ilike', 'word'), ('mimetype', 'ilike', 'document'), '|', ('name', 'ilike', '.doc'), ('name', 'ilike', '.docx')]
        elif self.file_type == 'xls':
            type_domain = ['|', '|', ('mimetype', 'ilike', 'excel'), ('mimetype', 'ilike', 'spreadsheet'), '|', ('name', 'ilike', '.xls'), ('name', 'ilike', '.xlsx')]
        elif self.file_type == 'ppt':
            type_domain = ['|', '|', ('mimetype', 'ilike', 'powerpoint'), ('mimetype', 'ilike', 'presentation'), '|', ('name', 'ilike', '.ppt'), ('name', 'ilike', '.pptx')]
        elif self.file_type == 'img':
            type_domain = ['|', ('mimetype', 'ilike', 'image/'), '|', ('name', 'ilike', '.jpg'), '|', ('name', 'ilike', '.jpeg'), '|', ('name', 'ilike', '.png'), '|', ('name', 'ilike', '.gif'), ('name', 'ilike', '.webp')]
            
        if type_domain:
            final_domain = expression.AND([final_domain, type_domain])
        if base_extra:
            final_domain = expression.AND([final_domain, base_extra])

        return final_domain

    def action_view_synced_files(self):
        """Open list of synced attachments for this model."""
        self.ensure_one()
        return {
            'name': f'Synced Files — {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'view_mode': 'tree,form',
            'domain': self._get_action_domain([('one_drive_file_id', '!=', False)]),
            'context': {'create': False},
            'target': 'current',
        }

    def action_view_unsynced_files(self):
        """Open list of unsynced attachments for this model."""
        self.ensure_one()
        return {
            'name': f'Not Synced Files — {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'view_mode': 'tree,form',
            'domain': self._get_action_domain([('one_drive_file_id', '=', False), ('type', '!=', 'url')]),
            'context': {'create': False},
            'target': 'current',
        }

    def action_view_dual_files(self):
        """Open list of dual sync attachments for this model."""
        self.ensure_one()
        return {
            'name': f'Dual Sync Files (Odoo + Drive) — {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'view_mode': 'tree,form',
            'domain': self._get_action_domain([('one_drive_file_id', '!=', False), ('type', '!=', 'url')]),
            'context': {'create': False},
            'target': 'current',
        }

    def action_view_drive_only_files(self):
        """Open list of drive-only attachments for this model."""
        self.ensure_one()
        return {
            'name': f'Drive-Only Files (Cloud Links) — {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'view_mode': 'tree,form',
            'domain': self._get_action_domain([('one_drive_file_id', '!=', False), ('type', '=', 'url')]),
            'context': {'create': False},
            'target': 'current',
        }

    def action_view_all_files(self):
        """Open list of all attachments for this model."""
        self.ensure_one()
        return {
            'name': f'All Files — {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'view_mode': 'tree,form',
            'domain': self._get_action_domain(),
            'context': {'create': False},
            'target': 'current',
        }

    def _create_attachment_categories(self):
        """Create folder structure for attachment categorization in Dropbox."""
        self.ensure_one()
        
        if not self.state == 'active':
            return
        
        # Define attachment categories
        categories = [
            ('🧾 Customer Uploaded Files', 'Customer files uploaded via chatter and attachments'),
            ('📑 Requirement / Project Files', 'Sales and pre-sales team requirements and specs'),
            ('📧 Email Attachments', 'Automatically created from incoming emails'),
        ]
        
        # In a real implementation, this would create subfolders in Dropbox
        # For now, we log the structure that would be created
        folder_structure = f"""
        📂 {self.google_folder_id.name}
        ├── 🧾 Customer Uploaded Files
        ├── 📑 Requirement / Project Files
        └── 📧 Email Attachments
        """
        
        return folder_structure

    def action_refresh_dashboard(self):
        """Manual refresh of the dashboard statistics."""
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_manual_drive_sync(self):
        """Manually sync all unsynced attachments for this model and show notification."""
        self.ensure_one()

        # 1. Validate that required configuration fields are set
        if not self.storage_mode:
            raise UserError(
                "⚠️ Please select a Storage Mode (Drive only, Dual, or Odoo only) "
                "before running the sync."
            )

        if not self.one_drive_id:
            raise UserError(
                "⚠️ No Dropbox selected. Please choose a Dropbox account "
                "in Step 2 before syncing."
            )

        if not self.google_folder_id:
            raise UserError(
                "⚠️ No destination folder selected. Please choose a folder in Step 3 "
                "before syncing."
            )

        # 2. Block sync when storage mode is 'Odoo Only'
        if self.storage_mode == 'odoo':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '🗄️ Sync Skipped — Odoo Only Mode',
                    'message': (
                        'Storage Mode is set to "Odoo Only". '
                        'No files will be uploaded to Dropbox. '
                        'Change the Storage Mode to "Drive only" or "Dual" to enable sync.'
                    ),
                    'type': 'warning',
                    'sticky': True,
                }
            }

        # 3. Get all unsynced attachments for this model (includes chatter copies)
        attachments = self._get_target_attachments([('one_drive_file_id', '=', False)])
        # Absolute fail-safe: ignore ghost copies that have no physical data
        attachments = attachments.filtered(lambda a: a.datas or a.raw)

        # Apply the SAME file-type filter that auto-sync uses (_matches_file_type_filter).
        if self.file_type != 'all':
            ir_att = self.env['ir.attachment']
            attachments = attachments.filtered(
                lambda att: ir_att._matches_file_type_filter(att, self)
            )

        if not attachments:
            model_label = self.module_config_id.model_label or self.model_name
            mode_label = dict(self._fields['storage_mode'].selection).get(
                self.storage_mode, self.storage_mode
            )
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '✅ Nothing to Sync',
                    'message': (
                        f'All {model_label} files are already synced to Dropbox.\n'
                        f'Storage Mode: {mode_label} | Drive: {self.one_drive_id.name}'
                    ),
                    'type': 'info',
                    'sticky': False,
                }
            }

        synced_count = 0
        failed_count = 0
        for attachment in attachments:
            # Use a savepoint per file: a failure on one file
            # rolls back ONLY that file's changes, not all previously synced files.
            try:
                with self.env.cr.savepoint():
                    attachment.with_context(sync_type='manual')._auto_sync_to_drive(self)
                    synced_count += 1
            except Exception:
                failed_count += 1
                continue

        # 4. Result notification
        model_label = self.module_config_id.model_label or self.model_name
        mode_label = dict(self._fields['storage_mode'].selection).get(
            self.storage_mode, self.storage_mode
        )
        if synced_count > 0:
            message = (
                f"✅ Synchronization complete!\n"
                f"{synced_count} {model_label} file(s) synced to Dropbox.\n"
                f"Storage Mode: {mode_label} | Drive: {self.one_drive_id.name}"
            )
            if failed_count:
                message += f"\n⚠️ {failed_count} file(s) failed — check Activity Logs."
            notif_type = 'success'
        else:
            message = f"❌ All {failed_count} file(s) failed to sync. Please check the Activity Logs."
            notif_type = 'warning'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f'{model_label} Sync Result',
                'message': message,
                'type': notif_type,
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            }
        }

    def action_setup_auto_sync(self):
        """Setup auto-sync: push all unsynced historical attachments to Dropbox.
        Uses _get_target_attachments() to correctly include chatter (mail.message) copies
        and uses per-file savepoints so a single failure doesn't roll back all others.
        """
        self.ensure_one()

        if self.storage_mode == 'odoo':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Auto-Sync Skipped',
                    'message': 'Storage Mode is set to "Odoo Only". Change the Storage Mode to Drive or Dual to sync.',
                    'type': 'info',
                    'sticky': False,
                }
            }

        # Get all unsynced attachments (includes direct + chatter mail.message copies)
        attachments = self._get_target_attachments([('one_drive_file_id', '=', False)])
        # Absolute fail-safe: ignore ghost copies that have no physical data
        attachments = attachments.filtered(lambda a: a.datas or a.raw)

        # Apply the SAME file-type filter as auto-sync for consistency
        if self.file_type != 'all':
            ir_att = self.env['ir.attachment']
            attachments = attachments.filtered(
                lambda att: ir_att._matches_file_type_filter(att, self)
            )

        synced_count = 0
        failed_count = 0
        if attachments:
            for attachment in attachments:
                try:
                    with self.env.cr.savepoint():
                        attachment.with_context(sync_type='auto_setup')._auto_sync_to_drive(self)
                        synced_count += 1
                except Exception:
                    failed_count += 1
                    continue

        folder_structure = self._create_attachment_categories()
        model_label = self.module_config_id.model_label or self.model_name
        message = f'Auto-sync setup complete. {synced_count} historical file(s) moved to Dropbox for {model_label}.'
        if failed_count:
            message += f' ({failed_count} file(s) failed — check sync logs.)'
        if self.state == 'active' and folder_structure:
            message += f'\n\nAttachment Structure:\n{folder_structure}'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Auto-Sync Configured',
                'message': message,
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            }
        }

    def action_apply_sync(self):
        """Push all unsynced historical attachments to Dropbox.
        Always triggers a batch sync regardless of the auto_sync_mode toggle.
        (Auto-sync for NEW files is handled automatically by ir.attachment.create/write hooks.)
        """
        self.ensure_one()
        return self.action_manual_drive_sync()

    def action_pause_config(self):
        """Pause this sync configuration: disables auto-sync triggers and sets state to paused."""
        self.ensure_one()
        self.write({'state': 'paused', 'auto_sync_mode': False})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Configuration Paused',
                'message': 'Auto-sync has been paused. New attachments will no longer be synced automatically.',
                'type': 'warning',
                'sticky': False,
            }
        }

    def action_activate_config(self):
        """Activate this sync configuration."""
        self.ensure_one()
        self.write({'state': 'active'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Configuration Activated',
                'message': 'This configuration is now active. Enable Auto Sync Mode to sync new files automatically.',
                'type': 'success',
                'sticky': False,
            }
        }
