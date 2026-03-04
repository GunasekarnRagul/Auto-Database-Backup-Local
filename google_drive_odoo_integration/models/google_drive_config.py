from odoo import models, fields, api, _
from odoo.http import request
from odoo.exceptions import ValidationError

class GoogleDriveRootFolder(models.Model):
    _name = 'google.drive.root.folder'
    _description = 'Google Drive Root Folder'

    name = fields.Char('Folder Name', required=True)
    root_id = fields.Char('Google Folder ID', required=True)
    config_id = fields.Many2one('google.drive.config', string='Drive Configuration', ondelete='cascade')
    active = fields.Boolean('Active', default=True)

    def unlink(self):
        # Clear files for this root
        self.env['google.drive.file'].sudo().search([
            ('root_folder_id', 'in', self.ids)
        ]).unlink()
        return super(GoogleDriveRootFolder, self).unlink()

    def write(self, vals):
        if 'root_id' in vals:
            for root in self:
                if vals.get('root_id') != root.root_id:
                    self.env['google.drive.file'].sudo().search([
                        ('root_folder_id', '=', root.id)
                    ]).unlink()
        return super(GoogleDriveRootFolder, self).write(vals)

class GoogleDriveConfig(models.Model):
    _name = 'google.drive.config'
    _description = 'Google Drive Configuration'

    name = fields.Char('Drive Name', required=True, default='Local Drive')
    drive_type = fields.Selection([
        ('gdrive', 'Google Drive'),
    ], string='Drive Type', default='gdrive', required=True)
    readonly = fields.Boolean('Readonly')
    active = fields.Boolean('Active', default=True)
    state = fields.Selection([
        ('draft', 'Not Connected'),
        ('connected', 'Connected'),
        ('error', 'Error'),
    ], string='Status', compute='_compute_state', store=True, default='draft')
    
    # Authorization code fields
    client_id = fields.Char('Client ID')
    client_secret = fields.Char('Client Secret')
    redirect_uri = fields.Char('Redirect URI', compute='_compute_redirect_uri')
    refresh_token = fields.Char('Refresh Token')
    
    drive_id = fields.Char('Drive ID?')
    root_ids = fields.One2many('google.drive.root.folder', 'config_id', string='Root Folders')
    
    group_ids = fields.Many2many('res.groups', string='Groups with access')
    user_ids = fields.Many2many('res.users', string='Users with access')

    _sql_constraints = [
        ('client_id_unique', 'unique(client_id)', 'This Client ID is already in use. Each configuration must have a unique Client ID!')
    ]

    @api.constrains('client_id')
    def _check_unique_client_id(self):
        for record in self:
            if record.client_id:
                duplicate = self.search([
                    ('client_id', '=', record.client_id),
                    ('id', '!=', record.id)
                ])
                if duplicate:
                    raise models.ValidationError("This Client ID is already configured in another storage record: %s" % duplicate[0].name)

    @api.depends('refresh_token', 'root_ids')
    def _compute_state(self):
        for config in self:
            config.state = 'connected' if config.refresh_token and config.root_ids else 'draft'

    @api.depends('client_id')
    def _compute_redirect_uri(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for config in self:
            host_url = base_url or ''
            try:
                # If running in a web request context, prefer the request host when the
                # configured base_url looks like a local development URL (localhost/127.0.0.1)
                if host_url and ("localhost" in host_url or "127.0.0.1" in host_url or ":8069" in host_url):
                    req_root = request.httprequest.url_root.rstrip('/')
                    if req_root:
                        host_url = req_root
            except Exception:
                # request may not be available in some contexts; fall back to base_url
                host_url = host_url

            config.redirect_uri = f"{host_url}/google_account/authentication"

    def compute_redirect_from_request(self):
        """Button/action to set the redirect URI from the current HTTP request host.

        This is useful when the stored `web.base.url` is a local value (like
        http://localhost:8069) and you want to detect the publicly accessible
        host (scheme + domain) from the browser request.
        """
        for config in self:
            try:
                host_root = request.httprequest.url_root.rstrip('/')
            except Exception:
                host_root = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''

            redirect = f"{host_root}/google_account/authentication"
            config.write({'redirect_uri': redirect})
        return True

    def action_authenticate(self):
        self.ensure_one()
        if not self.client_id:
            return False
            
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'https://www.googleapis.com/auth/drive',
            'access_type': 'offline',
            'prompt': 'consent',
            'state': str(self.id), # Pass ID to callback
        }
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + "&".join([f"{k}={v}" for k, v in params.items()])
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def action_sync_all(self):
        self.ensure_one()
        for root in self.root_ids.filtered(lambda r: r.active):
            self.env['google.drive.sync'].sudo()._sync_config_files(
                self, root_folder_id=root.id, gdrive_parent_id=root.root_id
            )
        return True

    def action_configure_roots(self):
        """Open a popup to manage root folders for this configuration."""
        self.ensure_one()
        return {
            'name': _('Manage Root Folders'),
            'type': 'ir.actions.act_window',
            'res_model': 'google.drive.root.folder',
            'view_mode': 'tree',
            'domain': [('config_id', '=', self.id)],
            'context': {'default_config_id': self.id},
            'target': 'new',
        }

    @api.model
    def action_trigger_sync(self, root_folder_id=None):
        """Trigger sync for active configs. Called from file explorer JS.
        If root_folder_id is provided, only sync that specific root.
        
        Flow: 1) Push pending local items to Drive  2) Pull from Drive
        """
        # Step 1: Push any pending folders/files to Google Drive first
        self.env['google.drive.file'].sudo().sync_pending_to_drive()

        # Step 2: Pull from Drive (backward sync)
        if root_folder_id:
            root = self.env['google.drive.root.folder'].sudo().browse(root_folder_id)
            if root.exists() and root.active and root.config_id.active:
                self.env['google.drive.sync'].sudo()._sync_config_files(
                    root.config_id, root_folder_id=root.id, gdrive_parent_id=root.root_id
                )
            return True

        configs = self.search([('active', '=', True)])
        for config in configs:
            for root in config.root_ids.filtered(lambda r: r.active):
                self.env['google.drive.sync'].sudo()._sync_config_files(
                    config, root_folder_id=root.id, gdrive_parent_id=root.root_id
                )
        return True

