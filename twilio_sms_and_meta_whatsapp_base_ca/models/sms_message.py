# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import format_datetime
import xlsxwriter
import requests
import logging
import io
import base64
import pytz

_logger = logging.getLogger(__name__)


class SmsMessage(models.Model):
    _name = "sms.message"
    _description = "Send SMS Message"
    _rec_name = "recipient_single"
    _order = "create_date desc"

    create_date = fields.Datetime(string="Created On", readonly=True)

    # ---------------------------
    # RECIPIENT FIELDS
    # ---------------------------
    recipient_type = fields.Selection(
        [('single', 'Single Number'),
         ('multi', 'Multiple Numbers'),
         ('contact', 'Contact List')],
        string="Recipient Type", default='single'
    )

    recipient_single = fields.Char(string="Mobile Number", help="e.g. +1234567890")
    recipient_multi = fields.Text(string="Mobile Numbers", help="Enter numbers separated by commas.")
    recipient_contacts = fields.Many2many('res.partner', string="Recipients")

    # ---------------------------
    # MESSAGE CONTENT
    # ---------------------------
    message_body = fields.Text(string="Message")

    # ---------------------------
    # TWILIO ACCOUNT SELECTION
    # ---------------------------
    twilio_config_id = fields.Many2one(
        'twilio.config', string="SMS Account",
        domain="[('connection_status', '=', 'connected')]",
        help="Select the Twilio account to send from"
    )

    # ---------------------------
    # SCHEDULING FIELDS
    # ---------------------------
    schedule_datetime = fields.Datetime(
        string="Schedule Date & Time",
        help="Select when the message should be sent"
    )

    schedule_display = fields.Char(
        string="Scheduled On",
        compute="_compute_schedule_display"
    )

    formatted_create_date = fields.Char(
        string="Formatted Created On",
        compute="_compute_formatted_create_date"
    )

    timezone = fields.Selection(
        [(tz, tz) for tz in pytz.all_timezones],
        string="Time Zone",
        default=lambda self: self.env.user.tz or "UTC",
        help="Choose your local time zone"
    )

    # ---------------------------
    # TRACKING FIELDS
    # ---------------------------
    sent_count = fields.Integer(string="Sent Count", readonly=True, default=0)
    failed_count = fields.Integer(string="Failed Count", readonly=True, default=0)

    state = fields.Selection(
        [('draft', 'Draft'),
         ('scheduled', 'Scheduled'),
         ('queued', 'Processing'),
         ('sent', 'Sent'),
         ('partial', 'Partial'),
         ('failed', 'Failed')],
        string="Status", default='draft', readonly=True, index=True
    )

    # --- READ MORE PREVIEW LOGIC ---
    show_full_message = fields.Boolean(string="Show Full Message", default=False)
    is_long_message = fields.Boolean(compute="_compute_is_long_message")
    message_body_truncated = fields.Text(compute="_compute_message_body_truncated")

    @api.depends('message_body')
    def _compute_is_long_message(self):
        for rec in self:
            rec.is_long_message = len(rec.message_body or "") > 400

    @api.depends('message_body')
    def _compute_message_body_truncated(self):
        for rec in self:
            body = rec.message_body or ""
            rec.message_body_truncated = (body[:397] + "...") if len(body) > 400 else body

    def action_toggle_full_message(self):
        self.ensure_one()
        self.show_full_message = not self.show_full_message
        return True

    detailed_status = fields.Char(
        string="Delivery Report",
        compute="_compute_detailed_status"
    )

    response_log = fields.Html(string="API Response", readonly=True)
    log_success = fields.Html(string="Success Log", readonly=True)
    log_failure = fields.Html(string="Failure Log", readonly=True)

    mobile_number_display = fields.Char(
        string="Mobile Number",
        compute="_compute_mobile_number_display"
    )

    recipient_count = fields.Integer(string="Recipient Count", compute="_compute_recipient_count")

    # ---------------------------
    # COMPUTE METHODS
    # ---------------------------
    @api.depends('recipient_type', 'recipient_single', 'recipient_contacts', 'recipient_multi')
    def _compute_recipient_count(self):
        for rec in self:
            count = 0
            if rec.recipient_type == 'single' and rec.recipient_single:
                count = 1
            elif rec.recipient_type == 'contact' and rec.recipient_contacts:
                count = len(rec.recipient_contacts)
            elif rec.recipient_type == 'multi' and rec.recipient_multi:
                raw = rec.recipient_multi or ""
                numbers = [x.strip() for x in raw.replace('\n', ',').split(',') if x.strip()]
                count = len(list(set(numbers)))
            rec.recipient_count = count

    @api.depends('state', 'sent_count', 'failed_count')
    def _compute_detailed_status(self):
        Queue = self.env['sms.message.queue']
        for rec in self:
            if rec.state == 'draft':
                rec.detailed_status = "Draft"
            elif rec.state == 'scheduled':
                rec.detailed_status = "Scheduled"
            elif rec.state == 'queued':
                queue_items = Queue.search([('message_id', '=', rec.id)])
                total = len(queue_items)
                sent = len(queue_items.filtered(lambda q: q.state == 'sent'))
                failed = len(queue_items.filtered(lambda q: q.state == 'failed'))
                pending = total - sent - failed
                if total > 0:
                    rec.detailed_status = f"📤 {sent}/{total} sent, {failed} failed, {pending} pending"
                else:
                    rec.detailed_status = "Processing..."
            elif rec.state == 'sent':
                rec.detailed_status = f"✅ All {rec.sent_count} Sent"
            elif rec.state == 'failed':
                rec.detailed_status = f"❌ {rec.failed_count} Failed"
            elif rec.state == 'partial':
                rec.detailed_status = f"⚠️ {rec.sent_count} sent / {rec.failed_count} failed"
            else:
                rec.detailed_status = "-"

    @api.depends('recipient_type', 'recipient_single', 'recipient_multi', 'recipient_contacts')
    def _compute_mobile_number_display(self):
        for rec in self:
            if rec.recipient_type == 'single':
                rec.mobile_number_display = rec.recipient_single
            elif rec.recipient_type == 'contact':
                count = len(rec.recipient_contacts)
                rec.mobile_number_display = f"{count} Contacts selected"
            else:
                full_text = rec.recipient_multi or ""
                if len(full_text) > 25:
                    rec.mobile_number_display = full_text[:18] + "..."
                else:
                    rec.mobile_number_display = full_text

    @api.depends('create_date')
    def _compute_formatted_create_date(self):
        for rec in self:
            if rec.create_date:
                dt = rec.create_date
                rec.formatted_create_date = dt.strftime('%m/%d/%Y  |  %H:%M:%S')
            else:
                rec.formatted_create_date = ""

    @api.depends('schedule_datetime', 'timezone')
    def _compute_schedule_display(self):
        for rec in self:
            if rec.schedule_datetime:
                rec.schedule_display = format_datetime(
                    self.env,
                    rec.schedule_datetime,
                    tz=rec.timezone or self.env.user.tz or 'UTC'
                )
            else:
                rec.schedule_display = "-"

    # ---------------------------
    # VALIDATION
    # ---------------------------
    @api.constrains('schedule_datetime')
    def _check_schedule(self):
        for rec in self:
            if rec.schedule_datetime and rec.schedule_datetime < fields.Datetime.now():
                raise UserError("Scheduled time cannot be in the past.")

    # ---------------------------
    # EXCEL EXPORT LOGIC
    # ---------------------------
    def _generate_excel(self, log_content, header_number, header_response, filename_prefix):
        """Helper function to generate Excel from text log"""
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Report')

        header_format = workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#D3D3D3', 'border': 1})
        cell_format = workbook.add_format({'align': 'left', 'border': 1})

        worksheet.write(0, 0, header_number, header_format)
        worksheet.write(0, 1, header_response, header_format)
        worksheet.set_column(0, 0, 25)
        worksheet.set_column(1, 1, 60)

        if log_content:
            lines = log_content.split('\n')
            row = 1
            for line in lines:
                if ':' in line:
                    parts = line.split(':', 1)
                    number_val = parts[0].strip()
                    response_val = parts[1].strip()
                else:
                    number_val = "-"
                    response_val = line

                worksheet.write(row, 0, number_val, cell_format)
                worksheet.write(row, 1, response_val, cell_format)
                row += 1

        workbook.close()
        output.seek(0)

        file_data = base64.b64encode(output.read())
        attachment = self.env['ir.attachment'].create({
            'name': f"{filename_prefix}_{self.id}.xlsx",
            'type': 'binary',
            'datas': file_data,
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def action_export_success_excel(self):
        self.ensure_one()
        return self._generate_excel(
            self.log_success,
            "Delivery Success Number",
            "Api Response",
            "SMS_Success_Report"
        )

    def action_export_failure_excel(self):
        self.ensure_one()
        return self._generate_excel(
            self.log_failure,
            "Delivery Failures Numbers",
            "Api Response",
            "SMS_Failure_Report"
        )

    # ---------------------------
    # SENDING LOGIC (Multi-Provider)
    # ---------------------------
    def _send_sms_via_twilio(self):
        """Send SMS message via the configured provider (Twilio)."""
        self.ensure_one()

        # 1. Config Check
        config = self.twilio_config_id or self.env['twilio.config'].search([('connection_status', '=', 'connected')], limit=1)
        if not config:
            raise UserError("Please configure and connect an SMS account in Settings first.")

        # 2. Prepare Recipient List
        if self.recipient_type == 'single':
            numbers_to_send = [self.recipient_single.strip()] if self.recipient_single else []
        elif self.recipient_type == 'contact':
            numbers_to_send = []
            for contact in self.recipient_contacts:
                if contact.mobile:
                    numbers_to_send.append(contact.mobile.strip())
                elif contact.phone:
                    numbers_to_send.append(contact.phone.strip())

            if not numbers_to_send:
                raise UserError("Selected contacts do not have valid mobile numbers.")
        else:
            raw_multi = self.recipient_multi or ""
            numbers_to_send = [x.strip() for x in raw_multi.replace('\n', ',').split(',') if x.strip()]

        # Remove duplicates
        numbers_to_send = list(set(numbers_to_send))

        # 3. Initialize tracking
        sent_numbers = []
        failed_numbers = []
        sent_log_lines = []
        failed_log_lines = []
        display_log_lines = []

        # 4. Loop to SEND
        for number in numbers_to_send:
            clean_number = number.strip()

            try:
                success, resp_info = self._do_send_sms(config, clean_number, self.message_body)

                if success:
                    sent_numbers.append(number)
                    log_entry = f"""
                        <div class="d-flex align-items-center p-2 mb-2 border-bottom" style="background: #f1f8e9; border-radius: 4px; border-left: 3px solid #28a745;">
                            <div class="me-3 text-success fs-4"><i class="fa fa-check-circle"/></div>
                            <div class="flex-grow-1">
                                <div class="d-flex justify-content-between">
                                    <strong>{number}</strong>
                                    <small class="text-muted">{fields.Datetime.now().strftime('%H:%M:%S')}</small>
                                </div>
                                <div class="text-muted small">{resp_info}</div>
                            </div>
                        </div>
                    """
                    sent_log_lines.append(log_entry)
                    display_log_lines.append(log_entry)
                else:
                    failed_numbers.append(number)
                    fail_entry = f"""
                        <div class="d-flex align-items-center p-2 mb-2 border-bottom" style="background: #fff5f5; border-radius: 4px; border-left: 3px solid #dc3545;">
                            <div class="me-3 text-danger fs-4"><i class="fa fa-times-circle"/></div>
                            <div class="flex-grow-1">
                                <div class="d-flex justify-content-between">
                                    <strong>{number}</strong>
                                    <small class="text-muted">{fields.Datetime.now().strftime('%H:%M:%S')}</small>
                                </div>
                                <div class="text-danger small">{resp_info}</div>
                            </div>
                        </div>
                    """
                    failed_log_lines.append(fail_entry)
                    display_log_lines.append(fail_entry)

            except Exception as e:
                failed_numbers.append(number)
                err_msg = str(e)
                exc_entry = f"""
                    <div class="d-flex align-items-center p-2 mb-2 border-bottom" style="background: #fff5f5; border-radius: 4px; border-left: 3px solid #dc3545;">
                        <div class="me-3 text-danger fs-4"><i class="fa fa-exclamation-triangle"/></div>
                        <div class="flex-grow-1">
                            <div class="d-flex justify-content-between">
                                <strong>{number}</strong>
                                <small class="text-muted">{fields.Datetime.now().strftime('%H:%M:%S')}</small>
                            </div>
                            <div class="text-danger small">{err_msg}</div>
                        </div>
                    </div>
                """
                failed_log_lines.append(exc_entry)
                display_log_lines.append(exc_entry)
                _logger.exception("SMS send error")

        # 5. SAVE DATA
        self.log_success = "".join(sent_log_lines)
        self.log_failure = "".join(failed_log_lines)
        self.response_log = "".join(display_log_lines)

        if sent_log_lines:
            _logger.info("SMS SUCCESS LOG:\n%s", "\n".join(sent_log_lines))
        if failed_log_lines:
            _logger.error("SMS FAILURE LOG:\n%s", "\n".join(failed_log_lines))

        # 6. Update Counts
        self.sent_count = len(list(set(sent_numbers)))
        self.failed_count = len(list(set(failed_numbers)))

        # 7. Final State Update
        if self.sent_count > 0 and self.failed_count == 0:
            self.state = 'sent'
            return True
        elif self.sent_count > 0 and self.failed_count > 0:
            self.state = 'partial'
            return True
        else:
            self.state = 'failed'
            return False

    def _do_send_sms(self, config, to_number, body):
        """Dispatch SMS to the correct provider via config. Returns (success: bool, info: str)."""
        return config.send_sms(to_number, body)

    # ---------------------------
    # Public: triggered by button
    # ---------------------------
    def action_send_message(self):
        self.ensure_one()

        # Validation
        if not self.message_body:
            raise UserError(_("Please enter a message before sending."))

        if self.recipient_type == 'single' and not self.recipient_single:
            pass

        if self.recipient_type == 'multi' and not self.recipient_multi:
            pass

        if self.recipient_type == 'contact' and not self.recipient_contacts:
            raise UserError(_("Please select at least one contact."))

        # Bulk messages can only be sent from DRAFT
        if self.recipient_type != 'single' and self.state not in ('draft', 'scheduled'):
            raise UserError(_("Multiple recipient messages can only be sent once. This message is already %s.") % self.state)

        # Scheduling
        if self.schedule_datetime and self.schedule_datetime > fields.Datetime.now():
            self.state = 'scheduled'
            return True

        # Prepare recipient list
        if self.recipient_type == 'single':
            numbers_to_send = [self.recipient_single.strip()] if self.recipient_single else []
        elif self.recipient_type == 'contact':
            numbers_to_send = []
            for contact in self.recipient_contacts:
                if contact.mobile:
                    numbers_to_send.append(contact.mobile.strip())
                elif contact.phone:
                    numbers_to_send.append(contact.phone.strip())

            numbers_to_send = list(set(numbers_to_send))

            if not numbers_to_send:
                raise UserError("Selected contacts do not have valid mobile numbers.")
        else:
            numbers_to_send = [x.strip() for x in self.recipient_multi.split(',') if x.strip()]

        # For multiple recipients (>3), use background queue
        if len(numbers_to_send) > 3:
            return self._queue_messages(numbers_to_send)
        else:
            return self._send_direct(numbers_to_send)

    def _queue_messages(self, numbers):
        """Queue messages for background processing"""
        self.ensure_one()

        Queue = self.env['sms.message.queue']

        for number in numbers:
            Queue.create({
                'message_id': self.id,
                'recipient': number,
                'message_body': self.message_body,
                'state': 'pending'
            })

        log_message = f"""
            <div class="p-3 mb-3 border-bottom shadow-sm" style="background: #f8f9fa; border-radius: 8px; border-left: 5px solid #d92128;">
                <div class="d-flex align-items-center mb-2">
                    <div class="me-3 fs-3" style="color: #d92128;"><i class="fa fa-refresh fa-spin"/></div>
                    <div>
                        <h5 class="m-0 fw-bold" style="color: #d92128;">SENDING IN PROGRESS...</h5>
                        <small class="text-muted">Messages are being sent automatically</small>
                    </div>
                </div>
                <hr class="my-2" style="opacity: 0.1;"/>
                <div class="row text-center bg-white rounded p-2 m-0 border">
                    <div class="col-6 border-end">
                        <small class="text-muted d-block text-uppercase fw-bold" style="font-size: 10px;">Total Messages</small>
                        <span class="fs-4 fw-bold text-dark">{len(numbers)}</span>
                    </div>
                    <div class="col-6">
                        <small class="text-muted d-block text-uppercase fw-bold" style="font-size: 10px;">Current Status</small>
                        <span class="badge" style="background: #d92128; color: white;">Queued</span>
                    </div>
                </div>
                <div class="mt-2 text-center small italic">
                    <i class="fa fa-info-circle me-1"/> <i>Refresh this page to see the latest progress.</i>
                </div>
            </div>
        """
        self.write({
            'state': 'queued',
            'sent_count': 0,
            'failed_count': 0,
            'response_log': log_message
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '✅ Background Processing!',
                'message': f'{len(numbers)} SMS messages queued for background sending. You can continue using Odoo.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'}
            }
        }

    def _send_direct(self, numbers):
        """Send messages directly (for small batches)"""
        self.ensure_one()

        try:
            self._send_sms_via_twilio()

            if self.state == 'sent':
                msg_title = 'Success'
                msg_body = '✔ All SMS Messages Sent Successfully!'
                msg_type = 'success'
            elif self.state == 'partial':
                msg_title = 'Partial Success'
                msg_body = f'⚠ Sent: {self.sent_count} / Failed: {self.failed_count}. Check logs.'
                msg_type = 'warning'
            else:
                msg_title = 'Failed'
                msg_body = '❌ All messages failed. Check Delivery Logs.'
                msg_type = 'danger'

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': msg_title,
                    'message': msg_body,
                    'type': msg_type,
                    'sticky': True if self.state != 'sent' else False,
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'}
                }
            }

        except UserError:
            raise
        except Exception as e:
            _logger.exception("Unexpected error in SMS action_send_message")
            self.response_log = str(e)
            self.state = 'failed'
            raise UserError(_("Unexpected error while sending SMS: %s") % e)

    def action_clear_log(self):
        self.ensure_one()
        self.response_log = ""
        self.log_success = ""
        self.log_failure = ""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Success',
                'message': 'Log history has been cleared.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'}
            }
        }

    def action_export_excel(self):
        self.ensure_one()
        return self.action_export_success_excel()

    @api.model
    def _cron_send_scheduled_sms(self):
        """Cron job to send scheduled SMS messages"""
        now = fields.Datetime.now()
        scheduled = self.search([
            ('state', '=', 'scheduled'),
            ('schedule_datetime', '<=', now)
        ])
        for rec in scheduled:
            try:
                rec.action_send_message()
            except Exception:
                _logger.exception("Failed to send scheduled SMS for id %s", rec.id)
                rec.state = 'failed'
