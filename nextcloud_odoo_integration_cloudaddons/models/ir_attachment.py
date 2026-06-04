# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from markupsafe import Markup

_logger = logging.getLogger(__name__)

class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    nextcloud_file_id = fields.Char('Nextcloud File ID', index=True)
    nextcloud_id = fields.Many2one('nextcloud.config', string='Drive', compute='_compute_nextcloud_id', store=True, readonly=False)
    nextcloud_folder_id = fields.Many2one('nextcloud.file', string='Folder', domain="[('file_type', '=', 'folder')]", compute='_compute_nextcloud_folder_id', store=True, readonly=False)
    model_name = fields.Selection(selection='_get_model_selection', string='Model Name', index=True, help="Technical name of the Odoo model")
    model_display = fields.Char('Model', compute='_compute_model_display', store=True)
    sync_type = fields.Selection([
        ('internal', 'Internal'),
        ('external', 'External'),
    ], string='Sync Type', default='internal')
    google_folder_path = fields.Char('Folder Structure', compute='_compute_google_folder_path', store=True)
    auto_sync_enabled = fields.Boolean('Auto Sync Enabled', default=False)
    storage_mode = fields.Selection([
        ('drive', 'Drive only'),
        ('dual', 'Dual (Drive + Odoo)'),
        ('odoo', 'Odoo only (no sync)'),
    ], string='Storage Mode', compute='_compute_storage_mode', store=True, readonly=False)
    is_nextcloud_synced = fields.Boolean('Nextcloud Synced', compute='_compute_is_nextcloud_synced', store=True)
    is_model_enabled_for_sync = fields.Boolean('Model Enabled for Sync', compute='_compute_is_model_enabled_for_sync', store=True)

    @api.depends('nextcloud_file_id', 'res_model', 'res_id')
    def _compute_nextcloud_id(self):
        """Get the drive associated with this attachment. Fallback to model config if not synced."""
        for attachment in self:
            if attachment.nextcloud_file_id:
                file_record = self.env['nextcloud.file'].sudo().search([
                    ('nextcloud_file_id', '=', attachment.nextcloud_file_id)
                ], limit=1)
                attachment.nextcloud_id = file_record.drive_config_id.id if file_record else False
            else:
                # Fallback to model configuration
                res_model = attachment.res_model
                res_id = attachment.res_id
                if res_model == 'mail.message' and res_id:
                    res_model, _ = self._resolve_mail_message_parent(res_id)
                
                config = self.env['attachment.sync.config'].sudo().get_config_for_model(res_model)
                attachment.nextcloud_id = config.nextcloud_id.id if config else False

    @api.depends('nextcloud_file_id', 'res_model', 'res_id')
    def _compute_nextcloud_folder_id(self):
        """Get the folder associated with this attachment. Fallback to model config if not synced."""
        for attachment in self:
            if attachment.nextcloud_file_id:
                file_record = self.env['nextcloud.file'].sudo().search([
                    ('nextcloud_file_id', '=', attachment.nextcloud_file_id)
                ], limit=1)
                attachment.nextcloud_folder_id = file_record.parent_folder_id.id if file_record else False
            else:
                # Fallback to model configuration
                res_model = attachment.res_model
                res_id = attachment.res_id
                if res_model == 'mail.message' and res_id:
                    res_model, _ = self._resolve_mail_message_parent(res_id)
                
                config = self.env['attachment.sync.config'].sudo().get_config_for_model(res_model)
                attachment.nextcloud_folder_id = config.nextcloud_folder_id.id if config else False

    @api.depends('res_model', 'res_id')
    def _compute_storage_mode(self):
        """Get the storage mode from model config for non-synced attachments."""
        for attachment in self:
            res_model = attachment.res_model
            res_id = attachment.res_id
            if res_model == 'mail.message' and res_id:
                res_model, _ = self._resolve_mail_message_parent(res_id)
            
            config = self.env['attachment.sync.config'].sudo().get_config_for_model(res_model)
            attachment.storage_mode = config.storage_mode if config else 'dual'

    @api.model
    def _get_model_selection(self):
        """Dynamic selection of models from nextcloud.model.config."""
        configs = self.env['nextcloud.model.config'].sudo().search([])
        return [(c.res_model, c.model_label) for c in configs]

    @api.depends('model_name', 'res_model')
    def _compute_model_display(self):
        """Get friendly display name for the model using nextcloud.model.config."""
        for attachment in self:
            res_model = attachment.model_name or attachment.res_model
            if not res_model:
                attachment.model_display = False
                continue
            
            config = self.env['nextcloud.model.config'].sudo().search([
                ('res_model', '=', res_model)
            ], limit=1)
            attachment.model_display = config.model_label if config else res_model

    @api.depends('nextcloud_id', 'nextcloud_folder_id')
    def _compute_google_folder_path(self):
        """Compute a human-readable folder path from the selected drive and folder.
        Correct order: Drive / GrandParent / Parent / SelectedFolder
        """
        for attachment in self:
            path_parts = []
            if attachment.nextcloud_id:
                path_parts.append(attachment.nextcloud_id.name)
            if attachment.nextcloud_folder_id:
                # Build ancestor chain root→parent first, then append selected folder last
                ancestors = []
                parent = attachment.nextcloud_folder_id.parent_folder_id
                while parent:
                    ancestors.insert(0, parent.name)
                    parent = parent.parent_folder_id
                path_parts.extend(ancestors)
                path_parts.append(attachment.nextcloud_folder_id.name)
            attachment.google_folder_path = ' / '.join(path_parts) if path_parts else ''

    @api.depends('nextcloud_file_id')
    def _compute_is_nextcloud_synced(self):
        """Check if attachment is synced with Nextcloud."""
        for attachment in self:
            attachment.is_nextcloud_synced = bool(attachment.nextcloud_file_id)

    @api.depends('res_model')
    def _compute_is_model_enabled_for_sync(self):
        """Check if the attachment's model is enabled for Nextcloud sync."""
        # Get all enabled model configurations
        enabled_configs = self.env['nextcloud.model.config'].sudo().search([
            ('is_enabled', '=', True)
        ])
        enabled_models = set(config.res_model for config in enabled_configs)
        
        for attachment in self:
            attachment.is_model_enabled_for_sync = attachment.res_model in enabled_models

    def get_model_config(self):
        """Get the Nextcloud model configuration for this attachment."""
        self.ensure_one()
        if not self.res_model:
            return False
        return self.env['nextcloud.model.config'].sudo().search([
            ('res_model', '=', self.res_model)
        ], limit=1)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('model_name') and not vals.get('res_model'):
                vals['res_model'] = vals['model_name']

        attachments = super(IrAttachment, self).create(vals_list)

        for attachment in attachments:
            if self.env.context.get('skip_nextcloud_sync'):
                continue

            res_model = attachment.res_model
            res_id = attachment.res_id

            # ────────────────────────────────────────────────────────────────────────────
            # Case A: mail.message chatter attachment
            # Resolve the parent model and upload ONCE to the correct model folder.
            # Setting nextcloud_file_id on the mail.message record makes the chatter
            # display the Drive link icon (↗) instead of a download button (↓).
            # ────────────────────────────────────────────────────────────────────────────
            if res_model == 'mail.message' and res_id:
                parent_model, parent_res_id = self._resolve_mail_message_parent(res_id)
                if not parent_model or not parent_res_id:
                    continue
                config = self.env['attachment.sync.config'].sudo().get_config_for_model(parent_model)
                if not config or config.storage_mode == 'odoo' or not config.auto_sync_mode:
                    continue
                if not self._matches_file_type_filter(attachment, config):
                    continue

                # If this mail.message attachment has no file data (e.g. Drive-only mode
                # already cleared the local data from the direct model attachment), try to
                # INHERIT the Drive link from the direct attachment instead of re-uploading.
                if not attachment.datas and not attachment.raw:
                    import re
                    # Auto-recover if Odoo already copied the Nextcloud URL into the type='url' field
                    if attachment.type == 'url' and attachment.url and ('nextcloud.com' in attachment.url or 'nextcloud.com' in attachment.url):
                        # For Nextcloud, the ID is often in the resid parameter
                        import urllib.parse as urlparse
                        # For Nextcloud, extract the stable ID from the URL if stored
                        # The nextcloud_file_id should already be set; use URL as fallback key
                        res_id_param = attachment.url or False
                        if res_id_param:
                            attachment.with_context(skip_nextcloud_sync=True).write({
                                'nextcloud_file_id': res_id_param
                            })
                            if config.storage_mode == 'drive':
                                attachment._inject_chatter_drive_button(attachment.url)
                            continue

                    # ── Step 1: Fuzzy-name search for direct synced attachment ──
                    # Handles 'INV/2026/00032.pdf' vs 'INV_2026_00032.pdf' mismatches.
                    direct = self._find_direct_synced_attachment(
                        parent_model, parent_res_id, attachment.name
                    )
                    if direct:
                        drive_url = direct.url or \
                            f"https://nextcloud.com/redir?resid={direct.nextcloud_file_id}"
                        attachment.with_context(skip_nextcloud_sync=True).write({
                            'nextcloud_file_id': direct.nextcloud_file_id,
                            'type': 'url',
                            'url': drive_url,
                        })
                        # ── Step 2: Immediately inject Drive button into chatter ──
                        if config.storage_mode == 'drive':
                            attachment._inject_chatter_drive_button(drive_url)
                    continue  # No data = no upload; either inherited or Step 3 will catch it

                attachment.with_context(
                    sync_target_model=parent_model,
                    sync_target_id=parent_res_id,
                )._auto_sync_to_drive(config)
                continue

            # Totally ignore non-relevant mail models
            if res_model in ('mail.thread', 'mail.followers'):
                continue

            # ────────────────────────────────────────────────────────────────────────────
            # Case B: mail.compose.message wizard — resolve to real model/id
            # ────────────────────────────────────────────────────────────────────────────
            if res_model == 'mail.compose.message':
                wizard = self.env['mail.compose.message'].sudo().browse(res_id) if res_id else False
                if wizard and wizard.exists() and wizard.model:
                    res_model = wizard.model
                    res_ids = getattr(wizard, 'res_ids', False)
                    if res_ids:
                        try:
                            if isinstance(res_ids, str):
                                res_id = int(res_ids.split(',')[0])
                            elif isinstance(res_ids, (list, tuple)):
                                res_id = res_ids[0]
                            else:
                                res_id = res_ids.ids[0] if res_ids.ids else 0
                        except Exception:
                            res_id = 0
                    else:
                        res_id = getattr(wizard, 'res_id', 0)
                else:
                    continue  # Wizard not committed or no model

            # ────────────────────────────────────────────────────────────────────────────
            # Case B2: account.move.send wizard — resolve to the actual invoice (account.move)
            # Odoo generates the invoice PDF through this wizard when the user clicks
            # "Send & Print". Without resolving it here, get_config_for_model() finds no
            # match for 'account.move.send' and the upload is silently skipped, leaving
            # the chatter attachment as a plain file card instead of a Drive link.
            # ────────────────────────────────────────────────────────────────────────────
            if res_model == 'account.move.send':
                wizard = self.env['account.move.send'].sudo().browse(res_id) if res_id else False
                if wizard and wizard.exists() and hasattr(wizard, 'move_ids') and wizard.move_ids:
                    res_model = 'account.move'
                    res_id = wizard.move_ids[0].id
                else:
                    continue  # Wizard has no linked invoice yet

            # ────────────────────────────────────────────────────────────────────────────
            # Case C: Direct model attachment
            # Skip if the mail.message copy of the same file for this record was
            # already synced — this prevents uploading the same file twice to Drive.
            # ────────────────────────────────────────────────────────────────────────────
            config = self.env['attachment.sync.config'].sudo().get_config_for_model(res_model)
            if not config or config.storage_mode == 'odoo' or not config.auto_sync_mode:
                continue
            if not self._matches_file_type_filter(attachment, config):
                continue
            if not attachment.datas and not attachment.raw:
                continue

            # Dedup: skip if mail.message copy already synced this file for this record
            msg_already_synced = self.env['ir.attachment'].sudo().search_count([
                ('res_model', '=', 'mail.message'),
                ('name', '=', attachment.name),
                ('nextcloud_file_id', '!=', False),
                ('res_id', 'in', self.env['mail.message'].sudo().search([
                    ('model', '=', res_model), ('res_id', '=', res_id)
                ]).ids),
            ]) > 0
            if msg_already_synced:
                continue

            attachment.with_context(
                sync_target_model=res_model,
                sync_target_id=res_id,
            )._auto_sync_to_drive(config)

        return attachments

    def _resolve_mail_message_parent(self, msg_id):
        """Safely resolve the parent model/res_id from a mail.message record."""
        try:
            msg = self.env['mail.message'].sudo().browse(msg_id)
            if msg.exists() and msg.model and msg.res_id:
                return msg.model, msg.res_id
        except Exception:
            pass
        return None, None

    def _normalize_att_name(self, name):
        """Normalize attachment name for fuzzy comparison.
        Replaces path separators with underscores and strips the PDF extension.
        e.g.  'INV/2026/00032.pdf' → 'inv_2026_00032'
              'INV_2026_00032.pdf' → 'inv_2026_00032'
        """
        import os
        base = os.path.splitext(name or '')[0]
        return base.replace('/', '_').replace('\\', '_').lower()

    def _find_direct_synced_attachment(self, parent_model, parent_res_id, att_name):
        """Find a synced direct attachment on the parent record that matches att_name.

        Uses a tiered fuzzy match so slashes-vs-underscores differences
        (e.g. 'INV/2026/00032.pdf' vs 'INV_2026_00032.pdf') don't cause misses.

        Priority:
          1. Exact name match
          2. Normalised name match (slashes → underscores, case-insensitive)
          3. Same PDF mimetype  (last resort for single-PDF records)
        """
        candidates = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', parent_model),
            ('res_id', '=', parent_res_id),
            ('nextcloud_file_id', '!=', False),
        ])
        if not candidates:
            return self.env['ir.attachment'].browse()

        norm_target = self._normalize_att_name(att_name)

        # Tier 1 – exact
        exact = candidates.filtered(lambda a: a.name == att_name)
        if exact:
            return exact[:1]

        # Tier 2 – normalised
        fuzzy = candidates.filtered(
            lambda a: self._normalize_att_name(a.name) == norm_target
        )
        if fuzzy:
            return fuzzy[:1]

        # Tier 3 – same mimetype (only safe when there is exactly one PDF candidate)
        pdf_candidates = candidates.filtered(lambda a: 'pdf' in (a.mimetype or ''))
        if len(pdf_candidates) == 1 and 'pdf' in (att_name or '').lower():
            return pdf_candidates

        return self.env['ir.attachment'].browse()

    def _inject_chatter_drive_button(self, drive_url, file_name=None, unlink_card=True):
        """Single source-of-truth for chatter Drive-button injection.

        After this attachment is set to type='url' (either via inheritance from
        a direct synced attachment or after upload), this method:
          1. Finds every mail.message that has this attachment linked.
          2. Appends a clickable Nextcloud button to the message body.
          3. Unlinks the 0kb file card from the message so only the button shows.

        Works for both:
          - Chatter copies  (res_model='mail.message') — found via res_id.
          - Direct model attachments linked via M2M join table.
        """
        self.ensure_one()
        file_name = file_name or self.name
        if not drive_url:
            return

        # Locate the linked mail.message
        linked_messages = self.env['mail.message'].browse()
        if self.res_model == 'mail.message' and self.res_id:
            msg = self.env['mail.message'].sudo().browse(self.res_id)
            if msg.exists():
                linked_messages = msg
        else:
            linked_messages = self.env['mail.message'].sudo().search([
                ('attachment_ids', 'in', [self.id])
            ])
            
        _logger.info(f"GD_SYNC: linked_messages = {linked_messages}")

        btn_html = (
            "<div class='mt-3 mb-2 p-2 border rounded bg-light drive-attachment-links'>"
            "<b><i class='fa fa-nextcloud'></i> Nextcloud Documents:</b>"
            "<ul class='list-unstyled mb-0 mt-2'>"
            f"<li><a href='{drive_url}' target='_blank' rel='noopener noreferrer' "
            f"class='btn btn-sm btn-primary mt-1 mb-1'>"
            f"<i class='fa fa-external-link'></i> Open {file_name}</a></li>"
            "</ul></div>"
        )

        for msg in linked_messages:
            if not msg.model:
                continue
            _logger.info(f"GD_SYNC: writing body to message {msg.id}")
            write_vals = {}
            if unlink_card:
                write_vals['attachment_ids'] = [(3, self.id)]  # unlink file card M2M
            
            if drive_url not in (msg.body or ''):
                write_vals['body'] = Markup(msg.body or '') + Markup(btn_html)
            msg.with_context(
                skip_drive_attachment_process=True,
                skip_nextcloud_sync=True,
            ).write(write_vals)
            
            # Key fix for "0kb ghost card" for mail.message duplicates:
            # If the attachment's res_model is literally 'mail.message', Odoo will
            # always show it in the chatter regardless of missing the M2M link.
            # We change res_model so Odoo ignores it, leaving only the button!
            if self.res_model == 'mail.message':
                self.with_context(skip_nextcloud_sync=True).write({'res_model': 'nextcloud.discard'})

    def write(self, vals):
        # Handle model_name → res_model mapping
        if vals.get('model_name') and not vals.get('res_model'):
            vals['res_model'] = vals['model_name']

        res = super(IrAttachment, self).write(vals)

        if self.env.context.get('skip_nextcloud_sync'):
            return res

        # Catch attachments that get their data late (e.g. generated report PDFs)
        trigger_fields = {'res_model', 'res_id', 'datas', 'raw', 'db_datas', 'store_fname', 'type'}
        if any(f in vals for f in trigger_fields):
            for attachment in self:
                if attachment.nextcloud_file_id:
                    continue  # Already synced

                res_model = attachment.res_model
                res_id = attachment.res_id

                # ─ Case A: mail.message chatter attachment ─
                if res_model == 'mail.message' and res_id:
                    parent_model, parent_res_id = self._resolve_mail_message_parent(res_id)
                    if not parent_model or not parent_res_id:
                        continue
                    config = self.env['attachment.sync.config'].sudo().get_config_for_model(parent_model)
                    if not config or config.storage_mode == 'odoo' or not config.auto_sync_mode:
                        continue
                    if not self._matches_file_type_filter(attachment, config):
                        continue

                    # No local data: try inheriting Drive link from direct attachment
                    if not attachment.datas and not attachment.raw:
                        import re
                        # Auto-recover if Odoo already copied the Nextcloud URL into type='url'
                        if attachment.type == 'url' and attachment.url and ('nextcloud.com' in attachment.url or 'nextcloud.com' in attachment.url):
                            import urllib.parse as urlparse
                            parsed = urlparse.urlparse(attachment.url)
                            res_id_param = urlparse.parse_qs(parsed.query).get('resid', [False])[0]
                            if res_id_param:
                                attachment.with_context(skip_nextcloud_sync=True).write({
                                    'nextcloud_file_id': res_id_param
                                })
                                if config.storage_mode == 'drive':
                                    attachment._inject_chatter_drive_button(attachment.url)
                                continue

                        # ── Step 1 (write): Fuzzy-name search ──
                        direct = attachment._find_direct_synced_attachment(
                            parent_model, parent_res_id, attachment.name
                        )
                        if direct:
                            drive_url = direct.url or \
                                f"https://nextcloud.com/redir?resid={direct.nextcloud_file_id}"
                            attachment.with_context(skip_nextcloud_sync=True).write({
                                'nextcloud_file_id': direct.nextcloud_file_id,
                                'type': 'url',
                                'url': drive_url,
                            })
                            # ── Step 2 (write): Inject Drive button ──
                            if config.storage_mode == 'drive':
                                attachment._inject_chatter_drive_button(drive_url)
                        continue

                    attachment.with_context(
                        sync_target_model=parent_model,
                        sync_target_id=parent_res_id,
                    )._auto_sync_to_drive(config)
                    continue

                if res_model in ('mail.thread', 'mail.followers'):
                    continue

                # ─ Case B2: account.move.send wizard → resolve to account.move ─
                if res_model == 'account.move.send':
                    wizard = self.env['account.move.send'].sudo().browse(res_id) if res_id else False
                    if wizard and wizard.exists() and hasattr(wizard, 'move_ids') and wizard.move_ids:
                        res_model = 'account.move'
                        res_id = wizard.move_ids[0].id
                    else:
                        continue  # Wizard has no linked invoice yet

                # ─ Case C: Direct model attachment ─
                config = self.env['attachment.sync.config'].sudo().get_config_for_model(res_model)
                # Guard matches create(): skip if no config, odoo-only mode, or auto-sync is off
                if not config or config.storage_mode == 'odoo' or not config.auto_sync_mode:
                    continue
                if not self._matches_file_type_filter(attachment, config):
                    continue
                if not attachment.datas and not attachment.raw:
                    continue

                # Dedup: skip if mail.message copy already synced this file
                msg_already_synced = self.env['ir.attachment'].sudo().search_count([
                    ('res_model', '=', 'mail.message'),
                    ('name', '=', attachment.name),
                    ('nextcloud_file_id', '!=', False),
                    ('res_id', 'in', self.env['mail.message'].sudo().search([
                        ('model', '=', res_model), ('res_id', '=', res_id)
                    ]).ids),
                ]) > 0
                if msg_already_synced:
                    continue

                attachment.with_context(
                    sync_target_model=res_model,
                    sync_target_id=res_id,
                )._auto_sync_to_drive(config)

        return res

    @api.model
    def _matches_file_type_filter(self, attachment, config):
        """Check if attachment matches the file type filter from the sync config.
        Returns True if the attachment should be synced.
        """
        if config.file_type == 'all':
            return True

        # Resolve actual mimetype from filename if attachment has generic mimetype
        mimetype = attachment.mimetype or ''
        name = (attachment.name or '').lower()

        if config.file_type == 'pdf':
            return 'pdf' in mimetype or name.endswith('.pdf')
        elif config.file_type == 'doc':
            return 'word' in mimetype or 'document' in mimetype or name.endswith(('.doc', '.docx'))
        elif config.file_type == 'xls':
            return 'excel' in mimetype or 'spreadsheet' in mimetype or name.endswith(('.xls', '.xlsx'))
        elif config.file_type == 'ppt':
            return 'powerpoint' in mimetype or 'presentation' in mimetype or name.endswith(('.ppt', '.pptx'))
        elif config.file_type == 'img':
            return mimetype.startswith('image/') or name.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg'))
        return True

    def _auto_sync_to_drive(self, config):
        _logger.info("START _auto_sync_to_drive for %s %s %s", self.name, self.res_model, self.res_id)
        """Automated sync to drive with storage mode handling (Drive only vs Dual)."""
        self.ensure_one()
        sync_service = self.env['nextcloud.sync'].sudo()
        
        # Determine the storage mode (fallback to config if not passed via context)
        storage_mode = self.env.context.get('manual_storage_mode') or config.storage_mode

        # ── Resolve Nextcloud connection ──────────────────────────────────────────
        # If the config has no nextcloud_id set, fall back to the first active drive.
        nextcloud_cfg = config.nextcloud_id
        if not nextcloud_cfg:
            nextcloud_cfg = self.env['nextcloud.config'].sudo().search(
                [('active', '=', True), ('is_connected', '=', True)], limit=1
            ) or self.env['nextcloud.config'].sudo().search(
                [('active', '=', True)], limit=1
            )
        if not nextcloud_cfg:
            _logger.warning("_auto_sync_to_drive: no active Nextcloud config found, aborting.")
            return

        # ── Resolve parent folder path ────────────────────────────────────────────
        # Prefer the folder explicitly set on the sync config; fall back to the first
        # active root folder of the resolved nextcloud connection.
        parent_id = config.nextcloud_folder_id.nextcloud_file_id if config.nextcloud_folder_id else False
        if not parent_id:
            root = nextcloud_cfg.root_ids.filtered(lambda r: r.active)[:1]
            parent_id = root.root_id if root else False

        # If parent_id is still a numeric string (Nextcloud file ID), resolve it to a path
        if parent_id and not str(parent_id).startswith('/'):
            parent_id = sync_service._get_remote_path_from_id(parent_id)

        # ── Ensure the config base folder exists on Nextcloud ─────────────────────
        # The folder may exist in Odoo's DB but not yet on the Nextcloud server.
        # Ensure every segment of the base path exists before creating sub-folders
        # inside it — otherwise MKCOL returns HTTP 409 Conflict.
        if parent_id and parent_id != '/':
            path_so_far = ''
            for segment in parent_id.strip('/').split('/'):
                if not segment:
                    continue
                path_so_far += '/' + segment
                sync_service._webdav_mkdir(nextcloud_cfg, path_so_far)

        # Apply Dynamic Folder Structure Logic
        # Prefer explicit context targets; auto-resolve mail.message to its parent so that
        # chatter attachment copies always land in the correct model folder (not 'General/mail_message/…').
        res_model = self.env.context.get('sync_target_model') or self.res_model
        res_id = self.env.context.get('sync_target_id') or self.res_id
        if res_model == 'mail.message' and res_id:
            parent_model, parent_res_id = self._resolve_mail_message_parent(res_id)
            if parent_model and parent_res_id:
                res_model, res_id = parent_model, parent_res_id
        
        model_root, record_folder = self._get_sync_subfolder_name(
            res_model, res_id
        )
        
        current_local_parent_id = config.nextcloud_folder_id.id if config.nextcloud_folder_id else False
        current_drive_parent_id = parent_id

        def get_or_create_local_folder(f_name, parent_local, config_drive_id, nextcloud_id, nextcloud_url, folder_res_model=False, folder_res_id=False):
            search_domain = [
                ('name', '=', f_name),
                ('parent_folder_id', '=', parent_local),
                ('file_type', '=', 'folder'),
            ]
            local_folder = self.env['nextcloud.file'].sudo().search(search_domain, limit=1)
            if not local_folder:
                # Use a savepoint to handle concurrent uploads that race to create the
                # same folder (avoids IntegrityError from the unique constraint).
                try:
                    with self.env.cr.savepoint():
                        local_folder = self.env['nextcloud.file'].sudo().create({
                            'name': f_name,
                            'file_type': 'folder',
                            'drive_config_id': config_drive_id,
                            'parent_folder_id': parent_local,
                            'nextcloud_file_id': nextcloud_id,
                            'nextcloud_url': nextcloud_url,
                            'sync_state': 'synced',
                            'owner_name': self.env.user.name,
                            'res_model': folder_res_model,
                            'res_id': folder_res_id,
                        })
                except Exception:
                    # Another process won the race; re-search to get that record.
                    local_folder = self.env['nextcloud.file'].sudo().search(search_domain, limit=1)
            return local_folder.id if local_folder else parent_local
        
        if model_root and record_folder and current_drive_parent_id:
            # 1. Find or create Model Root Folder (e.g. Sales, CRM, Accounting...)
            root_res = sync_service.find_or_create_folder(model_root, current_drive_parent_id, nextcloud_cfg)
            if root_res:
                current_local_parent_id = get_or_create_local_folder(
                    model_root, current_local_parent_id, nextcloud_cfg.id,
                    root_res['nextcloud_file_id'], root_res.get('nextcloud_url'),
                    folder_res_model=res_model, folder_res_id=False
                )
                # Notify explorer that the root config folder has new contents (the model folder)
                self.env['nextcloud.sync'].sudo()._notify_folder_sync(config.nextcloud_folder_id.id if config.nextcloud_folder_id else False)

                # 2. Find or create Record Folder (e.g. order_idS00001, lead_idLead Name, move_idINV/2025/0001)
                # record_folder is a list to support names with '/' (e.g. INV/2025/0001 -> ['move_idINV', '2025', '0001'])
                path_parts = record_folder if isinstance(record_folder, list) else [record_folder]
                last_res = root_res
                for part in path_parts:
                    if not part:
                        continue
                    part_res = sync_service.find_or_create_folder(part, last_res['nextcloud_file_id'], nextcloud_cfg)
                    if part_res:
                        # Capture parent ID before updating it to the new sub-folder
                        folder_to_notify = current_local_parent_id
                        current_local_parent_id = get_or_create_local_folder(
                            part, current_local_parent_id, nextcloud_cfg.id,
                            part_res['nextcloud_file_id'], part_res.get('nextcloud_url'),
                            folder_res_model=res_model, folder_res_id=res_id
                        )
                        # Notify explorer that this parent folder now has a new sub-folder
                        self.env['nextcloud.sync'].sudo()._notify_folder_sync(folder_to_notify)
                        last_res = part_res
                    else:
                        break

                # Files are uploaded directly into the record folder (no category subfolder)
                parent_id = last_res['nextcloud_file_id']

        # Attempt Upload — abort early if there is no file content (prevents 0-byte uploads)
        import base64
        file_content = base64.b64decode(self.datas) if self.datas else self.raw
        if not file_content:
            return  # No data yet; write() will retry when data arrives

        try:
            result = sync_service.upload_file_to_drive(
                self.name,
                file_content,
                self.mimetype or 'application/octet-stream',
                nextcloud_cfg,
                parent_nextcloud_id=parent_id
            )

            if result:
                # Write Drive file ID + metadata back to this attachment
                att_vals = {
                    'nextcloud_file_id': result['nextcloud_file_id'],
                    'nextcloud_id': nextcloud_cfg.id,
                    'nextcloud_folder_id': config.nextcloud_folder_id.id if config.nextcloud_folder_id else False,
                    'sync_type': 'external',
                }
                # Handle "Drive only" mode — fully purge binary from Odoo storage
                if storage_mode == 'drive':
                    att_vals.update({
                        'type': 'url',
                        'url': result['nextcloud_url'],
                        'datas': False,      # Clears db_datas (DB blob)
                        'db_datas': False,
                        'store_fname': False,  # Removes filestore path so disk file is GC'd
                    })
                self.with_context(skip_nextcloud_sync=True).write(att_vals)

                # ── Step 2: Chatter Drive-button injection via helper ────────────────────
                # Inject the Nextcloud link button into the chatter for both Drive and Dual modes.
                # Only unlink the local attachment card if we are in "Drive only" mode.
                self._inject_chatter_drive_button(
                    result['nextcloud_url'], 
                    unlink_card=(storage_mode == 'drive')
                )

                # ── Step 3: Ghost chatter-copy scan ─────────────────────────────────────
                # When the DIRECT model attachment is synced (e.g. the account.move PDF),
                # its 0-byte chatter copy (res_model='mail.message') may already exist but
                # have no nextcloud_file_id because the Step 1 inheritance search ran BEFORE
                # the direct attachment had a nextcloud_file_id. Fix all such ghosts now.
                if storage_mode == 'drive':
                    res_model_target = (
                        self.env.context.get('sync_target_model') or self.res_model
                    )
                    res_id_target = (
                        self.env.context.get('sync_target_id') or self.res_id
                    )
                    if res_model_target and res_id_target \
                            and res_model_target != 'mail.message':
                        messages = self.env['mail.message'].sudo().search([
                            ('model', '=', res_model_target),
                            ('res_id', '=', res_id_target),
                        ])
                        if messages:
                            ghost_atts = self.env['ir.attachment'].sudo().search([
                                ('res_model', '=', 'mail.message'),
                                ('res_id', 'in', messages.ids),
                                ('nextcloud_file_id', '=', False),
                            ])
                            synced_url = result['nextcloud_url']
                            synced_gid = result['nextcloud_file_id']
                            for ghost in ghost_atts:
                                # Fuzzy name check before adopting this ghost
                                if self._normalize_att_name(ghost.name) != \
                                        self._normalize_att_name(self.name) \
                                        and ghost.name != self.name:
                                    continue
                                ghost.with_context(skip_nextcloud_sync=True).write({
                                    'nextcloud_file_id': synced_gid,
                                    'type': 'url',
                                    'url': synced_url,
                                    'datas': False,
                                    'db_datas': False,
                                })
                                ghost._inject_chatter_drive_button(synced_url)
                # ────────────────────────────────────────────────────────────────────────

                # Update sync count on config
                import pytz as _pytz
                import datetime as _dt
                _user_tz = _pytz.timezone(self.env.user.tz or 'UTC')
                _utc_now = _dt.datetime.utcnow().replace(tzinfo=_pytz.utc)
                ts = _utc_now.astimezone(_user_tz).strftime('%Y-%m-%d %H:%M:%S')
                
                config.write({
                    'sync_count': config.sync_count + 1,
                    'last_synced': ts,
                })
                existing_drive_file = self.env['nextcloud.file'].sudo().search([
                    ('nextcloud_file_id', '=', result['nextcloud_file_id']),
                    ('drive_config_id', '=', nextcloud_cfg.id),
                ], limit=1)
                if not existing_drive_file:
                    self.env['nextcloud.file'].sudo().create({
                        'name': self.name,
                        'drive_config_id': nextcloud_cfg.id,
                        'parent_folder_id': current_local_parent_id,
                        'file_type': 'file',
                        'mime_type': result.get('mime_type') or self.mimetype or 'application/octet-stream',
                        'file_size': len(file_content) if file_content else 0,
                        'nextcloud_file_id': result['nextcloud_file_id'],
                        'nextcloud_url': result['nextcloud_url'],
                        'owner_name': self.env.user.name,
                        'sync_state': 'synced',
                        'last_synced': fields.Datetime.now(),
                        'res_model': res_model,
                        'res_id': res_id,
                    })
                
                # Trigger real-time File Explorer refresh for the target folder
                self.env['nextcloud.sync'].sudo()._notify_folder_sync(current_local_parent_id)

                # ── Detailed Activity Log ────────────────────────────────────────────────
                # Captures sync_type, full hierarchical folder path, timestamp, Drive URL,
                # and storage mode so the Activity Log shows a complete audit trail.
                import pytz as _pytz
                import datetime as _dt
                current_sync_type = self.env.context.get('sync_type', 'auto')
                record_path_str = "/".join(record_folder) if isinstance(record_folder, list) else str(record_folder)

                # Build the base folder name from the config root folder
                base_folder_name = config.nextcloud_folder_id.name if config.nextcloud_folder_id else nextcloud_cfg.name or "My Odoo Files"
                # full_drive_path is the complete, human-readable file path on Drive
                full_drive_path = f"{base_folder_name}/{str(model_root)}/{record_path_str}"
                # Timestamp in the user's local timezone (e.g. IST), not the server UTC clock.
                _user_tz = _pytz.timezone(self.env.user.tz or 'UTC')
                _utc_now = _dt.datetime.utcnow().replace(tzinfo=_pytz.utc)
                ts = _utc_now.astimezone(_user_tz).strftime('%Y-%m-%d %H:%M:%S')

                if config.storage_mode == 'drive':
                    details = (
                        f"Synced At: {ts}\n"
                        f"Storage Mode: Drive Only\n"
                        f"Drive Folder Path: {full_drive_path}\n"
                        f"Drive URL: {result['nextcloud_url']}"
                    )
                else:
                    details = (
                        f"Synced At: {ts}\n"
                        f"Storage Mode: Dual (Drive + Odoo)\n"
                        f"Drive Folder Path: {full_drive_path}\n"
                        f"Drive URL: {result['nextcloud_url']}\n"
                        f"Also saved in: Odoo Internal DB / Filestore"
                    )

                self.env['nextcloud.sync.log'].log_operation(
                    config=nextcloud_cfg,
                    file_name=self.name,
                    operation='upload',
                    state='success',
                    sync_type=current_sync_type,
                    # root_folder_name = the Drive root config folder (e.g. "My Odoo Files")
                    root_folder_name=base_folder_name,
                    # folder_path = full sub-path after the root (e.g. "Invoices/move_idINV/2025/0001")
                    folder_path=f"{str(model_root)}/{record_path_str}",
                    nextcloud_file_id=result['nextcloud_file_id'],
                    file_size=len(file_content) if file_content else 0,
                    sync_details=details
                )
        except Exception as e:
            # Fallback: Record remained in Odoo — log the failure so it appears in Activity Log
            import pytz as _pytz
            import datetime as _dt
            current_sync_type = self.env.context.get('sync_type', 'auto')
            record_path_str = "/".join(record_folder) if isinstance(record_folder, list) else str(record_folder)
            base_folder_name = config.nextcloud_folder_id.name if config.nextcloud_folder_id else nextcloud_cfg.name or "My Odoo Files"
            # User-timezone-aware timestamp
            _user_tz = _pytz.timezone(self.env.user.tz or 'UTC')
            ts = _dt.datetime.utcnow().replace(tzinfo=_pytz.utc).astimezone(_user_tz).strftime('%Y-%m-%d %H:%M:%S')

            self.env['nextcloud.sync.log'].log_operation(
                config=nextcloud_cfg,
                file_name=self.name,
                operation='upload',
                state='fail',
                sync_type=current_sync_type,
                root_folder_name=base_folder_name,
                folder_path=f"{str(model_root)}/{record_path_str}",
                error_message=f"[{ts}] Auto-sync failed: {str(e)}"
            )

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

    @api.model
    def _get_sync_subfolder_name(self, model, res_id):
        """Determine the 2-level folder structure for Nextcloud sync.

        Root folder name is read from nextcloud.model.config.drive_root_folder_name
        so that adding new models to the config automatically gives them the right
        folder — no hardcoded MAP needed.

        Record folder prefix is derived automatically from the model name:
            crm.lead  → 'lead_id'
            sale.order → 'order_id'
            stock.picking → 'picking_id'

        Returns: (root_name, record_path_list)
            root_name        – top-level folder (e.g. 'Sales')
            record_path_list – list of path segments for the record subfolder.
                               Names containing '/' are split into nested segments.
                               Examples:
                                 S00001        → ['order_idS00001']
                                 INV/2025/0001 → ['move_idINV', '2025', '0001']
        """
        if not model:
            return 'General', ['general']

        # ── Root folder: live lookup from nextcloud.model.config ──
        model_config = self.env['nextcloud.model.config'].sudo().search(
            [('res_model', '=', model)], limit=1
        )
        if model_config and model_config.drive_root_folder_name:
            root_name = model_config.drive_root_folder_name
        else:
            # Fallback: title-case last dotted segment  (e.g. 'mrp.production' → 'Production')
            root_name = model.split('.')[-1].replace('_', ' ').title()

        # ── Record prefix: last model segment + '_id' ──
        prefix = model.split('.')[-1] + '_id'

        record_path = []
        if res_id:
            try:
                record = self.env[model].sudo().browse(res_id)

                # Special handling: Invoice Send wizard – use the actual move name
                if model == 'account.move.send' and record.exists() \
                        and hasattr(record, 'move_ids') and record.move_ids:
                    display_name = record.move_ids[0].name
                else:
                    display_name = record.display_name if record.exists() else str(res_id)
            except Exception:
                display_name = str(res_id)

            # Split names containing '/' into nested sub-folders.
            # e.g.  INV/2025/0001  →  ['move_idINV', '2025', '0001']
            # e.g.  WH/OUT/00001   →  ['picking_idWH', 'OUT', '00001']
            if '/' in display_name:
                parts = [p.strip() for p in display_name.split('/') if p.strip()]
                if parts:
                    parts[0] = f"{prefix}{parts[0]}"
                    record_path = parts
                else:
                    record_path = [f"{prefix}Draft_{res_id}"]
            else:
                record_path = [f"{prefix}{display_name}"]
        else:
            record_path = [f"{prefix}General"]

        return root_name, record_path

    def action_sync_to_drive(self):
        """Manually sync an attachment to Nextcloud using the model's sync configuration.
        Reuses _auto_sync_to_drive to ensure consistent hierarchical folder structures.
        """
        self.ensure_one()

        # 1. Resolve the correct model for chatter attachments
        res_model = self.res_model
        res_id = self.res_id
        if res_model == 'mail.message' and res_id:
            res_model, res_id = self._resolve_mail_message_parent(res_id)

        # 2. Find the sync configuration for this model
        config = self.env['attachment.sync.config'].sudo().get_config_for_model(res_model)
        
        if config:
            # ── Case A: Model has a configuration ──
            # Reuse the robust auto-sync function. 
            # We override the storage_mode from the attachment if manually set.
            self.with_context(
                sync_type='manual',
                sync_target_model=res_model,
                sync_target_id=res_id,
                manual_storage_mode=self.storage_mode
            )._auto_sync_to_drive(config)
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('File "%s" uploaded to Nextcloud using the %s configuration.') % (self.name, config.name),
                    'type': 'success',
                    'sticky': False,
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                }
            }
        else:
            # ── Case B: No model configuration (Standalone upload) ──
            # Fallback to simple upload using the Drive/Folder manually selected on the attachment.
            if not self.nextcloud_id:
                active_drives = self.env['nextcloud.config'].sudo().search([('active', '=', True)], limit=1)
                if active_drives:
                    self.nextcloud_id = active_drives.id
                else:
                    raise UserError(_("Please select a Nextcloud first or ensure at least one Nextcloud is configured and active."))

            sync = self.env['nextcloud.sync'].sudo()
            import datetime as _dt
            import base64

            try:
                # Determine parent folder ID
                parent_nextcloud_id = False
                if self.nextcloud_folder_id and self.nextcloud_folder_id.nextcloud_file_id:
                    parent_nextcloud_id = self.nextcloud_folder_id.nextcloud_file_id
                elif self.nextcloud_id.root_ids:
                    root = self.nextcloud_id.root_ids.filtered(lambda r: r.active)[:1]
                    parent_nextcloud_id = root.root_id if root else False

                file_content = base64.b64decode(self.datas) if self.datas else self.raw
                if not file_content:
                    raise UserError(_("This attachment has no file content to upload."))

                result = sync.upload_file_to_drive(
                    self.name,
                    file_content,
                    self.mimetype or 'application/octet-stream',
                    self.nextcloud_id,
                    parent_nextcloud_id=parent_nextcloud_id
                )

                if result:
                    self.with_context(skip_nextcloud_sync=True).write({
                        'nextcloud_file_id': result['nextcloud_file_id'],
                        'nextcloud_id': self.nextcloud_id.id,
                        'nextcloud_folder_id': self.nextcloud_folder_id.id,
                    })
                    
                    # Log as manual upload
                    self.env['nextcloud.sync.log'].log_operation(
                        config=self.nextcloud_id,
                        file_name=self.name,
                        operation='upload',
                        state='success',
                        sync_type='manual',
                        nextcloud_file_id=result['nextcloud_file_id'],
                        file_size=len(file_content),
                        sync_details=f"Manual Standalone Upload\nDrive URL: {result.get('nextcloud_url', '')}"
                    )

                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Success'),
                            'message': _('File "%s" uploaded to Nextcloud successfully.') % self.name,
                            'type': 'success',
                            'sticky': False,
                        }
                    }
                else:
                    raise UserError(_("Failed to upload file to Nextcloud."))
            except Exception as e:
                raise UserError(_("Error during upload: %s") % str(e))
