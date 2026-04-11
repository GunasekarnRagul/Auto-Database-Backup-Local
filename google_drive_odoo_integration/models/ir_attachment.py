# -*- coding: utf-8 -*-
from odoo import models, fields, api

class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    google_file_id = fields.Char('Google File ID', index=True)

    @api.model_create_multi
    def create(self, vals_list):
        attachments = super(IrAttachment, self).create(vals_list)
        # Automatic upload removed - uploads now only happen during sync process
        # This prevents web.assets and other system files from being uploaded
        return attachments
