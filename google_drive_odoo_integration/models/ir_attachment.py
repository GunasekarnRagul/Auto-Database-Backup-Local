# -*- coding: utf-8 -*-
from odoo import models, fields, api

class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    google_file_id = fields.Char('Google File ID', index=True)

    @api.model_create_multi
    def create(self, vals_list):
        attachments = super(IrAttachment, self).create(vals_list)
        # Skip sync if already coming from Google Drive or if manually disabled via context
        if self.env.context.get('skip_gdrive_sync'):
            return attachments
            
        for attachment in attachments:
            if attachment.res_model != 'google.drive.file':
                continue
            if not attachment.google_file_id:
                self.env['google.drive.sync'].sudo().upload_file(attachment)
        return attachments
