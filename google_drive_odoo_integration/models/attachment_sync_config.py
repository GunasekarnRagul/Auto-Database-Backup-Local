# -*- coding: utf-8 -*-
from odoo import models, fields, api

class AttachmentSyncConfig(models.Model):
    _name = 'attachment.sync.config'
    _description = 'Attachment Sync Configuration'
    _rec_name = 'name'

    # Configuration Name
    name = fields.Char('Configuration Name', required=True)

    # Step 1: Model Selection
    model_name = fields.Selection([
        ('crm.lead', 'CRM - Leads'),
        ('sale.order', 'Sales - Orders'),
        ('account.move', 'Accounting - Invoices'),
        ('purchase.order', 'Purchase - Orders'),
        ('hr.employee', 'HR - Employees'),
        ('project.task', 'Project - Tasks'),
        ('stock.picking', 'Inventory - Transfers'),
        ('documents.document', 'Documents - Managed Files'),
        ('helpdesk.ticket', 'Helpdesk - Tickets'),
    ], string='Model', required=True)

    # Step 2: Driver Selection
    google_drive_id = fields.Many2one('google.drive.config', string='Google Drive', required=True)

    # Step 3: Folder Selection (filtered by driver)
    google_folder_id = fields.Many2one('google.drive.file',
        string='Folder',
        domain="[('file_type', '=', 'folder'), ('drive_config_id', '=', google_drive_id)]",
        required=True)

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

    # Configuration metadata
    last_synced = fields.Datetime('Last Synced')
    sync_count = fields.Integer('Files Synced', default=0, readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
    ], string='State', default='draft')

    # Computed field for folder path display
    folder_path = fields.Char('Folder Path', compute='_compute_folder_path')

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

    def action_manual_sync(self):
        """Perform manual sync for selected configuration."""
        self.ensure_one()

        # Get attachments matching the criteria
        domain = [
            ('res_model', '=', self.model_name),
            ('google_file_id', '=', False),  # Not yet synced
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

        if not attachments:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'No Files Found',
                    'message': f'No unsynced {self.file_type} files found for {self.model_name}',
                    'type': 'warning',
                    'sticky': False,
                }
            }

        # Perform sync for each attachment
        sync_count = 0
        for attachment in attachments:
            try:
                # Set the drive and folder for the attachment
                attachment.write({
                    'google_drive_id': self.google_drive_id.id,
                    'google_folder_id': self.google_folder_id.id,
                    'sync_type': 'manual',
                })

                # Upload to drive
                result = attachment.action_sync_to_drive()
                if result and result.get('type') == 'ir.actions.client':
                    sync_count += 1

            except Exception as e:
                self.env.cr.rollback()
                continue

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sync Complete',
                'message': f'Successfully synced {sync_count} out of {len(attachments)} files',
                'type': 'success',
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

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Auto-Sync Configured',
                'message': f'Auto-sync enabled for {len(attachments)} files in {self.model_name}',
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
