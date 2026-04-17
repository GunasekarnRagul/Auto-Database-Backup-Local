# -*- coding: utf-8 -*-
from odoo import models, fields, api

class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    google_file_id = fields.Char('Google File ID', index=True)
    google_drive_id = fields.Many2one('google.drive.config', string='Drive')
    google_folder_id = fields.Many2one('google.drive.file', string='Folder', domain="[('file_type', '=', 'folder')]" )
    model_name = fields.Selection(selection='_get_model_selection', string='Model Name', index=True, help="Technical name of the Odoo model")
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

    @api.model
    def _get_model_selection(self):
        """Dynamic selection of models from gdrive.model.config."""
        configs = self.env['gdrive.model.config'].sudo().search([])
        return [(c.res_model, c.model_label) for c in configs]

    @api.depends('model_name', 'res_model')
    def _compute_model_display(self):
        """Get friendly display name for the model using gdrive.model.config."""
        for attachment in self:
            res_model = attachment.model_name or attachment.res_model
            if not res_model:
                attachment.model_display = False
                continue
            
            config = self.env['gdrive.model.config'].sudo().search([
                ('res_model', '=', res_model)
            ], limit=1)
            attachment.model_display = config.model_label if config else res_model

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
        
        # Auto-sync logic: check each new attachment against configured models
        for attachment in attachments:
            if self.env.context.get('skip_gdrive_sync'):
                continue
                
            res_model = attachment.res_model
            res_id = attachment.res_id
            
            # Resolve "Effective Model" for mail wizards/messages
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
                elif not res_id:
                    # Report created for composer with ID 0; fall back to context
                    ctx_model = self.env.context.get('active_model') or self.env.context.get('default_model')
                    ctx_id = self.env.context.get('active_id') or self.env.context.get('default_res_id') or (self.env.context.get('default_res_ids') or [0])[0]
                    if ctx_model:
                        res_model = ctx_model
                        res_id = ctx_id

            elif res_model == 'mail.message' and res_id:
                msg = self.env['mail.message'].sudo().browse(res_id)
                if msg.exists() and msg.model:
                    res_model = msg.model
                    res_id = msg.res_id

            # Step 1: Check if model is configured in Attachment Sync Configuration
            config = self.env['attachment.sync.config'].sudo().get_config_for_model(res_model)
            if not config or config.storage_mode == 'odoo' or not config.auto_sync_mode:
                continue

            # Step 2: Check file type filter from config
            if not self._matches_file_type_filter(attachment, config):
                continue

            if not attachment.datas and not attachment.raw:
                continue

            # Step 3: Trigger sync with resolved model/id for correct folder structure
            attachment.with_context(
                sync_target_model=res_model, 
                sync_target_id=res_id
            )._auto_sync_to_drive(config)
            
        return attachments

    def write(self, vals):
        # Handle model_name → res_model mapping
        if vals.get('model_name') and not vals.get('res_model'):
            vals['res_model'] = vals['model_name']

        res = super(IrAttachment, self).write(vals)
        
        if self.env.context.get('skip_gdrive_sync'):
            return res
            
        # Catch attachments re-linking to their final model OR obtaining data late
        trigger_fields = {'res_model', 'res_id', 'datas', 'raw', 'db_datas'}
        if any(f in vals for f in trigger_fields):
            for attachment in self:
                if attachment.google_file_id:
                    continue  # Already synced

                config = self.env['attachment.sync.config'].sudo().get_config_for_model(attachment.res_model)
                if not config or not config.auto_sync_mode:
                    continue

                if not self._matches_file_type_filter(attachment, config):
                    continue
                
                if not attachment.datas and not attachment.raw:
                    continue
                
                attachment._auto_sync_to_drive(config)
                
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
        """Automated sync to drive with storage mode handling (Drive only vs Dual)."""
        self.ensure_one()
        sync_service = self.env['google.drive.sync'].sudo()
        
        # Determine the target parent folder
        parent_id = config.google_folder_id.google_file_id
        if not parent_id and config.google_drive_id.root_ids:
             root = config.google_drive_id.root_ids.filtered(lambda r: r.active)[:1]
             parent_id = root.root_id if root else False

        # Check if it is an email attachment
        is_email = False
        if 'mail.message' in self.env:
            is_email = bool(self.env['mail.message'].search_count([('attachment_ids', 'in', self.id)]))

        # Apply Dynamic Folder Structure Logic
        res_model = self.env.context.get('sync_target_model') or self.res_model
        res_id = self.env.context.get('sync_target_id') or self.res_id
        
        model_root, record_folder, model_sub = self._get_sync_subfolder_name(
            res_model, res_id, self.mimetype, self.name, is_email
        )
        
        current_local_parent_id = config.google_folder_id.id
        current_drive_parent_id = parent_id

        def get_or_create_local_folder(f_name, parent_local, config_drive_id, gdrive_id, gdrive_url):
            local_folder = self.env['google.drive.file'].sudo().search([
                ('name', '=', f_name),
                ('parent_folder_id', '=', parent_local),
                ('file_type', '=', 'folder')
            ], limit=1)
            if not local_folder:
                local_folder = self.env['google.drive.file'].sudo().create({
                    'name': f_name,
                    'file_type': 'folder',
                    'drive_config_id': config_drive_id,
                    'parent_folder_id': parent_local,
                    'google_file_id': gdrive_id,
                    'google_url': gdrive_url,
                    'sync_state': 'synced',
                    'owner_name': self.env.user.name,
                })
            return local_folder.id
        
        if model_root and record_folder and current_drive_parent_id:
            # 1. Find or create Model Root Folder (e.g. Sales)
            root_res = sync_service.find_or_create_folder(model_root, current_drive_parent_id, config.google_drive_id)
            if root_res:
                current_local_parent_id = get_or_create_local_folder(
                    model_root, current_local_parent_id, config.google_drive_id.id, 
                    root_res['google_file_id'], root_res.get('google_url')
                )
                
                # 2. Find or create Record Folder (e.g. order_idS00009)
                record_res = sync_service.find_or_create_folder(record_folder, root_res['google_file_id'], config.google_drive_id)
                if record_res:
                    current_local_parent_id = get_or_create_local_folder(
                        record_folder, current_local_parent_id, config.google_drive_id.id, 
                        record_res['google_file_id'], record_res.get('google_url')
                    )
                    
                    # 3. Find or create Category Subfolder (e.g. quotations)
                    sub_res = sync_service.find_or_create_folder(model_sub, record_res['google_file_id'], config.google_drive_id)
                    if sub_res:
                        current_local_parent_id = get_or_create_local_folder(
                            model_sub, current_local_parent_id, config.google_drive_id.id, 
                            sub_res['google_file_id'], sub_res.get('google_url')
                        )
                        parent_id = sub_res['google_file_id']

        # Attempt Upload
        try:
            result = sync_service.upload_file_to_drive(
                self.name,
                self.raw,
                self.mimetype or 'application/octet-stream',
                config.google_drive_id,
                parent_gdrive_id=parent_id
            )

            if result:
                vals = {
                    'google_file_id': result['google_file_id'],
                    'google_drive_id': config.google_drive_id.id,
                    'google_folder_id': config.google_folder_id.id,
                    'sync_type': 'external',
                }
                
                # Handle "Drive only" mode
                if config.storage_mode == 'drive':
                    vals.update({
                        'type': 'url',
                        'url': result['google_url'],
                        'datas': False, # Remove local binary
                        'db_datas': False,
                    })
                
                self.with_context(skip_gdrive_sync=True).write(vals)

                # Update sync count on config
                config.write({
                    'sync_count': config.sync_count + 1,
                    'last_synced': fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                })
                
                # Register in explorer
                self.env['google.drive.file'].sudo().create({
                    'name': self.name,
                    'drive_config_id': config.google_drive_id.id,
                    'parent_folder_id': current_local_parent_id,
                    'file_type': 'file',
                    'mime_type': self.mimetype or 'application/octet-stream',
                    'file_size': len(self.raw) if self.raw else 0,
                    'google_file_id': result['google_file_id'],
                    'google_url': result['google_url'],
                    'owner_name': self.env.user.name,
                    'sync_state': 'synced',
                    'last_synced': fields.Datetime.now(),
                })
                
                # Detailed success logging for both auto and manual syncs
                current_sync_type = self.env.context.get('sync_type', 'auto')
                full_drive_path = f"{model_root}/{record_folder}/{model_sub}"
                if config.storage_mode == 'drive':
                    details = f"Storage Mode: Drive Only\nSaved to Drive folder path: {full_drive_path}\nDrive URL: {result['google_url']}"
                else:
                    details = f"Storage Mode: Dual\nSaved to Drive folder path: {full_drive_path}\nDrive URL: {result['google_url']}\nSaved internally in: Odoo Internal DB/Filestore"
                    
                self.env['google.drive.sync.log'].log_operation(
                    config=config.google_drive_id,
                    file_name=self.name,
                    operation='upload',
                    state='success',
                    sync_type=current_sync_type,
                    folder_path=full_drive_path,
                    google_file_id=result['google_file_id'],
                    file_size=len(self.raw) if self.raw else 0,
                    sync_details=details
                )
        except Exception as e:
            # Fallback: Record remained in Odoo, just log failure
            self.env['google.drive.sync.log'].log_operation(
                config=config.google_drive_id,
                file_name=self.name,
                operation='upload',
                state='fail',
                error_message=f"Auto-sync failed: {str(e)}"
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
    def _get_sync_subfolder_name(self, model, res_id, mimetype, name, is_email):
        """Determine the 3-level folder structure based on model and attachment attributes.
        Returns: (root_name, record_name, category_name)
        """
        MAP = {
            'crm.lead':            { 'root': 'CRM',        'prefix': 'lead_id',     'sub': ['documents', 'images', 'Email_attachments'] },
            'sale.order':          { 'root': 'Sales',      'prefix': 'order_id',    'sub': ['quotations', 'customer_files', 'Email_attachments'] },
            'account.move':        { 'root': 'Accounting', 'prefix': 'move_id',     'sub': ['invoices', 'vendor_bills', 'tax_docs'] },
            'purchase.order':      { 'root': 'Purchase',   'prefix': 'order_id',    'sub': ['vendor_docs', 'rfq', 'contracts'] },
            'hr.employee':         { 'root': 'HR',         'prefix': 'employee_id', 'sub': ['certificates', 'id_docs', 'contracts'] },
            'project.task':        { 'root': 'Project',    'prefix': 'task_id',     'sub': ['task_files', 'references', 'deliverables'] },
            'stock.picking':       { 'root': 'Inventory',  'prefix': 'picking_id',  'sub': ['delivery_notes', 'packing_lists', 'customs_docs'] },
            'helpdesk.ticket':     { 'root': 'Helpdesk',   'prefix': 'ticket_id',   'sub': ['screenshots', 'customer_files', 'resolution_docs'] },
        }

        if model not in MAP:
            return False, False, False

        root_name = MAP[model]['root']
        subs = MAP[model]['sub']
        
        if res_id:
            try:
                record = self.env[model].sudo().browse(res_id)
                display_name = record.display_name if record.exists() else str(res_id)
            except Exception:
                display_name = str(res_id)
            
            # Use format e.g., order_idS00009
            record_name = f"{MAP[model]['prefix']}{display_name}"
        else:
            record_name = f"{MAP[model]['prefix']}General"

        # 1. Email Attachments
        if is_email and 'Email_attachments' in subs:
            return root_name, record_name, 'Email_attachments'
            
        # 2. PDF specific categorizations
        is_pdf = mimetype == 'application/pdf'
        if is_pdf:
            if model == 'sale.order' and 'quotations' in subs:
                return root_name, record_name, 'quotations'
            if model == 'crm.lead' and 'documents' in subs:
                return root_name, record_name, 'documents'
            if model == 'account.move' and ('invoices' in subs):
                return root_name, record_name, 'invoices'
            if model == 'purchase.order' and 'rfq' in subs:
                return root_name, record_name, 'rfq'
            if model == 'hr.employee' and 'contracts' in subs:
                return root_name, record_name, 'contracts'
                
        # 3. Name based categorizations
        lower_name = (name or '').lower()
        if model == 'hr.employee':
            if 'contract' in lower_name and 'contracts' in subs:
                return root_name, record_name, 'contracts'
            if ('id' in lower_name or 'passport' in lower_name) and 'id_docs' in subs:
                return root_name, record_name, 'id_docs'
                
        if model == 'stock.picking':
            if 'packing' in lower_name and 'packing_lists' in subs:
                return root_name, record_name, 'packing_lists'
            if 'customs' in lower_name and 'customs_docs' in subs:
                return root_name, record_name, 'customs_docs'

        # 4. Image categorizations
        is_image = mimetype and mimetype.startswith('image/')
        if model == 'crm.lead' and is_image and 'images' in subs:
            return root_name, record_name, 'images'
        if model == 'helpdesk.ticket' and is_image and 'screenshots' in subs:
            return root_name, record_name, 'screenshots'

        # 5. Default fallbacks
        fallback = subs[1] if len(subs) > 1 else subs[0]
        if model == 'sale.order':
             fallback = 'customer_files'
        elif model == 'crm.lead':
             fallback = 'documents'
        elif model == 'account.move':
             fallback = 'vendor_bills'
        elif model == 'purchase.order':
             fallback = 'vendor_docs'
        elif model == 'project.task':
             fallback = 'task_files'
        elif model == 'helpdesk.ticket':
             fallback = 'customer_files'
        elif model == 'hr.employee':
             fallback = 'certificates'
        elif model == 'stock.picking':
             fallback = 'delivery_notes'
             
        return root_name, record_name, fallback

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
