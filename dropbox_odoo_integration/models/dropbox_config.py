# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.http import request
from odoo.exceptions import ValidationError

DROPBOX_AUTH_URL = "https://www.dropbox.com/oauth2/authorize"

class GoogleDriveRootFolder(models.Model):
    _name = "one.drive.root.folder"
    _description = "Dropbox Root Folder"

    name = fields.Char("Folder Name", required=True)
    root_id = fields.Char("Dropbox Folder ID", readonly=True)
    config_id = fields.Many2one("one.drive.config", string="Drive Configuration", ondelete="cascade")
    active = fields.Boolean("Active", default=True)

    _sql_constraints = [
        ("name_config_unique", "unique(name, config_id)", "A folder with this name is already configured for this drive!")
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.config_id.state == "connected" and not record.root_id:
                result = self.env["one.drive.sync"].sudo().find_or_create_folder(
                    record.name, "", record.config_id
                )
                if result and result.get("one_drive_file_id"):
                    record.write({"root_id": result["one_drive_file_id"]})
        return records

    def unlink(self):
        self.env["one.drive.file"].sudo().search([("root_folder_id", "in", self.ids)]).unlink()
        return super().unlink()

    def write(self, vals):
        if "root_id" in vals:
            for root in self:
                if vals.get("root_id") != root.root_id:
                    self.env["one.drive.file"].sudo().search([("root_folder_id", "=", root.id)]).unlink()
        return super().write(vals)


class GoogleDriveConfig(models.Model):
    _name = "one.drive.config"
    _description = "Dropbox Configuration"

    name = fields.Char("Drive Name", required=True, default="My Dropbox")
    drive_type = fields.Selection([("one_drive", "Dropbox")], string="Drive Type", default="one_drive", required=True)
    readonly = fields.Boolean("Readonly")
    active = fields.Boolean("Active", default=True)
    state = fields.Selection([
        ("draft", "Not Connected"), ("connected", "Connected"), ("error", "Error"),
    ], string="Status", compute="_compute_state", store=True, default="draft")

    # Dropbox OAuth fields — client_id = App Key, client_secret = App Secret
    client_id = fields.Char("App Key", help="Dropbox App Key from the App Console")
    client_secret = fields.Char("App Secret", help="Dropbox App Secret from the App Console")
    redirect_uri = fields.Char("Redirect URI", compute="_compute_redirect_uri")
    refresh_token = fields.Char("Refresh Token")

    shared_drive_id = fields.Char("Team Folder Namespace", help="Dropbox team folder namespace ID (Business only). Leave blank for personal.")
    root_ids = fields.One2many("one.drive.root.folder", "config_id", string="Root Folders")
    group_ids = fields.Many2many("res.groups", string="Groups with access")
    user_ids = fields.Many2many("res.users", string="Users with access")
    root_folder_count = fields.Integer("Root Folders", compute="_compute_counts")

    def _compute_counts(self):
        for config in self:
            config.root_folder_count = len(config.root_ids)

    _sql_constraints = [
        ("client_id_unique", "unique(client_id)", "This App Key is already in use!")
    ]

    @api.constrains("client_id")
    def _check_unique_client_id(self):
        for record in self:
            if record.client_id:
                duplicate = self.search([("client_id", "=", record.client_id), ("id", "!=", record.id)])
                if duplicate:
                    raise ValidationError(
                        "This App Key is already configured in: %s" % duplicate[0].name
                    )

    @api.depends("refresh_token", "root_ids")
    def _compute_state(self):
        for config in self:
            config.state = "connected" if config.refresh_token and config.root_ids else "draft"

    @api.depends("client_id")
    def _compute_redirect_uri(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        for config in self:
            host_url = base_url or ""
            try:
                if host_url and ("localhost" in host_url or "127.0.0.1" in host_url or ":8069" in host_url):
                    req_root = request.httprequest.url_root.rstrip("/")
                    if req_root:
                        host_url = req_root
            except Exception:
                pass
            config.redirect_uri = f"{host_url}/dropbox/authentication"

    def action_authenticate(self):
        self.ensure_one()
        if not self.client_id:
            return False
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "token_access_type": "offline",
            "state": str(self.id),
        }
        url = DROPBOX_AUTH_URL + "?" + "&".join([f"{k}={v}" for k, v in params.items()])
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    def action_sync_all(self):
        self.ensure_one()
        # 1. PUSH: Odoo to Dropbox
        self.env["one.drive.file"].sudo().with_context(sync_type="manual").sync_pending_to_drive(drive_config_id=self.id)
        # 2. PULL: Dropbox to Odoo
        for root in self.root_ids.filtered(lambda r: r.active):
            self.env["one.drive.sync"].sudo().with_context(sync_type="manual")._sync_config_files(
                self, root_folder_id=root.id, one_drive_parent_id=root.root_id
            )
        return True

    def action_configure_roots(self):
        self.ensure_one()
        return {
            "name": _("Manage Root Folders"),
            "type": "ir.actions.act_window",
            "res_model": "one.drive.root.folder",
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
            "res_model": "one.drive.folder.wizard",
            "view_mode": "form",
            "context": {"default_config_id": self.id},
            "target": "new",
        }

    @api.model
    def action_trigger_sync(self, root_folder_id=None, drive_config_id=None):
        self.env["one.drive.file"].sudo().with_context(sync_type="manual").sync_pending_to_drive(
            drive_config_id=drive_config_id
        )
        if root_folder_id:
            root = self.env["one.drive.root.folder"].sudo().browse(root_folder_id)
            if root.exists() and root.active and root.config_id.active:
                self.env["one.drive.sync"].sudo().with_context(sync_type="manual")._sync_config_files(
                    root.config_id, root_folder_id=root.id, one_drive_parent_id=root.root_id
                )
            return True
        domain = [("active", "=", True)]
        if drive_config_id:
            domain.append(("id", "=", drive_config_id))
        for config in self.search(domain):
            for root in config.root_ids.filtered(lambda r: r.active):
                self.env["one.drive.sync"].sudo().with_context(sync_type="manual")._sync_config_files(
                    config, root_folder_id=root.id, one_drive_parent_id=root.root_id
                )
        return True

    @api.model
    def action_auto_sync_drive(self, drive_config_id=None):
        return self.env["one.drive.file"].sudo().with_context(sync_type="auto").sync_pending_to_drive(
            drive_config_id=drive_config_id
        )
