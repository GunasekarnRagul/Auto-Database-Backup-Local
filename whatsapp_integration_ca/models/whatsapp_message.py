# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import format_datetime
from odoo.tools.misc import xlsxwriter
import requests
import logging
import io
import base64
import pytz
import json
import mimetypes
import os

_logger = logging.getLogger(__name__)


class WhatsAppMessage(models.Model):
    _name = "whatsapp.message"
    _description = "Send WhatsApp Message"
    _rec_name = "recipient_single"
    _order = "create_date desc"

    create_date = fields.Datetime(string="Created On", readonly=True)

    # ---------------------------
    # UI FIELDS
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

    message_body = fields.Text(string="Message")

    # ---------------------------
    # SCHEDULING FIELDS
    # ---------------------------
    schedule_datetime = fields.Datetime(
        string="Schedule Date & Time",
        help="Select when the message should be sent"
    )

    # ---------------------------
    # ---------------------------
    # ---------------------------
    # MESSAGE CONTENT
    # ---------------------------
    message_type = fields.Selection([
        ('text', 'Text'),
        ('media', 'Media'),
    ], string="Message Type", default='text', required=True)

    message_body = fields.Text(string="Message")
    
    
    # --- MEDIA FIELDS ---
    media_source = fields.Selection([
        ('local', 'Upload File'),
        ('url', 'Public URL')
    ], string="Media Source", default='local')

    media_type = fields.Selection([
        ('image', 'Image'),
        ('document', 'Document'),
    ], string="Media Type", default='image')
    
    media_file = fields.Binary(string="Media File", attachment=True)
    media_filename = fields.Char(string="Media Filename")
    media_caption = fields.Char(string="Caption")
    
    # Technical field to store WhatsApp Media ID
    wa_media_id = fields.Char(string="WhatsApp Media ID", readonly=True, copy=False)
    
    # DUMMY FIELDS (Temporary for upgrade - remove after successful upgrade)
    location_latitude = fields.Float(string="Lat (Dummy)")
    location_longitude = fields.Float(string="Long (Dummy)")
    location_name = fields.Char(string="Name (Dummy)")
    location_address = fields.Char(string="Addr (Dummy)")

    # --- DOCUMENT PREVIEW METADATA ---
    document_icon_class = fields.Char(compute="_compute_document_metadata", store=False)
    document_icon_color = fields.Char(compute="_compute_document_metadata", store=False) # hex code
    document_ext_label = fields.Char(compute="_compute_document_metadata", store=False) # uppercase ext
    document_short_label = fields.Char(compute="_compute_document_metadata", store=False) # PDF, X, W
    file_size_display = fields.Char(compute="_compute_document_metadata", store=False)

    @api.depends('media_filename', 'media_file')
    def _compute_document_metadata(self):
        for rec in self:
            icon = 'fa-file' # Generic default
            color = '#54656f' # default muted
            ext_label = 'FILE'
            short_label = ''
            size_str = ''
            
            if rec.media_filename:
                ext = os.path.splitext(rec.media_filename)[1].lower()
                ext_label = ext.replace('.', '').upper()
                
                if ext == '.pdf': 
                    icon = 'fa-file-pdf'
                    color = '#f1592a' # PDF Red
                    short_label = 'PDF'
                elif ext in ['.doc', '.docx']: 
                    icon = 'fa-file-word'
                    color = '#2b579a' # Word Blue
                    short_label = 'W'
                elif ext in ['.xls', '.xlsx']: 
                    icon = 'fa-file-excel'
                    color = '#1d6f42' # Excel Green
                    short_label = 'X'
                elif ext in ['.ppt', '.pptx']: 
                    icon = 'fa-file-powerpoint'
                    color = '#d24726' # PPT Orange
                    short_label = 'P'
                elif ext in ['.txt', '.csv']:
                    icon = 'fa-file-text'
                    color = '#00a3da' # Text Blue
                    short_label = 'TXT' if ext == '.txt' else 'CSV'
                elif ext in ['.zip', '.rar']: 
                    icon = 'fa-file-archive'
                elif ext in ['.jpg', '.jpeg', '.png']: 
                    icon = 'fa-file-image'
            
            if rec.media_file:
                try:
                    # media_file is stored as base64 in Odoo Binary fields.
                    # We need to calculate the original size.
                    if isinstance(rec.media_file, bytes):
                        # If it's already bytes, it's likely base64 encoded bytes in this context
                        size_bytes = (len(rec.media_file) * 3) / 4
                        # Adjust for padding if necessary
                        if rec.media_file.endswith(b'=='): size_bytes -= 2
                        elif rec.media_file.endswith(b'='): size_bytes -= 1
                    else:
                        # String base64
                        size_bytes = (len(rec.media_file) * 3) / 4
                        if rec.media_file.endswith('=='): size_bytes -= 2
                        elif rec.media_file.endswith('='): size_bytes -= 1
                        
                    if size_bytes < 1024:
                        size_str = f"{int(size_bytes)} B"
                    elif size_bytes < 1024 * 1024:
                        size_str = f"{size_bytes / 1024:.1f} KB"
                    else:
                        size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
                except Exception:
                    size_str = "Unknown size"
            
            rec.document_icon_class = icon
            rec.document_icon_color = color
            rec.document_ext_label = ext_label
            rec.document_short_label = short_label
            rec.file_size_display = size_str

    @api.onchange('media_file', 'media_filename')
    def _onchange_media_file(self):
        """Auto-detect media type based on extension"""
        if self.media_filename:
            ext = os.path.splitext(self.media_filename)[1].lower()
            if ext in ['.jpg', '.jpeg', '.png']:
                self.media_type = 'image'
            elif ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv', '.zip', '.rar']:
                self.media_type = 'document'



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

    @api.depends('message_body', 'media_caption', 'message_type')
    def _compute_is_long_message(self):
        for rec in self:
            content = rec.media_caption if rec.message_type == 'media' else rec.message_body
            rec.is_long_message = len(content or "") > 400

    @api.depends('message_body')
    def _compute_message_body_truncated(self):
        for rec in self:
            body = rec.message_body or ""
            rec.message_body_truncated = (body[:397] + "...") if len(body) > 400 else body

    media_caption_truncated = fields.Text(compute="_compute_media_caption_truncated")

    @api.depends('media_caption')
    def _compute_media_caption_truncated(self):
        for rec in self:
            caption = rec.media_caption or ""
            rec.media_caption_truncated = (caption[:397] + "...") if len(caption) > 400 else caption

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
        Queue = self.env['whatsapp.message.queue']
        for rec in self:
            if rec.state == 'draft':
                rec.detailed_status = "Draft"
            elif rec.state == 'scheduled':
                rec.detailed_status = "Scheduled"
            elif rec.state == 'queued':
                # Get live counts from queue
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
                # Format: MM/DD/YYYY | HH:MM:SS
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

        # Styles
        header_format = workbook.add_format({'bold': True, 'align': 'center', 'bg_color': '#D3D3D3', 'border': 1})
        cell_format = workbook.add_format({'align': 'left', 'border': 1})

        # Write Headers (Row 1)
        worksheet.write(0, 0, header_number, header_format)
        worksheet.write(0, 1, header_response, header_format)
        worksheet.set_column(0, 0, 25)
        worksheet.set_column(1, 1, 60)

        # Parse Text Log and Write Rows
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

        # Create Attachment
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
            "Success_Report"
        )

    def action_export_failure_excel(self):
        self.ensure_one()
        return self._generate_excel(
            self.log_failure,
            "Delivery Failures Numbers",
            "Api Response",
            "Failure_Report"
        )

    # ---------------------------
    # SENDING LOGIC
    # ---------------------------
    def _send_to_whatsapp(self):
        """Send WhatsApp message via Cloud API."""
        self.ensure_one()

        # 1. Config Check
        config = self.env['whatsapp.config'].search([], limit=1)
        if not config or getattr(config, 'connection_status', None) != 'connected':
            raise UserError("Please configure WhatsApp in settings first (connected).")

        if not getattr(config, 'phone_number_id', None) or not getattr(config, 'access_token', None):
            raise UserError("Missing WhatsApp API credentials in configuration.")

        # 2. Prepare Recipient List
        if self.recipient_type == 'single':
            # if not self.recipient_single:
            #     raise UserError("Please enter a mobile number.")
            # Relaxed validation
            numbers_to_send = [self.recipient_single.strip()] if self.recipient_single else []
        elif self.recipient_type == 'contact':
            numbers_to_send = []
            for contact in self.recipient_contacts:
                if contact.mobile:
                    numbers_to_send.append(contact.mobile.strip())
                elif contact.phone:
                    numbers_to_send.append(contact.phone.strip())
            
            # Removed redundant duplicate removal here, moved below loop 
            pass
            
            if not numbers_to_send:
                raise UserError("Selected contacts do not have valid mobile numbers.")
        else:
            # Support both newlines and commas
            raw_multi = self.recipient_multi or ""
            numbers_to_send = [x.strip() for x in raw_multi.replace('\n', ',').split(',') if x.strip()]

        # Remove duplicates from all recipient types
        numbers_to_send = list(set(numbers_to_send))

        # WhatsApp Cloud API URL
        url = f"https://graph.facebook.com/{config.api_version or 'v18.0'}/{config.phone_number_id}/messages"
        headers = {
            'Authorization': f'Bearer {config.access_token}',
            'Content-Type': 'application/json'
        }

        # 3. Initialize tracking
        sent_numbers = []
        failed_numbers = []
        sent_log_lines = []
        failed_log_lines = []
        display_log_lines = []

        # 4. Loop to SEND
        for number in numbers_to_send:
            # clean_number = ''.join(c for c in number if c.isdigit() or c == '+')
            # Relaxed cleaning: just strip spaces, maybe user wants to send to weird numbers?
            # Still, WhatsApp API usually needs digits. Let's keep minimal cleaning but strict validation off.
            clean_number = number.strip() # usage of raw input
            wa_number = clean_number.replace('+', '').replace(' ', '')

            # Build payload based on message type
            payload = {}
            try:
                if self.message_type == 'text':
                    payload = self._get_text_payload(wa_number)
                elif self.message_type == 'media':
                    payload = self._get_media_payload(wa_number)
            except Exception as e:
                 # Capture payload build errors (like validation)
                 failed_numbers.append(number)
                 failed_log_lines.append(f"{number}: Payload Error - {str(e)}")
                 display_log_lines.append(f"❌ {number}: {str(e)}")
                 continue

            try:
                response = requests.post(url, json=payload, headers=headers, timeout=30)

                # --- SUCCESS CASE ---
                if response.status_code in (200, 201):
                    sent_numbers.append(number)
                    try:
                        resp_json = response.json()
                        wa_id = resp_json.get('messages', [{}])[0].get('id', 'N/A')
                        
                        log_entry = f"""
                            <div class="d-flex align-items-center p-2 mb-2 border-bottom" style="background: #f1f8e9; border-radius: 4px; border-left: 3px solid #28a745;">
                                <div class="me-3 text-success fs-4"><i class="fa fa-check-circle"/></div>
                                <div class="flex-grow-1">
                                    <div class="d-flex justify-content-between">
                                        <strong>{number}</strong>
                                        <small class="text-muted">{fields.Datetime.now().strftime('%H:%M:%S')}</small>
                                    </div>
                                    <div class="text-muted small">ID: {wa_id}</div>
                                </div>
                            </div>
                        """
                        sent_log_lines.append(log_entry)
                        display_log_lines.append(log_entry)
                    except:
                        log_err = f"""
                            <div class="d-flex align-items-center p-2 mb-2 border-bottom" style="background: #f1f8e9; border-radius: 4px; border-left: 3px solid #28a745;">
                                <div class="me-3 text-success fs-4"><i class="fa fa-check-circle"/></div>
                                <div class="flex-grow-1">
                                    <strong>{number}</strong> | ✅ Delivered
                                </div>
                            </div>
                        """
                        sent_log_lines.append(log_err)
                        display_log_lines.append(log_err)

                # --- FAILURE CASE ---
                else:
                    failed_numbers.append(number)
                    try:
                        error_data = response.json()
                        msg = error_data.get('error', {}).get('message', response.text)
                    except:
                        msg = response.text

                    fail_entry = f"""
                        <div class="d-flex align-items-center p-2 mb-2 border-bottom" style="background: #fff5f5; border-radius: 4px; border-left: 3px solid #dc3545;">
                            <div class="me-3 text-danger fs-4"><i class="fa fa-times-circle"/></div>
                            <div class="flex-grow-1">
                                <div class="d-flex justify-content-between">
                                    <strong>{number}</strong>
                                    <small class="text-muted">{fields.Datetime.now().strftime('%H:%M:%S')}</small>
                                </div>
                                <div class="text-danger small">{msg}</div>
                            </div>
                        </div>
                    """
                    failed_log_lines.append(fail_entry)
                    display_log_lines.append(fail_entry)

            # --- EXCEPTION CASE ---
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
                _logger.exception("WhatsApp send error")

        # 5. SAVE DATA
        self.log_success = "".join(sent_log_lines)
        self.log_failure = "".join(failed_log_lines)
        self.response_log = "".join(display_log_lines)
        
        # Log to Console for debugging
        if sent_log_lines:
            _logger.info("WhatsApp SUCCESS LOG:\n%s", "\n".join(sent_log_lines))
        if failed_log_lines:
            _logger.error("WhatsApp FAILURE LOG:\n%s", "\n".join(failed_log_lines))

        # 6. Update Counts (with uniqueness safeguard)
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

    # ---------------------------
    # PAYLOAD HELPERS
    # ---------------------------
    def _get_text_payload(self, wa_number):
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": wa_number,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": self.message_body
            }
        }


    def write(self, vals):
        # Reset Media ID if file changes
        if 'media_file' in vals:
            vals['wa_media_id'] = False
        return super(WhatsAppMessage, self).write(vals)

    
    def _validate_media(self, file_content, filename, media_type):
        """Validate media file against WhatsApp Cloud API constraints."""
        file_size_mb = len(file_content) / (1024 * 1024)
        ext = os.path.splitext(filename)[1].lower()
        
        # Validation Rules
        rules = {
            'image': {'max_size': 5, 'ext': ['.jpg', '.jpeg', '.png']},
            'video': {'max_size': 16, 'ext': ['.mp4', '.3gp']},
            'audio': {'max_size': 16, 'ext': ['.aac', '.amr', '.mp3', '.m4a', '.ogg']},
            'document': {'max_size': 100, 'ext': ['.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.txt', '.csv']},
        }
        
        if media_type not in rules:
            return # Should not happen based on selection field
            
        rule = rules[media_type]
        
        # Check Extension
        if ext not in rule['ext']:
            raise UserError(_(f"Invalid file extension '{ext}' for {media_type}. Allowed: {', '.join(rule['ext'])}"))
            
        # Check Size
        if file_size_mb > rule['max_size']:
            raise UserError(_(f"File size {file_size_mb:.2f}MB exceeds the {media_type} limit of {rule['max_size']}MB."))

    def _upload_media_to_whatsapp(self):
        """Upload local media file to WhatsApp Cloud API to get an ID."""
        self.ensure_one()
        
        # Return cached ID if available
        if self.wa_media_id:
            return self.wa_media_id
        
        config = self.env['whatsapp.config'].search([], limit=1)
        if not config:
            raise UserError("WhatsApp Configuration missing.")

        url = f"https://graph.facebook.com/{config.api_version}/{config.phone_number_id}/media"
        headers = {
            'Authorization': f'Bearer {config.access_token}'
        }
        
        
        # Prepare file for upload
        file_content = base64.b64decode(self.media_file)
        filename = self.media_filename or "media_file"
        
        # Determine effective media type and auto-detection
        effective_media_type = self.media_type
        if self.message_type == 'media':
             # Auto-detect type based on extension if standard media
             ext = os.path.splitext(filename)[1].lower()
             if ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv'] and effective_media_type != 'document':
                  effective_media_type = 'document'
             elif ext in ['.png', '.jpg', '.jpeg'] and effective_media_type != 'image':
                  effective_media_type = 'image'
        
        # Validate Media
        self._validate_media(file_content, filename, effective_media_type)
        
        mime_type, _ = mimetypes.guess_type(filename)
        
        # Enforce MIME type strictly for WhatsApp Compatibility
        if not mime_type or mime_type == 'application/octet-stream':
             if effective_media_type == 'image':
                 mime_type = 'image/jpeg' # Default to jpeg for images
             elif effective_media_type == 'document':
                 mime_type = 'application/pdf' # Default to pdf
            
        files = {
            'file': (filename, file_content, mime_type)
        }
        
        # Add 'type' parameter as per user snippet
        data = {
            'messaging_product': 'whatsapp',
            'type': effective_media_type if effective_media_type in ['image', 'document'] else 'image'
        }
        
        try:
            # Increased timeout to 300s (5 min) for larger video files on slow connections
            response = requests.post(url, headers=headers, files=files, data=data, timeout=300)
            if response.status_code == 200:
                new_id = response.json().get('id')
                # Cache the ID
                self.write({'wa_media_id': new_id})
                return new_id
            else:
                error_msg = response.text
                try:
                    error_msg = response.json().get('error', {}).get('message', response.text)
                except:
                    pass
                raise UserError(f"Media Upload Failed: {error_msg}")
        except Exception as e:
            raise UserError(f"Media Upload Error: {str(e)}")

    def _get_media_payload(self, wa_number):
        if not self.media_file:
             raise UserError(_("Please upload a media file."))
             
        media_object = {}
        
        # 1. Try to use uploaded file first
        if self.media_file:
            media_id = self._upload_media_to_whatsapp()
            if media_id:
                media_object["id"] = media_id
                
        if not media_object:
             raise UserError("Could not upload media. Please try again.")

        # Auto-detect effective type for payload consistency
        effective_type = self.media_type
        if self.media_filename:
             ext = os.path.splitext(self.media_filename)[1].lower()
             if ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv']: effective_type = 'document'
             elif ext in ['.png', '.jpg', '.jpeg']: effective_type = 'image'

        if self.media_caption:
            media_object["caption"] = self.media_caption

        if effective_type == 'document' and self.media_filename:
            media_object["filename"] = self.media_filename

        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": wa_number,
            "type": effective_type,
            effective_type: media_object
        }

    # ---------------------------
    # Public: triggered by button
    # ---------------------------
    def action_send_message(self):
        self.ensure_one()

        # Validation
        if self.message_type == 'text' and not self.message_body:
            raise UserError(_("Please enter a message before sending."))
            
            
        if self.message_type == 'media' and not self.media_file:
             raise UserError(_("Please upload a media file."))
        
        # Auto-populate message_body for Media messages if empty (for UI display)
        if self.message_type == 'media' and not self.message_body:
             caption = self.media_caption or 'No Caption'
             self.message_body = f"📷 MEDIA\nSource: File Upload\nCaption: {caption}"

        if self.recipient_type == 'multi' and not self.recipient_multi:
             # Just warn or allow empty? User said remove validation.
             # raise UserError("Please enter or import mobile numbers before sending.")
             pass

        if self.recipient_type == 'single' and not self.recipient_single:
             # raise UserError("Please enter a mobile number before sending.")
             pass

        if self.recipient_type == 'contact' and not self.recipient_contacts:
            raise UserError(_("Please select at least one contact."))

        # --- NEW VALIDATION: Bulk messages can only be sent from DRAFT ---
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
            
            # Remove duplicates
            numbers_to_send = list(set(numbers_to_send))
            
            if not numbers_to_send:
                raise UserError("Selected contacts do not have valid mobile numbers.")
        else:
            numbers_to_send = [x.strip() for x in self.recipient_multi.split(',') if x.strip()]

        # Check for large media (> 2MB)
        is_large_media = False
        if self.media_file:
            # Base64 length calculation (approximate but reliable for this threshold)
            file_size_bytes = len(self.media_file) * 3 / 4
            if file_size_bytes > 2 * 1024 * 1024:
                is_large_media = True

        # For multiple recipients (>3) or large media (>2MB), use background queue
        if len(numbers_to_send) > 3 or is_large_media:
            return self._queue_messages(numbers_to_send, is_large_media=is_large_media)
        else:
            # For small batches, send directly
            return self._send_direct(numbers_to_send)

    def _queue_messages(self, numbers, is_large_media=False):
        """Queue messages for background processing"""
        self.ensure_one()
        
        Queue = self.env['whatsapp.message.queue']
        
        # Create queue items for each number
        for number in numbers:
            Queue.create({
                'message_id': self.id,
                'recipient': number,
                'message_body': self.message_body,
                'state': 'pending'
            })
        
        # Update state to show processing
        status_title = "LARGE MEDIA PROCESSING..." if is_large_media else "SENDING IN PROGRESS..."
        status_desc = "Your large file is being processed in the background" if is_large_media else "Messages are being sent automatically"
        
        log_message = f"""
            <div class="p-3 mb-3 border-bottom shadow-sm" style="background: #f8f9fa; border-radius: 8px; border-left: 5px solid #71639E;">
                <div class="d-flex align-items-center mb-2">
                    <div class="me-3 fs-3" style="color: #71639E;"><i class="fa fa-refresh fa-spin"/></div>
                    <div>
                        <h5 class="m-0 fw-bold" style="color: #71639E;">{status_title}</h5>
                        <small class="text-muted">{status_desc}</small>
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
                        <span class="badge" style="background: #71639E; color: white;">Queued</span>
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

        notif_msg = f'{len(numbers)} messages queued. Large media detected.' if is_large_media else f'{len(numbers)} messages queued for background sending.'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '✅ Background Processing!',
                'message': f'{notif_msg} You can continue using Odoo.',
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'}
            }
        }

    def _send_direct(self, numbers):
        """Send messages directly (for small batches)"""
        self.ensure_one()
        
        try:
            # Use original send logic
            self._send_to_whatsapp()

            if self.state == 'sent':
                msg_title = 'Success'
                msg_body = '✔ All Messages Sent Successfully!'
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
            _logger.exception("Unexpected error in action_send_message")
            self.response_log = str(e)
            self.state = 'failed'
            raise UserError(_("Unexpected error while sending message: %s") % e)

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
    def _cron_send_scheduled_messages(self):
        """Cron job to send scheduled WhatsApp messages"""
        now = fields.Datetime.now()
        scheduled = self.search([
            ('state', '=', 'scheduled'),
            ('schedule_datetime', '<=', now)
        ])
        for rec in scheduled:
            try:
                rec.action_send_message()
            except Exception:
                _logger.exception("Failed to send scheduled message for id %s", rec.id)
                rec.state = 'failed'
