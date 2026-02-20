# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import logging

_logger = logging.getLogger(__name__)


class WhatsAppMessageQueue(models.Model):
    _name = "whatsapp.message.queue"
    _description = "WhatsApp Message Queue"
    _order = "create_date desc"

    message_id = fields.Many2one('whatsapp.message', string="Parent Message", ondelete='cascade')
    recipient = fields.Char(string="Recipient Number", required=True)
    message_body = fields.Text(string="Message")
    
    state = fields.Selection([
        ('pending', 'Pending'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('failed', 'Failed')
    ], default='pending', string="Status")
    
    error_message = fields.Text(string="Error Message")
    sent_date = fields.Datetime(string="Sent Date")
    attempts = fields.Integer(string="Attempts", default=0)

    def _send_single_message(self):
        """Send a single queued message"""
        self.ensure_one()
        
        # Get config
        config = self.env['whatsapp.config'].search([], limit=1)
        if not config or config.connection_status != 'connected':
            self.write({'state': 'failed', 'error_message': 'WhatsApp not configured'})
            return False

        # WhatsApp Cloud API URL
        url = f"https://graph.facebook.com/{config.api_version or 'v18.0'}/{config.phone_number_id}/messages"
        headers = {
            'Authorization': f'Bearer {config.access_token}',
            'Content-Type': 'application/json'
        }

        # Clean number
        clean_number = ''.join(c for c in self.recipient if c.isdigit() or c == '+')
        wa_number = clean_number.replace('+', '')

        # Build payload dynamically based on parent message type
        # utilize the helper methods in the parent model
        parent = self.message_id
        payload = {}
        
        try:
            if parent.message_type == 'text':
                payload = parent._get_text_payload(wa_number)
            elif parent.message_type == 'media':
                payload = parent._get_media_payload(wa_number)
            else:
                payload = parent._get_text_payload(wa_number) # Fallback
        except Exception as e:
            self.write({
                'state': 'failed',
                'error_message': f"Payload Generation Error: {str(e)}"
            })
            return False

        self.attempts += 1
        self.state = 'sending'

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)

            if response.status_code in (200, 201):
                self.write({
                    'state': 'sent',
                    'sent_date': fields.Datetime.now(),
                    'error_message': False
                })
                return True
            else:
                try:
                    error_data = response.json()
                    msg = error_data.get('error', {}).get('message', response.text)
                except:
                    msg = response.text
                
                self.write({
                    'state': 'failed',
                    'error_message': msg
                })
                return False

        except Exception as e:
            self.write({
                'state': 'failed',
                'error_message': str(e)
            })
            _logger.exception("WhatsApp queue send error")
            return False

    @api.model
    def _cron_process_queue(self):
        """Cron job to process pending messages in queue"""
        # Get pending messages (limit to avoid timeout)
        pending = self.search([
            ('state', '=', 'pending')
        ], limit=50, order='create_date asc')

        sent_count = 0
        failed_count = 0

        for queue_item in pending:
            try:
                if queue_item._send_single_message():
                    sent_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                _logger.exception("Error processing queue item %s", queue_item.id)
                queue_item.write({'stat         scroll-behavior: smooth;e': 'failed', 'error_message': str(e)})
                failed_count += 1

        # Update parent message statistics
        self._update_parent_stats()

        _logger.info("WhatsApp Queue: Processed %s sent, %s failed", sent_count, failed_count)

    def _update_parent_stats(self):
        """Update parent message sent/failed counts"""
        # Find all message_ids that have been processed
        messages = self.search([
            ('state', 'in', ['sent', 'failed']),
            ('message_id', '!=', False)
        ]).mapped('message_id')

        for msg in messages:
            queue_items = self.search([('message_id', '=', msg.id)])
            
            sent = len(queue_items.filtered(lambda q: q.state == 'sent'))
            failed = len(queue_items.filtered(lambda q: q.state == 'failed'))
            pending = len(queue_items.filtered(lambda q: q.state in ['pending', 'sending']))
            
            # Determine state
            if pending > 0:
                state = 'queued'  # Still processing
            elif failed == 0 and sent > 0:
                state = 'sent'
            elif sent == 0 and failed > 0:
                state = 'failed'
            elif sent > 0 and failed > 0:
                state = 'partial'
            else:
                state = 'draft'

            # Build logs
            success_lines = []
            failure_lines = []
            display_lines = []

            for item in queue_items:
                time_str = item.sent_date.strftime('%H:%M:%S') if item.sent_date else fields.Datetime.now().strftime('%H:%M:%S')
                if item.state == 'sent':
                    log_entry = f"""
                        <div class="d-flex align-items-center p-2 mb-2 border-bottom" style="background: #f1f8e9; border-radius: 4px; border-left: 3px solid #28a745;">
                            <div class="me-3 text-success fs-4"><i class="fa fa-check-circle"/></div>
                            <div class="flex-grow-1">
                                <div class="d-flex justify-content-between">
                                    <strong>{item.recipient}</strong>
                                    <small class="text-muted">{time_str}</small>
                                </div>
                                <div class="text-muted small">✅ Message Delivered</div>
                            </div>
                        </div>
                    """
                    success_lines.append(log_entry)
                    display_lines.append(log_entry)
                elif item.state == 'failed':
                    error = item.error_message or 'Failed'
                    fail_entry = f"""
                        <div class="d-flex align-items-center p-2 mb-2 border-bottom" style="background: #fff5f5; border-radius: 4px; border-left: 3px solid #dc3545;">
                            <div class="me-3 text-danger fs-4"><i class="fa fa-times-circle"/></div>
                            <div class="flex-grow-1">
                                <div class="d-flex justify-content-between">
                                    <strong>{item.recipient}</strong>
                                    <small class="text-muted">{time_str}</small>
                                </div>
                                <div class="text-danger small">{error}</div>
                            </div>
                        </div>
                    """
                    failure_lines.append(fail_entry)
                    display_lines.append(fail_entry)

            msg.write({
                'sent_count': sent,
                'failed_count': failed,
                'state': state,
                'log_success': "".join(success_lines),
                'log_failure': "".join(failure_lines),
                'response_log': "".join(display_lines)
            })
