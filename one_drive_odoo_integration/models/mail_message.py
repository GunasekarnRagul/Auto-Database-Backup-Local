# -*- coding: utf-8 -*-
from odoo import models, api
from markupsafe import Markup

class MailMessage(models.Model):
    _inherit = 'mail.message'

    @api.model_create_multi
    def create(self, vals_list):
        messages = super(MailMessage, self).create(vals_list)
        if not self.env.context.get('skip_drive_attachment_process'):
            self._process_drive_only_attachments(messages)
        return messages

    def write(self, vals):
        if self.env.context.get('skip_drive_attachment_process'):
            return super(MailMessage, self).write(vals)
            
        res = super(MailMessage, self).write(vals)
        if 'attachment_ids' in vals:
            self._process_drive_only_attachments(self)
        return res

    def _process_drive_only_attachments(self, messages):
        """Intercept messages with 'Drive Only' URL attachments, remove the 
        0kb visual card, and inject a clickable HTML button seamlessly.
        """
        for msg in messages:
            if not msg.attachment_ids or not msg.model:
                continue
                
            config = self.env['attachment.sync.config'].sudo().get_config_for_model(msg.model)
            if not config or config.storage_mode != 'drive':
                continue

            drive_links = []
            attachments_to_remove = []
            
            for att in msg.attachment_ids:
                if att.type == 'url' and att.one_drive_file_id and att.url:
                    drive_links.append((att.name, att.url))
                    attachments_to_remove.append(att.id)
            
            # Avoid duplicate injection if already processed
            if drive_links:
                body_parts = []
                body_parts.append("<div class='mt-3 mb-2 p-2 border rounded bg-light drive-attachment-links'>")
                body_parts.append("<b><i class='fa fa-windows'></i> OneDrive Documents:</b>")
                body_parts.append("<ul class='list-unstyled mb-0 mt-2'>")
                
                links_added = 0
                for name, url in drive_links:
                    # Make sure the exact link isn't already in the body
                    if url not in (msg.body or ''):
                        body_parts.append(f"<li><a href='{url}' target='_blank' rel='noopener noreferrer' class='btn btn-sm btn-primary mt-1 mb-1'><i class='fa fa-external-link'></i> Open {name}</a></li>")
                        links_added += 1
                        
                body_parts.append("</ul></div>")
                
                if links_added > 0:
                    body_addition = Markup("".join(body_parts))
                    new_body = (msg.body or Markup('')) + body_addition
                    msg.with_context(skip_drive_attachment_process=True).write({
                        'body': new_body,
                        'attachment_ids': [(3, att_id) for att_id in attachments_to_remove]
                    })
                else:
                    # Even if links were already there, make sure attachments are unlinked
                    msg.with_context(skip_drive_attachment_process=True).write({
                        'attachment_ids': [(3, att_id) for att_id in attachments_to_remove]
                    })


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _process_attachments_for_post(self, attachments, attachment_ids, msg_values):
        """Intercept attachments passed to message_post as raw binary tuples.
        If our module has removed the local binary data (Storage Mode: Drive Only),
        attachment.raw evaluates to False. Certain wizards (like account.move.send)
        pass attachment.raw directly to message_post, which crashes when Odoo tries
        to base64.b64encode(False). We strip these out so they are gracefully skipped.
        """
        if not attachments:
            return super(MailThread, self)._process_attachments_for_post(
                attachments, attachment_ids, msg_values
            )

        clean_attachments = []
        for att in attachments:
            # If the attachment tuple has content data that is literally False,
            # we convert it to None so Odoo's core logic safely skips it
            # instead of throwing "TypeError: a bytes-like object is required, not 'bool'".
            if len(att) >= 2 and att[1] is False:
                # Odoo's core skips 'None' explicitly, so we replace False with None.
                new_att = list(att)
                new_att[1] = None
                clean_attachments.append(tuple(new_att))
            else:
                clean_attachments.append(att)
                
        return super(MailThread, self)._process_attachments_for_post(
            clean_attachments, attachment_ids, msg_values
        )
