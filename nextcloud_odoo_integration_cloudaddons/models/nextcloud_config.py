# -*- coding: utf-8 -*-
import requests as http_requests
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

class NextcloudRootFolder(models.Model):
    _name = "nextcloud.root.folder"
    _description = "Nextcloud Root Folder"

    name = fields.Char("Folder Name", required=True)
    root_id = fields.Char("Nextcloud Folder ID", readonly=True)
    config_id = fields.Many2one("nextcloud.config", string="Drive Configuration", ondelete="cascade")
    active = fields.Boolean("Active", default=True)

    _sql_constraints = [
        ("name_config_unique", "unique(name, config_id)", "A folder with this name is already configured for this drive!")
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Note: Folder creation on Nextcloud server is handled
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
    _description = "Nextcloud Configuration"

    name = fields.Char("Drive Name", required=True, default="My Nextcloud")
    drive_type = fields.Selection([("nextcloud", "Nextcloud")], string="Drive Type", default="nextcloud", required=True)
    readonly = fields.Boolean("Readonly")
    active = fields.Boolean("Active", default=True)
    state = fields.Selection([
        ("draft", "Not Connected"), ("connected", "Connected"), ("error", "Error"),
    ], string="Status", compute="_compute_state", store=True, default="draft")

    # Nextcloud credentials
    client_id = fields.Char("NextCloud Username", help="Your Nextcloud login username")
    client_secret = fields.Char("App Password", help="Nextcloud App Password generated from Settings > Security")
    nextcloud_url = fields.Char("Nextcloud Server Address", help="Your Nextcloud server address, e.g. https://cloud.example.com")
    is_connected = fields.Boolean("Credentials Verified", default=False, copy=False)
    # Keep refresh_token as a dummy so other code referencing it doesn't break
    refresh_token = fields.Char("Auth Token", compute="_compute_refresh_token", store=True)

    shared_drive_id = fields.Char("Team Folder Namespace", help="Nextcloud team folder namespace ID (Business only). Leave blank for personal.")
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
        ("client_id_unique", "unique(client_id)", "This NextCloud Username is already in use!")
    ]

    @api.constrains("client_id")
    def _check_unique_client_id(self):
        for record in self:
            if record.client_id:
                duplicate = self.search([("client_id", "=", record.client_id), ("id", "!=", record.id)])
                if duplicate:
                    raise ValidationError(
                        "This NextCloud Username is already configured in: %s" % duplicate[0].name
                    )

    @api.depends("is_connected", "root_ids")
    def _compute_state(self):
        for config in self:
            if config.is_connected:
                config.state = "connected"
            else:
                config.state = "draft"

    def action_authenticate(self):
        """Validate Nextcloud credentials via WebDAV and mark as connected."""
        self.ensure_one()

        # ── Field validation ───────────────────────────────────────────────
        if not self.nextcloud_url or self.nextcloud_url.strip() in ("", "https://", "http://"):
            raise UserError(_(
                "Please enter your Nextcloud Server Address.\n"
                "Example: https://cloud.yourcompany.com"
            ))
        if not self.client_id or not self.client_id.strip():
            raise UserError(_("Please enter your NextCloud Username."))
        if not self.client_secret or not self.client_secret.strip():
            raise UserError(_("Please enter your App Password."))

        # ── WebDAV credential check ────────────────────────────────────────
        base_url = self.nextcloud_url.strip().rstrip("/")
        username = self.client_id.strip()
        webdav_url = f"{base_url}/remote.php/webdav"

        try:
            response = http_requests.request(
                "PROPFIND",
                webdav_url,
                auth=(username, self.client_secret),
                headers={"Depth": "0"},
                timeout=15,
            )
        except http_requests.exceptions.ConnectionError:
            raise UserError(_(
                "Cannot reach the Nextcloud server at:\n%s\n\n"
                "Please check the Server Address and make sure the server is online."
            ) % base_url)
        except http_requests.exceptions.Timeout:
            raise UserError(_(
                "Connection to the Nextcloud server timed out.\n"
                "Please check your network and the Server Address."
            ))
        except Exception as e:
            raise UserError(_("Unexpected error: %s") % str(e))

        # ── Interpret response ─────────────────────────────────────────────
        if response.status_code in (200, 207):
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
        elif response.status_code == 401:
            self.sudo().write({"is_connected": False})
            raise UserError(_(
                "Invalid credentials (HTTP 401 Unauthorized).\n"
                "Please check your NextCloud Username and App Password."
            ))
        elif response.status_code == 403:
            self.sudo().write({"is_connected": False})
            raise UserError(_(
                "Access denied (HTTP 403 Forbidden).\n"
                "Your account may not have permission to access WebDAV. "
                "Please check your App Password and account settings."
            ))
        elif response.status_code == 404:
            self.sudo().write({"is_connected": False})
            raise UserError(_(
                "User not found on this server (HTTP 404).\n"
                "Please check your NextCloud Username and Server Address."
            ))
        else:
            self.sudo().write({"is_connected": False})
            raise UserError(_(
                "Connection failed (HTTP %s).\n"
                "Please verify your Server Address, Username, and App Password."
            ) % response.status_code)

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
                # Step 1: Find or create the folder on Nextcloud
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
        return True

    def action_configure_roots(self):
        self.ensure_one()
        return {
            "name": _("Manage Root Folders"),
            "type": "ir.actions.act_window",
            "res_model": "nextcloud.root.folder",
            "view_mode": "tree",
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
        self.env["nextcloud.file"].sudo().with_context(sync_type="manual").sync_pending_to_drive(
            drive_config_id=drive_config_id
        )
        if root_folder_id:
            root = self.env["nextcloud.root.folder"].sudo().browse(root_folder_id)
            if root.exists() and root.active and root.config_id.active:
                self.env["nextcloud.sync"].sudo().with_context(sync_type="manual")._sync_config_files(
                    root.config_id, root_folder_id=root.id, nextcloud_parent_id=root.root_id
                )
            return True
        domain = [("active", "=", True)]
        if drive_config_id:
            domain.append(("id", "=", drive_config_id))
        for config in self.search(domain):
            for root in config.root_ids.filtered(lambda r: r.active):
                self.env["nextcloud.sync"].sudo().with_context(sync_type="manual")._sync_config_files(
                    config, root_folder_id=root.id, nextcloud_parent_id=root.root_id
                )
        return True

    @api.model
    def action_auto_sync_drive(self, drive_config_id=None):
        return self.env["nextcloud.file"].sudo().with_context(sync_type="auto").sync_pending_to_drive(
            drive_config_id=drive_config_id
        )
