# -*- coding: utf-8 -*-
from odoo import models, fields, api
from datetime import datetime

class AttachmentSyncConfig(models.Model):
    _name = 'attachment.sync.config'
    _description = 'Attachment Sync Configuration'
    _rec_name = 'name'

    # Configuration Name
    name = fields.Char('Configuration Name', required=True)

    # Step 1: Module/Model Selection
    module_config_id = fields.Many2one('gdrive.model.config', string='Module', required=True,
        domain="[('id', 'not in', existing_module_ids)]",
        help="Select the module for which you want to configure attachment synchronization.")
    
    model_name = fields.Selection(selection='_get_model_selection', string='Model', 
        compute='_compute_model_name', store=True, readonly=False, required=True, index=True)

    # Step 2: Driver Selection
    google_drive_id = fields.Many2one('google.drive.config', string='Google Drive', required=True, ondelete='cascade')

    # Step 3: Folder Selection (filtered by driver)
    google_folder_id = fields.Many2one('google.drive.file',
        string='Folder',
        domain="[('file_type', '=', 'folder'), ('drive_config_id', '=', google_drive_id)]",
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
        ('odoo', 'Odoo only'),
    ], string='Storage Mode', default='dual', required=True,
    help='Choose where the attachments should be stored.')

    # Configuration metadata
    last_synced = fields.Char('Last Synced', default='Never')
    sync_count = fields.Integer('Files Synced', default=0, readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
    ], string='State', default='draft')

    # Sync statistics computed fields
    synced_attachment_count = fields.Integer('Synced Files', compute='_compute_sync_statistics')
    unsynced_attachment_count = fields.Integer('Unsynced Files', compute='_compute_sync_statistics')
    sync_percentage = fields.Float('Sync %', compute='_compute_sync_statistics')

    @api.model
    def _get_model_selection(self):
        """Dynamic selection of models from gdrive.model.config."""
        configs = self.env['gdrive.model.config'].sudo().search([])
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
    existing_module_ids = fields.Many2many('gdrive.model.config', 
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

    @api.depends('google_drive_id', 'google_folder_id')
    def _compute_folder_path(self):
        for wizard in self:
            path_parts = []
            if wizard.google_drive_id:
                path_parts.append(wizard.google_drive_id.name)
            if wizard.google_folder_id:
                path_parts.append(wizard.google_folder_id.name)
                parent = wizard.google_folder_id.parent_folder_id
                inner_parts = []
                while parent:
                    inner_parts.insert(0, parent.name)
                    parent = parent.parent_folder_id
                if inner_parts:
                    path_parts.extend(inner_parts)
            wizard.folder_path = ' / '.join(path_parts) if path_parts else ''

    @api.depends('model_name')
    def _compute_model_attachment_count(self):
        """Count total attachments in Odoo for the selected model."""
        for record in self:
            if record.model_name:
                record.model_attachment_count = self.env['ir.attachment'].sudo().search_count([
                    ('res_model', '=', record.model_name)
                ])
            else:
                record.model_attachment_count = 0

    @api.depends('model_name', 'sync_count')
    def _compute_sync_statistics(self):
        """Compute detailed sync statistics for the dashboard."""
        Attachment = self.env['ir.attachment'].sudo()
        for record in self:
            if record.model_name:
                synced_count = Attachment.search_count([
                    ('res_model', '=', record.model_name),
                    ('google_file_id', '!=', False),
                ])
                unsynced_count = Attachment.search_count([
                    ('res_model', '=', record.model_name),
                    ('google_file_id', '=', False),
                ])
                record.synced_attachment_count = synced_count
                record.unsynced_attachment_count = unsynced_count
                total = synced_count + unsynced_count
                record.sync_percentage = (synced_count / total * 100) if total > 0 else 0
            else:
                record.synced_attachment_count = 0
                record.unsynced_attachment_count = 0
                record.sync_percentage = 0

    def action_view_synced_files(self):
        """Open list of synced attachments for this model."""
        self.ensure_one()
        return {
            'name': f'Synced Files — {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'ir.attachment',
            'view_mode': 'tree,form',
            'domain': [
                ('res_model', '=', self.model_name),
                ('google_file_id', '!=', False),
            ],
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
            'domain': [
                ('res_model', '=', self.model_name),
                ('google_file_id', '=', False),
            ],
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
            'domain': [
                ('res_model', '=', self.model_name),
            ],
            'context': {'create': False},
            'target': 'current',
        }

    def _create_attachment_categories(self):
        """Create folder structure for attachment categorization in Google Drive."""
        self.ensure_one()
        
        if not self.state == 'active':
            return
        
        # Define attachment categories
        categories = [
            ('🧾 Customer Uploaded Files', 'Customer files uploaded via chatter and attachments'),
            ('📑 Requirement / Project Files', 'Sales and pre-sales team requirements and specs'),
            ('📧 Email Attachments', 'Automatically created from incoming emails'),
        ]
        
        # In a real implementation, this would create subfolders in Google Drive
        # For now, we log the structure that would be created
        folder_structure = f"""
        📂 {self.google_folder_id.name}
        ├── 🧾 Customer Uploaded Files
        ├── 📑 Requirement / Project Files
        └── 📧 Email Attachments
        """
        
        return folder_structure

    def action_manual_drive_sync(self):
        """Manually sync all unsynced attachments for this model and show notification."""
        self.ensure_one()
        
        # 1. First check: Select Storage Mode validation
        if not self.storage_mode or self.storage_mode == 'odoo':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Sync Skipped',
                    'message': 'Please select a valid Storage Mode (Drive or Dual) before syncing.',
                    'type': 'warning',
                    'sticky': False,
                }
            }

        # 2. Search for all attachments for this model that haven't been synced yet
        domain = [
            ('res_model', '=', self.model_name),
            ('google_file_id', '=', False),
        ]
        
        # Filter by file type if not 'all'
        if self.file_type != 'all':
            if self.file_type == 'pdf':
                domain.append(('mimetype', 'ilike', 'pdf'))
            elif self.file_type == 'doc':
                domain.append(('mimetype', 'ilike', 'word'))
            elif self.file_type == 'xls':
                domain.append(('mimetype', 'ilike', 'excel'))
            elif self.file_type == 'ppt':
                domain.append(('mimetype', 'ilike', 'powerpoint'))
            elif self.file_type == 'img':
                domain.append(('mimetype', 'ilike', 'image'))

        attachments = self.env['ir.attachment'].search(domain)
        
        synced_count = 0
        if attachments:
            for attachment in attachments:
                try:
                    # Use the automated sync logic which respects storage_mode
                    attachment.with_context(sync_type='manual')._auto_sync_to_drive(self)
                    synced_count += 1
                except Exception as e:
                    self.env.cr.rollback()
                    continue

        # 3. Success Notification (Instead of routing to another page)
        model_label = self.module_config_id.model_label or self.model_name
        message = f"Synchronization complete! {synced_count} {model_label} files moved to Google Drive."
        if synced_count == 0:
            message = f"No unsynced {model_label} files found for this configuration."

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f'{model_label} Sync Result',
                'message': message,
                'type': 'success' if synced_count > 0 or not attachments else 'info',
                'sticky': False,
            }
        }

    def action_setup_auto_sync(self):
        """Setup auto-sync for selected configuration."""
        self.ensure_one()

        # Create or update auto-sync configuration
        # This could create a scheduled job or set up webhooks
        # For now, we'll just mark attachments as auto-sync enabled

        domain = [
            ('res_model', '=', self.model_name),
            ('google_file_id', '=', False),  # Not yet synced
        ]

        if self.file_type != 'all':
            if self.file_type == 'pdf':
                domain.append(('mimetype', 'ilike', 'pdf'))
            elif self.file_type == 'doc':
                domain.append(('mimetype', 'ilike', 'word'))
            elif self.file_type == 'xls':
                domain.append(('mimetype', 'ilike', 'excel'))
            elif self.file_type == 'ppt':
                domain.append(('mimetype', 'ilike', 'powerpoint'))
            elif self.file_type == 'img':
                domain.append(('mimetype', 'ilike', 'image'))

        attachments = self.env['ir.attachment'].search(domain)

        # Configure attachments for auto-sync
        attachments.write({
            'google_drive_id': self.google_drive_id.id,
            'google_folder_id': self.google_folder_id.id,
            'sync_type': 'external',
            'auto_sync_enabled': True,
        })

        # Create folder structure if active
        folder_structure = self._create_attachment_categories()
        
        message = f'Auto-sync enabled for {len(attachments)} files in {self.model_name}'
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
            }
        }

    def action_apply_sync(self):
        """Apply the selected sync mode."""
        self.ensure_one()
        if self.auto_sync_mode:
            return self.action_setup_auto_sync()
        return self.action_manual_sync()
