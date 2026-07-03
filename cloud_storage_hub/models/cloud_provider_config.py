# -*- coding: utf-8 -*-
import re
import logging
import requests as http_requests
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

# ── AWS credential validation patterns ─────────────────────────────────────────
_AWS_KEY_ID_RE = re.compile(r'^(AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}$')
_AWS_REGION_RE = re.compile(r'^[a-z]{2}-[a-z]+-\d+$')
_S3_BUCKET_RE  = re.compile(r'^(?!-)(?!.*--)[a-z0-9][a-z0-9\-]{1,61}[a-z0-9]$')

PROVIDER_LABELS = {
    'aws':       'AWS S3',
    'gdrive':    'Google Drive',
    'onedrive':  'Microsoft OneDrive',
    'nextcloud': 'Nextcloud',
    'dropbox':   'Dropbox',
}


class CloudRootFolder(models.Model):
    _name = 'cloud.root.folder'
    _description = 'Cloud Storage Root Folder'

    name = fields.Char('Folder Name', required=True)
    # The remote ID / path varies by provider:
    # - AWS S3: S3 key prefix (path)
    # - Google Drive / OneDrive: folder ID
    # - Nextcloud: WebDAV path
    # - Dropbox: path_lower
    root_id = fields.Char('Remote Folder ID / Path', readonly=True)
    config_id = fields.Many2one('cloud.provider.config', string='Provider Config', ondelete='cascade')
    active = fields.Boolean('Active', default=True)

    _sql_constraints = [
        ('name_config_unique', 'unique(name, config_id)',
         'A folder with this name is already configured for this provider!')
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            config = record.config_id
            if config and config.state == 'connected' and not record.root_id:
                try:
                    engine = self.env['cloud.sync.engine'].sudo()._get_engine(config.provider_type)
                    if engine and hasattr(engine, 'find_or_create_folder'):
                        result = engine.find_or_create_folder(record.name, '/', config)
                        if result and result.get('remote_id'):
                            record.write({'root_id': result['remote_id']})
                except Exception as e:
                    _logger.warning("Could not auto-create remote folder '%s': %s", record.name, e)
        return records

    def unlink(self):
        self.env['cloud.file'].sudo().search([('root_folder_id', 'in', self.ids)]).unlink()
        return super().unlink()

    def write(self, vals):
        if 'root_id' in vals:
            for root in self:
                if vals.get('root_id') != root.root_id:
                    self.env['cloud.file'].sudo().search([('root_folder_id', '=', root.id)]).unlink()
        return super().write(vals)


class CloudProviderConfig(models.Model):
    _name = 'cloud.provider.config'
    _description = 'Cloud Provider Configuration'

    # ── Identity ────────────────────────────────────────────────────────────────
    name = fields.Char('Configuration Name', required=True, default='My Cloud Drive')
    provider_type = fields.Selection([
        ('aws',       'AWS S3'),
        ('gdrive',    'Google Drive'),
        ('onedrive',  'Microsoft OneDrive'),
        ('nextcloud', 'Nextcloud'),
        ('dropbox',   'Dropbox'),
    ], string='Cloud Provider', required=True, default='aws')
    active = fields.Boolean('Active', default=True)

    # ── Connection state ────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft',     'Not Connected'),
        ('connected', 'Connected'),
        ('error',     'Error'),
    ], string='Status', compute='_compute_state', store=True, default='draft')
    is_connected = fields.Boolean('Credentials Verified', default=False, copy=False)

    # ── OAuth token (Google Drive, OneDrive, Dropbox) ──────────────────────────
    client_id     = fields.Char('App / Client ID',
                                help='Google Client ID, Microsoft Application ID, or Dropbox App Key')
    client_secret = fields.Char('App / Client Secret',
                                help='Google Client Secret, Microsoft Client Secret, or Dropbox App Secret')
    refresh_token = fields.Char('Refresh Token', copy=False)
    redirect_uri  = fields.Char('Redirect URI', compute='_compute_redirect_uri', store=False)

    # ── AWS S3 credentials ─────────────────────────────────────────────────────
    aws_access_key_id     = fields.Char('AWS Access Key ID')
    aws_secret_access_key = fields.Char('AWS Secret Access Key')
    aws_region            = fields.Char('AWS Region', default='us-east-1')
    aws_s3_bucket_name    = fields.Char('S3 Bucket Name')
    share_link_expiry_days = fields.Integer('Share Link Expiry (Days)', default=7,
                                            help='How many days a presigned URL stays valid (max 7).')

    # ── Nextcloud credentials ──────────────────────────────────────────────────
    nextcloud_url     = fields.Char('Nextcloud Server URL',
                                    help='e.g. https://cloud.yourcompany.com')
    nc_username       = fields.Char('Nextcloud Username')
    nc_app_password   = fields.Char('Nextcloud App Password')

    # ── Shared Drive / Namespace ───────────────────────────────────────────────
    shared_drive_id = fields.Char('Team Drive / Namespace ID',
                                  help='Google Shared Drive ID, Dropbox team namespace, etc. Leave blank for personal.')

    # ── Root folders ────────────────────────────────────────────────────────────
    root_ids          = fields.One2many('cloud.root.folder', 'config_id', string='Root Folders')
    root_folder_count = fields.Integer('Root Folders', compute='_compute_counts')

    # ── Computed provider label ─────────────────────────────────────────────────
    provider_label = fields.Char('Provider', compute='_compute_provider_label')

    # ─────────────────────────────────────────────────────────────────────────
    # Computed fields
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_counts(self):
        for cfg in self:
            cfg.root_folder_count = len(cfg.root_ids)

    @api.depends('provider_type')
    def _compute_provider_label(self):
        for cfg in self:
            cfg.provider_label = PROVIDER_LABELS.get(cfg.provider_type, cfg.provider_type)

    @api.depends('is_connected', 'refresh_token')
    def _compute_state(self):
        for cfg in self:
            if cfg.provider_type in ('aws', 'nextcloud'):
                cfg.state = 'connected' if cfg.is_connected else 'draft'
            else:
                cfg.state = 'connected' if cfg.refresh_token else 'draft'

    @api.depends('provider_type', 'client_id')
    def _compute_redirect_uri(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        for cfg in self:
            try:
                host_url = base_url
                if host_url and ('localhost' in host_url or '127.0.0.1' in host_url or ':8069' in host_url):
                    req_root = request.httprequest.url_root.rstrip('/')
                    if req_root:
                        host_url = req_root
            except Exception:
                host_url = base_url

            route_map = {
                'gdrive':   '/cloud_hub/gdrive/callback',
                'onedrive': '/cloud_hub/onedrive/callback',
                'dropbox':  '/cloud_hub/dropbox/callback',
            }
            route = route_map.get(cfg.provider_type, '/cloud_hub/oauth/callback')
            cfg.redirect_uri = f'{host_url}{route}'

    # ─────────────────────────────────────────────────────────────────────────
    # Constraints
    # ─────────────────────────────────────────────────────────────────────────

    @api.constrains('aws_access_key_id', 'provider_type')
    def _check_unique_aws_key(self):
        for rec in self:
            if rec.provider_type == 'aws' and rec.aws_access_key_id:
                dup = self.search([
                    ('provider_type', '=', 'aws'),
                    ('aws_access_key_id', '=', rec.aws_access_key_id),
                    ('id', '!=', rec.id),
                ])
                if dup:
                    raise ValidationError(
                        _('This AWS Access Key ID is already configured in: %s') % dup[0].name
                    )

    @api.constrains('client_id', 'provider_type')
    def _check_unique_client_id(self):
        for rec in self:
            if rec.provider_type in ('gdrive', 'onedrive', 'dropbox') and rec.client_id:
                dup = self.search([
                    ('provider_type', '=', rec.provider_type),
                    ('client_id', '=', rec.client_id),
                    ('id', '!=', rec.id),
                ])
                if dup:
                    raise ValidationError(
                        _('This Client ID is already configured in: %s') % dup[0].name
                    )

    # ─────────────────────────────────────────────────────────────────────────
    # Authentication Actions
    # ─────────────────────────────────────────────────────────────────────────

    def action_authenticate(self):
        """Dispatch to the correct authentication method based on provider_type."""
        self.ensure_one()
        method = f'_authenticate_{self.provider_type}'
        if hasattr(self, method):
            return getattr(self, method)()
        raise UserError(_('Authentication not implemented for provider: %s') % self.provider_type)

    def _authenticate_aws(self):
        """Validate AWS S3 credentials using boto3."""
        access_key  = (self.aws_access_key_id or '').strip()
        secret_key  = (self.aws_secret_access_key or '').strip()
        region      = (self.aws_region or '').strip()
        bucket_name = (self.aws_s3_bucket_name or '').strip()

        if not access_key:
            raise UserError(_('AWS Access Key ID is required.'))
        if not secret_key:
            raise UserError(_('AWS Secret Access Key is required.'))
        if not region:
            raise UserError(_('AWS Region is required (e.g. us-east-1).'))
        if not bucket_name:
            raise UserError(_('S3 Bucket Name is required.'))

        if not _AWS_KEY_ID_RE.match(access_key):
            raise UserError(_('Invalid AWS Access Key ID: must be 20 chars starting with AKIA/ABIA/ACCA/ASIA.'))
        if len(secret_key) < 40:
            raise UserError(_('Invalid AWS Secret Access Key: must be at least 40 characters.'))
        if not _AWS_REGION_RE.match(region):
            raise UserError(_('Invalid AWS Region format. Example: us-east-1, eu-north-1'))
        if len(bucket_name) < 3 or len(bucket_name) > 63 or not _S3_BUCKET_RE.match(bucket_name):
            raise UserError(_('Invalid S3 Bucket Name. Must be 3–63 chars, lowercase, no consecutive hyphens.'))

        try:
            import boto3
            from botocore.exceptions import ClientError, NoCredentialsError
        except ImportError:
            raise UserError(_("The 'boto3' library is not installed. Run: pip install boto3"))

        try:
            s3 = boto3.client('s3', aws_access_key_id=access_key,
                              aws_secret_access_key=secret_key, region_name=region)
            s3.head_bucket(Bucket=bucket_name)
        except Exception as e:
            self.sudo().write({'is_connected': False})
            err = str(e)
            if '403' in err or 'Forbidden' in err:
                raise UserError(_('Access Denied (403). Check IAM policy for s3:HeadBucket.'))
            if '404' in err or 'NoSuchBucket' in err:
                raise UserError(_("Bucket '%s' not found in region '%s'.") % (bucket_name, region))
            raise UserError(_('AWS S3 connection failed: %s') % err)

        self.sudo().write({'is_connected': True})
        return self._return_form_action('Connected to AWS S3 successfully!')

    def _authenticate_gdrive(self):
        """Redirect to Google OAuth consent screen."""
        if not self.client_id:
            raise UserError(_('Please enter your Google Client ID first.'))
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'https://www.googleapis.com/auth/drive',
            'access_type': 'offline',
            'prompt': 'consent',
            'state': str(self.id),
        }
        url = 'https://accounts.google.com/o/oauth2/v2/auth?' + '&'.join([f'{k}={v}' for k, v in params.items()])
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def _authenticate_onedrive(self):
        """Redirect to Microsoft OAuth consent screen."""
        if not self.client_id:
            raise UserError(_('Please enter your Microsoft Application (Client) ID first.'))
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'Files.ReadWrite.All offline_access',
            'prompt': 'consent',
            'state': str(self.id),
        }
        url = 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?' + '&'.join([f'{k}={v}' for k, v in params.items()])
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def _authenticate_nextcloud(self):
        """Validate Nextcloud credentials via WebDAV PROPFIND."""
        base_url = (self.nextcloud_url or '').strip().rstrip('/')
        username = (self.nc_username or '').strip()
        password = (self.nc_app_password or '').strip()

        if not base_url or base_url in ('https://', 'http://'):
            raise UserError(_('Please enter your Nextcloud Server URL.'))
        if not username:
            raise UserError(_('Please enter your Nextcloud Username.'))
        if not password:
            raise UserError(_('Please enter your Nextcloud App Password.'))

        webdav_url = f'{base_url}/remote.php/webdav'
        try:
            resp = http_requests.request('PROPFIND', webdav_url, auth=(username, password),
                                         headers={'Depth': '0'}, timeout=15)
        except http_requests.exceptions.ConnectionError:
            raise UserError(_('Cannot reach Nextcloud at: %s\nPlease check the Server URL.') % base_url)
        except http_requests.exceptions.Timeout:
            raise UserError(_('Connection to Nextcloud timed out. Check your network.'))
        except Exception as e:
            raise UserError(_('Unexpected error: %s') % str(e))

        if resp.status_code in (200, 207):
            self.sudo().write({'is_connected': True})
            return self._return_form_action('Connected to Nextcloud successfully!')
        elif resp.status_code == 401:
            self.sudo().write({'is_connected': False})
            raise UserError(_('Invalid credentials (HTTP 401). Check your username and app password.'))
        elif resp.status_code == 403:
            self.sudo().write({'is_connected': False})
            raise UserError(_('Access denied (HTTP 403). Check WebDAV permissions.'))
        else:
            self.sudo().write({'is_connected': False})
            raise UserError(_('Connection failed (HTTP %s). Verify your Server URL, username, and password.') % resp.status_code)

    def _authenticate_dropbox(self):
        """Redirect to Dropbox OAuth consent screen."""
        if not self.client_id:
            raise UserError(_('Please enter your Dropbox App Key first.'))
        scopes = ' '.join([
            'account_info.read', 'files.content.read', 'files.content.write',
            'files.metadata.read', 'files.metadata.write', 'sharing.read', 'sharing.write',
        ])
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'token_access_type': 'offline',
            'scope': scopes,
            'state': str(self.id),
        }
        url = 'https://www.dropbox.com/oauth2/authorize?' + '&'.join([f'{k}={v}' for k, v in params.items()])
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def _return_form_action(self, _msg=None):
        """Return an action to reopen this form."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'cloud.provider.config',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def action_disconnect(self):
        """Clear OAuth tokens and mark as disconnected."""
        self.ensure_one()
        self.sudo().write({'is_connected': False, 'refresh_token': False})
        return self._return_form_action()

    # ─────────────────────────────────────────────────────────────────────────
    # Sync Actions
    # ─────────────────────────────────────────────────────────────────────────

    def action_sync_all(self):
        """Sync all active root folders for this config."""
        self.ensure_one()
        for root in self.root_ids.filtered(lambda r: r.active):
            try:
                engine = self.env['cloud.sync.engine'].sudo()._get_engine(self.provider_type)
                result = engine.find_or_create_folder(root.name, '/', self)
                if result and result.get('remote_id'):
                    remote_id = result['remote_id']
                    if root.root_id != remote_id:
                        root.sudo().write({'root_id': remote_id})
                else:
                    remote_id = root.root_id or ('/' + root.name)
                engine.with_context(sync_type='manual')._sync_config_files(
                    self, root_folder_id=root.id, remote_parent_id=remote_id
                )
            except Exception as e:
                _logger.error("Sync error for root folder '%s': %s", root.name, e)
        return True

    def action_configure_roots(self):
        self.ensure_one()
        return {
            'name': _('Manage Root Folders'),
            'type': 'ir.actions.act_window',
            'res_model': 'cloud.root.folder',
            'view_mode': 'list',
            'domain': [('config_id', '=', self.id)],
            'context': {'default_config_id': self.id},
            'target': 'new',
        }

    def action_bulk_add_folders(self):
        self.ensure_one()
        return {
            'name': _('Bulk Add Folders'),
            'type': 'ir.actions.act_window',
            'res_model': 'cloud.folder.wizard',
            'view_mode': 'form',
            'context': {'default_config_id': self.id},
            'target': 'new',
        }

    @api.model
    def action_trigger_sync(self, root_folder_id=None, drive_config_id=None):
        """Full bi-directional sync: push Odoo → Cloud, then pull Cloud → Odoo."""
        # Step 1: Push pending files to cloud
        self.env['cloud.file'].sudo().with_context(sync_type='manual').sync_pending_to_drive(
            drive_config_id=drive_config_id
        )
        # Step 2: Pull from cloud
        if root_folder_id:
            root = self.env['cloud.root.folder'].sudo().browse(root_folder_id)
            if root.exists() and root.active and root.config_id.active:
                engine = self.env['cloud.sync.engine'].sudo()._get_engine(root.config_id.provider_type)
                engine.with_context(sync_type='manual')._sync_config_files(
                    root.config_id, root_folder_id=root.id, remote_parent_id=root.root_id
                )
            return True

        domain = [('active', '=', True)]
        if drive_config_id:
            domain.append(('id', '=', drive_config_id))
        for config in self.search(domain):
            for root in config.root_ids.filtered(lambda r: r.active):
                try:
                    engine = self.env['cloud.sync.engine'].sudo()._get_engine(config.provider_type)
                    engine.with_context(sync_type='manual')._sync_config_files(
                        config, root_folder_id=root.id, remote_parent_id=root.root_id
                    )
                except Exception as e:
                    _logger.error("Sync error for '%s' / '%s': %s", config.name, root.name, e)
        return True

    @api.model
    def action_auto_sync_drive(self, drive_config_id=None):
        """One-way auto-sync: push pending files to cloud only (no pull)."""
        return self.env['cloud.file'].sudo().with_context(sync_type='auto').sync_pending_to_drive(
            drive_config_id=drive_config_id
        )
