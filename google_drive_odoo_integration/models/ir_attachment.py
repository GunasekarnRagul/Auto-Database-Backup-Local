# -*- coding: utf-8 -*-
from odoo import models, fields, api

class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    google_file_id = fields.Char('Google File ID', index=True)
    google_drive_id = fields.Many2one('google.drive.config', string='Drive')
    google_folder_id = fields.Many2one('google.drive.file', string='Folder', domain="[('file_type', '=', 'folder')]" )
    model_name = fields.Selection([
        ('crm.lead', 'CRM - Leads'),
        ('sale.order', 'Sales - Orders'),
        ('account.move', 'Accounting - Invoices'),
        ('purchase.order', 'Purchase - Orders'),
        ('hr.employee', 'HR - Employees'),
        ('project.task', 'Project - Tasks'),
        ('stock.picking', 'Inventory - Pickings'),
        ('documents.document', 'Documents - Managed Files'),
        ('google.drive.file', 'Google Drive - Files'),
    ], string='Model')
    model_display = fields.Char('Model', compute='_compute_model_display', store=True)
    sync_type = fields.Selection([
        ('internal', 'Internal'),
        ('external', 'External'),
    ], string='Sync Type', default='internal')
    google_folder_path = fields.Char('Folder Structure', compute='_compute_google_folder_path', store=True)
    auto_sync_enabled = fields.Boolean('Auto Sync Enabled', default=False)
    is_google_synced = fields.Boolean('Google Synced', compute='_compute_is_google_synced', store=True)
    is_model_enabled_for_sync = fields.Boolean('Model Enabled for Sync', compute='_compute_is_model_enabled_for_sync', store=True)

    @api.depends('google_file_id')
    def _compute_google_drive_id(self):
        """Get the drive associated with this attachment if synced."""
        for attachment in self:
            if attachment.google_file_id:
                # Search for the file in google.drive.file to find its drive
                file_record = self.env['google.drive.file'].sudo().search([
                    ('google_file_id', '=', attachment.google_file_id)
                ], limit=1)
                attachment.google_drive_id = file_record.drive_config_id.id if file_record else False
            else:
                attachment.google_drive_id = False

    @api.depends('model_name', 'res_model')
    def _compute_model_display(self):
        """Get friendly display name for the selected model."""
        model_mapping = {
            'crm.lead': 'CRM - Leads',
            'sale.order': 'Sales - Orders',
            'account.move': 'Accounting - Invoices',
            'purchase.order': 'Purchase - Orders',
            'hr.employee': 'HR - Employees',
            'project.task': 'Project - Tasks',
            'stock.picking': 'Inventory - Pickings',
            'documents.document': 'Documents - Managed Files',
            'google.drive.file': 'Google Drive - Files',
        }
        for attachment in self:
            key = attachment.model_name or attachment.res_model
            attachment.model_display = model_mapping.get(key, key)

    @api.depends('google_drive_id', 'google_folder_id')
    def _compute_google_folder_path(self):
        """Compute a human-readable folder path from the selected drive and folder."""
        for attachment in self:
            path_parts = []
            if attachment.google_drive_id:
                path_parts.append(attachment.google_drive_id.name)
            if attachment.google_folder_id:
                path_parts.append(attachment.google_folder_id.name)
                parent = attachment.google_folder_id.parent_folder_id
                inner_parts = []
                while parent:
                    inner_parts.insert(0, parent.name)
                    parent = parent.parent_folder_id
                if inner_parts:
                    path_parts.extend(inner_parts)
            attachment.google_folder_path = ' / '.join(path_parts) if path_parts else ''

    @api.depends('google_file_id')
    def _compute_is_google_synced(self):
        """Check if attachment is synced with Google Drive."""
        for attachment in self:
            attachment.is_google_synced = bool(attachment.google_file_id)

    @api.depends('res_model')
    def _compute_is_model_enabled_for_sync(self):
        """Check if the attachment's model is enabled for Google Drive sync."""
        # Get all enabled model configurations
        enabled_configs = self.env['gdrive.model.config'].sudo().search([
            ('is_enabled', '=', True)
        ])
        enabled_models = set(config.res_model for config in enabled_configs)
        
        for attachment in self:
            attachment.is_model_enabled_for_sync = attachment.res_model in enabled_models

    def get_model_config(self):
        """Get the Google Drive model configuration for this attachment."""
        self.ensure_one()
        if not self.res_model:
            return False
        return self.env['gdrive.model.config'].sudo().search([
            ('res_model', '=', self.res_model)
        ], limit=1)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('model_name') and not vals.get('res_model'):
                vals['res_model'] = vals['model_name']
        attachments = super(IrAttachment, self).create(vals_list)
        # Automatic upload removed - uploads now only happen during sync process
        # This prevents web.assets and other system files from being uploaded
        return attachments

    def write(self, vals):
        if vals.get('model_name') and not vals.get('res_model'):
            vals['res_model'] = vals['model_name']
        return super(IrAttachment, self).write(vals)

    def action_toggle_auto_sync(self):
        self.ensure_one()
        self.auto_sync_enabled = not self.auto_sync_enabled
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Auto Sync',
                'message': 'Auto sync has been %s for this attachment.' % (
                    'enabled' if self.auto_sync_enabled else 'disabled'
                ),
                'type': 'success',
                'sticky': False,
            }
        }

    def action_sync_to_drive(self):
        """Manually sync an attachment to Google Drive."""
        self.ensure_one()
        
        if not self.google_drive_id:
            raise ValueError("Please select a Google Drive first")
        
        sync = self.env['google.drive.sync'].sudo()
        
        try:
            # Determine parent folder ID
            parent_gdrive_id = False
            if self.google_folder_id and self.google_folder_id.google_file_id:
                parent_gdrive_id = self.google_folder_id.google_file_id
            elif self.google_drive_id.root_ids:
                # Use first active root folder
                root = self.google_drive_id.root_ids.filtered(lambda r: r.active)[:1]
                parent_gdrive_id = root.root_id if root else False
            
            # Upload file to Google Drive
            result = sync.upload_file_to_drive(
                self.name,
                self.raw,
                self.mimetype or 'application/octet-stream',
                self.google_drive_id,
                parent_gdrive_id=parent_gdrive_id
            )
            
            if result:
                self.write({
                    'google_file_id': result['google_file_id']
                })
                # Also create google.drive.file record
                self.env['google.drive.file'].create({
                    'name': self.name,
                    'drive_config_id': self.google_drive_id.id,
                    'parent_folder_id': self.google_folder_id.id if self.google_folder_id else False,
                    'file_type': 'file',
                    'mime_type': self.mimetype or 'application/octet-stream',
                    'file_size': len(self.raw) if self.raw else 0,
                    'google_file_id': result['google_file_id'],
                    'google_url': result['google_url'],
                    'owner_name': self.env.user.name,
                    'sync_state': 'synced',
                    'last_synced': fields.Datetime.now(),
                })
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Success',
                        'message': f'File "{self.name}" uploaded to Google Drive successfully',
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise ValueError("Failed to upload file to Google Drive")
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Error',
                    'message': f'Failed to upload: {str(e)}',
                    'type': 'danger',
                    'sticky': True,
                }
            }
