# -*- coding: utf-8 -*-
import re
import logging
import requests as http_requests
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)

# ── AWS credential format patterns ──────────────────────────────────────────
# Access Key IDs: 20 uppercase alphanumeric chars starting with AKIA/ABIA/ACCA/ASIA
_AWS_KEY_ID_RE = re.compile(r'^(AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}$')
# Region: e.g. us-east-1, eu-north-1, ap-southeast-2
_AWS_REGION_RE = re.compile(r'^[a-z]{2}-[a-z]+-\d+$')
# S3 bucket: 3–63 chars, lowercase letters/numbers/hyphens, no leading/trailing hyphen,
# no consecutive hyphens, cannot look like an IP address
_S3_BUCKET_RE  = re.compile(r'^(?!-)(?!.*--)[a-z0-9][a-z0-9\-]{1,61}[a-z0-9]$')

class NextcloudRootFolder(models.Model):
    _name = "nextcloud.root.folder"
    _description = "AWS S3 Root Folder"

    name = fields.Char("Folder Name", required=True)
    root_id = fields.Char("AWS S3 Folder ID", readonly=True)
    config_id = fields.Many2one("nextcloud.config", string="Drive Configuration", ondelete="cascade")
    active = fields.Boolean("Active", default=True)

    _sql_constraints = [
        ("name_config_unique", "unique(name, config_id)", "A folder with this name is already configured for this drive!")
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Note: Folder creation on AWS S3 server is handled
        # by the sync engine, not during record creation.
        return records

    def unlink(self):
        self.env["nextcloud.file"].sudo().search([("root_folder_id", "in", self.ids)]).unlink()
        return super().unlink()

    def write(self, vals):
        if "root_id" in vals:
            for root in self:
                if vals.get("root_id") != root.root_id:
                    self.env["nextcloud.file"].sudo().search([("root_folder_id", "=", root.id)]).unlink()
        return super().write(vals)


class NextcloudConfig(models.Model):
    _name = "nextcloud.config"
    _description = "AWS S3 Configuration"

    name = fields.Char("Drive Name", required=True, default="My AWS S3")
    drive_type = fields.Selection([("nextcloud", "AWS S3")], string="Drive Type", default="nextcloud", required=True)
    readonly = fields.Boolean("Readonly")
    active = fields.Boolean("Active", default=True)
    state = fields.Selection([
        ("draft", "Not Connected"), ("connected", "Connected"), ("error", "Error"),
    ], string="Status", compute="_compute_state", store=True, default="draft")

    # AWS S3 credentials
    aws_access_key_id = fields.Char("AWS Access Key ID", help="Enter your AWS Access Key ID")
    aws_secret_access_key = fields.Char("AWS Secret Access Key", help="Enter your AWS Secret Access Key")
    aws_region = fields.Char("AWS Region", help="Enter your AWS Region", default="us-east-1")
    aws_s3_bucket_name = fields.Char("S3 Bucket Name", help="AWS S3 Bucket where files will be stored.")
    share_link_expiry_days = fields.Integer(
        "Share Link Expiry (Days)",
        default=7,
        help="Number of days a shared file link (presigned URL) remains valid. Min: 1, Max: 7."
    )

    is_connected = fields.Boolean("Credentials Verified", default=False, copy=False)
    # Keep refresh_token as a dummy so other code referencing it doesn't break
    refresh_token = fields.Char("Auth Token", compute="_compute_refresh_token", store=True)

    shared_drive_id = fields.Char("Team Folder Namespace", help="AWS S3 team folder namespace ID (Business only). Leave blank for personal.")
    root_ids = fields.One2many("nextcloud.root.folder", "config_id", string="Root Folders")
    group_ids = fields.Many2many("res.groups", string="Groups with access")
    user_ids = fields.Many2many("res.users", string="Users with access")
    root_folder_count = fields.Integer("Root Folders", compute="_compute_counts")

    def _compute_counts(self):
        for config in self:
            config.root_folder_count = len(config.root_ids)

    @api.depends("is_connected")
    def _compute_refresh_token(self):
        """Provides a non-empty string when connected so downstream code that checks
        'if config.refresh_token' continues to work without changes."""
        for config in self:
            config.refresh_token = "basic_auth_connected" if config.is_connected else False

    _sql_constraints = [
        ("aws_access_key_id_unique", "unique(aws_access_key_id)", "This AWS Access Key ID is already in use!")
    ]

    @api.constrains("aws_access_key_id")
    def _check_unique_access_key(self):
        for record in self:
            if record.aws_access_key_id:
                duplicate = self.search([("aws_access_key_id", "=", record.aws_access_key_id), ("id", "!=", record.id)])
                if duplicate:
                    raise ValidationError(
                        "This AWS Access Key ID is already configured in: %s" % duplicate[0].name
                    )

    @api.depends("is_connected", "root_ids")
    def _compute_state(self):
        for config in self:
            if config.is_connected:
                config.state = "connected"
            else:
                config.state = "draft"

    def action_authenticate(self):
        """Validate AWS S3 credentials using boto3 and mark as connected."""
        self.ensure_one()

        # ── 1. Presence checks ─────────────────────────────────────────────
        access_key  = (self.aws_access_key_id or '').strip()
        secret_key  = (self.aws_secret_access_key or '').strip()
        region      = (self.aws_region or '').strip()
        bucket_name = (self.aws_s3_bucket_name or '').strip()

        if not access_key:
            raise UserError(_("AWS Access Key ID is required. Please enter your Access Key ID."))
        if not secret_key:
            raise UserError(_("AWS Secret Access Key is required. Please enter your Secret Access Key."))
        if not region:
            raise UserError(_("AWS Region is required. Please enter a valid region (e.g. eu-north-1)."))
        if not bucket_name:
            raise UserError(_("S3 Bucket Name is required. Please enter your bucket name."))

        # ── 2. Format validation ───────────────────────────────────────────
        # Access Key ID must be exactly 20 chars and start with AKIA/ABIA/ACCA/ASIA
        if not _AWS_KEY_ID_RE.match(access_key):
            raise UserError(_(
                "Invalid AWS Access Key ID: '%s'.\n\n"
                "An AWS Access Key ID must:\n"
                "  • Be exactly 20 characters long\n"
                "  • Start with AKIA, ABIA, ACCA, or ASIA\n"
                "  • Contain only uppercase letters and digits\n\n"
                "Example: AKIAS6VZRTEZIA57ZCNK"
            ) % access_key)

        # Secret Access Key must be at least 40 characters
        if len(secret_key) < 40:
            raise UserError(_(
                "Invalid AWS Secret Access Key.\n\n"
                "The Secret Access Key must be at least 40 characters long.\n"
                "Please copy the exact key from your AWS IAM console."
            ))

        # Region must match the standard AWS region naming pattern
        if not _AWS_REGION_RE.match(region):
            raise UserError(_(
                "Invalid AWS Region: '%s'.\n\n"
                "A valid AWS region follows the pattern: <area>-<direction>-<number>\n"
                "Examples: us-east-1, eu-north-1, ap-southeast-2, ca-central-1"
            ) % region)

        # S3 bucket name must comply with AWS DNS-compatible naming rules
        if len(bucket_name) < 3 or len(bucket_name) > 63:
            raise UserError(_(
                "Invalid S3 Bucket Name: '%s'.\n\n"
                "Bucket names must be between 3 and 63 characters long."
            ) % bucket_name)
        if not _S3_BUCKET_RE.match(bucket_name):
            raise UserError(_(
                "Invalid S3 Bucket Name: '%s'.\n\n"
                "S3 bucket names must:\n"
                "  • Contain only lowercase letters, numbers, and hyphens\n"
                "  • Start and end with a letter or number\n"
                "  • Not contain consecutive hyphens (--)\n"
                "  • Not resemble an IP address (e.g. 192.168.0.1)\n\n"
                "Example: your-custom-name-202"
            ) % bucket_name)

        # ── 3. boto3 live connection check ────────────────────────────────
        try:
            import boto3
            from botocore.exceptions import ClientError, NoCredentialsError, EndpointResolutionError
        except ImportError:
            raise UserError(_(
                "The 'boto3' Python library is not installed.\n"
                "Please install it by running: pip install boto3"
            ))

        try:
            s3_client = boto3.client(
                's3',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
            )
            # head_bucket verifies both credentials AND that the bucket exists/is accessible
            s3_client.head_bucket(Bucket=bucket_name)

        except ImportError as e:
            raise UserError(_("Missing dependency: %s") % str(e))
        except Exception as e:
            err_str = str(e)
            self.sudo().write({"is_connected": False})
            if 'NoCredentialsError' in type(e).__name__ or 'NoCredentialsError' in err_str:
                raise UserError(_(
                    "AWS credentials are invalid or missing.\n"
                    "Please check your Access Key ID and Secret Access Key."
                ))
            if 'ClientError' in type(e).__name__:
                if '403' in err_str or 'Forbidden' in err_str:
                    raise UserError(_(
                        "Access Denied (HTTP 403).\n"
                        "The credentials are valid but do not have permission "
                        "to access the bucket '%s'.\n"
                        "Please check your IAM policy for s3:HeadBucket permission."
                    ) % bucket_name)
                if '404' in err_str or 'NoSuchBucket' in err_str:
                    raise UserError(_(
                        "Bucket Not Found (HTTP 404).\n"
                        "The bucket '%s' does not exist in the '%s' region.\n"
                        "Please verify the bucket name and region."
                    ) % (bucket_name, region))
                if '301' in err_str or 'PermanentRedirect' in err_str:
                    raise UserError(_(
                        "Region Mismatch.\n"
                        "The bucket '%s' exists but is NOT in the '%s' region.\n"
                        "Please update the AWS Region field to match the bucket's actual region."
                    ) % (bucket_name, region))
            if 'EndpointResolutionError' in type(e).__name__ or 'Could not connect' in err_str:
                raise UserError(_(
                    "Cannot connect to AWS.\n"
                    "Please check your internet connection and the AWS Region value."
                ))
            raise UserError(_(
                "Connection to AWS S3 failed:\n%s\n\n"
                "Please verify your credentials, bucket name, and region."
            ) % err_str)

        # ── 4. Success ───────────────────────────────────────────────────
        self.sudo().write({"is_connected": True})
        return {
            "type": "ir.actions.act_window",
            "res_model": "nextcloud.config",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": {
                "show_notification": "connected",
            },
        }

    def action_disconnect(self):
        """Clear credentials and reset to draft state."""
        self.ensure_one()
        self.sudo().write({"is_connected": False})
        return {
            "type": "ir.actions.act_window",
            "res_model": "nextcloud.config",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_sync_all(self):
        self.ensure_one()
        sync_env = self.env["nextcloud.sync"].sudo()

        for root in self.root_ids.filtered(lambda r: r.active):
            try:
                # Step 1: Find or create the folder on AWS S3
                result = sync_env.find_or_create_folder(root.name, "/", self)
                if result and result.get("remote_path"):
                    remote_path = result["remote_path"]
                    # Save the remote path so we know where to sync
                    if root.root_id != remote_path:
                        root.sudo().write({"root_id": remote_path})
                else:
                    remote_path = root.root_id or ("/" + root.name)

                # Step 2: Recursively sync all files from that folder into Odoo
                sync_env.with_context(sync_type="manual")._sync_config_files(
                    self,
                    root_folder_id=root.id,
                    nextcloud_parent_id=remote_path,
                )
            except Exception as e:
                _logger.error("Sync error for root folder '%s': %s", root.name, e)
                self.env["nextcloud.sync.log"].sudo().log_operation(
                    config=self,
                    file_name=root.name,
                    operation="create_folder",
                    state="fail",
                    error_message=str(e),
                    sync_type="manual",
                    file_type="folder",
                )
        return True

    def action_configure_roots(self):
        self.ensure_one()
        return {
            "name": _("Manage Root Folders"),
            "type": "ir.actions.act_window",
            "res_model": "nextcloud.root.folder",
            "view_mode": "list",
            "domain": [("config_id", "=", self.id)],
            "context": {"default_config_id": self.id},
            "target": "new",
        }

    def action_bulk_add_folders(self):
        self.ensure_one()
        return {
            "name": _("Bulk Add Folders"),
            "type": "ir.actions.act_window",
            "res_model": "nextcloud.folder.wizard",
            "view_mode": "form",
            "context": {"default_config_id": self.id},
            "target": "new",
        }

    @api.model
    def action_trigger_sync(self, root_folder_id=None, drive_config_id=None):
        """Bi-directional sync entry point called from the file explorer UI.

        1. Push: Odoo pending records → AWS S3
        2. Pull: AWS S3 folders → Odoo (ensures root folder exists on S3 first)
        """
        # ── Push: Odoo → S3 ────────────────────────────────────────────────────
        self.env["nextcloud.file"].sudo().with_context(sync_type="manual").sync_pending_to_drive(
            drive_config_id=drive_config_id
        )

        sync_env = self.env["nextcloud.sync"].sudo()

        def _ensure_and_sync_root(config, root):
            """Ensure root folder exists on S3 (creates if missing), save root_id, then pull."""
            try:
                # Step 1: Find or create the root folder on S3
                result = sync_env.find_or_create_folder(root.name, "/", config)
                if result and result.get("remote_path"):
                    remote_path = result["remote_path"]
                    if root.root_id != remote_path:
                        root.sudo().write({"root_id": remote_path})
                else:
                    remote_path = root.root_id or ("/" + root.name)

                # Step 2: Pull files from S3 into Odoo
                sync_env.with_context(sync_type="manual")._sync_config_files(
                    config, root_folder_id=root.id, nextcloud_parent_id=remote_path
                )
            except Exception as e:
                _logger.error("Sync error for root folder '%s': %s", root.name, e)
                config.env["nextcloud.sync.log"].sudo().log_operation(
                    config=config,
                    file_name=root.name,
                    operation="create_folder",
                    state="fail",
                    error_message=str(e),
                    sync_type="manual",
                    file_type="folder",
                )

        # ── Pull: S3 → Odoo ─────────────────────────────────────────────────────
        if root_folder_id:
            root = self.env["nextcloud.root.folder"].sudo().browse(root_folder_id)
            if root.exists() and root.active and root.config_id.active:
                _ensure_and_sync_root(root.config_id, root)
            return True

        domain = [("active", "=", True)]
        if drive_config_id:
            domain.append(("id", "=", drive_config_id))
        for config in self.search(domain):
            for root in config.root_ids.filtered(lambda r: r.active):
                _ensure_and_sync_root(config, root)
        return True

    @api.model
    def action_auto_sync_drive(self, drive_config_id=None):
        return self.env["nextcloud.file"].sudo().with_context(sync_type="auto").sync_pending_to_drive(
            drive_config_id=drive_config_id
        )
