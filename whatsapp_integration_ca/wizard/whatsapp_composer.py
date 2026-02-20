# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import json

class WhatsAppComposer(models.TransientModel):
    _name = 'whatsapp.composer'
    _description = 'WhatsApp Composer Wizard'

    recipient_single = fields.Char(string="Mobile Number")
    
    # Message Content
    message_type = fields.Selection([
        ('text', 'Free Text'),
    ], string="Message Type", default='text', required=True)
    
    
    message_body = fields.Text(string="Message Body", store=True, readonly=False)

    # _compute_message_body REMOVED

    def action_send_message(self):
        self.ensure_one()
        
        if not self.recipient_single:
            raise UserError(_("Please specify a mobile number."))

             
        # Create History Record
        history = self.env['whatsapp.message'].create({
            'recipient_type': 'single',
            'recipient_single': self.recipient_single,
            'message_body': self.message_body,
            'message_type': self.message_type,
            'state': 'draft', # Will change to sent/failed
        })
        
        # Trigger Send
        history.action_send_message()
        
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': 'Message sent successfully!',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'} 
            }
        }
