# -*- coding: utf-8 -*-
from odoo import models, fields


class MailMessage(models.Model):
    _inherit = 'mail.message'

    # Expose cloud_file_id on chatter attachments through mail.message
    # (This is needed to display the cloud icon in chatter next to synced attachments)
    cloud_attachment_ids = fields.Many2many(
        'ir.attachment',
        'mail_message_res_ir_attachment_rel', 'message_id', 'attachment_id',
        string='Cloud Attachments',
        compute='_compute_cloud_attachments',
    )

    def _compute_cloud_attachments(self):
        for msg in self:
            msg.cloud_attachment_ids = msg.attachment_ids.filtered(lambda a: a.cloud_file_id)
