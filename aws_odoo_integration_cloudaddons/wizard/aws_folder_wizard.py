from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)

class NextcloudFolderWizard(models.TransientModel):
    _name = 'nextcloud.folder.wizard'
    _description = 'AWS S3 Folder Bulk Add Wizard'
    

    config_id = fields.Many2one('nextcloud.config', string='Drive Configuration', required=True)
    folder_ids_raw = fields.Text('Folder IDs', help='Paste Google Folder IDs here, one per line.')
    
    line_ids = fields.One2many('nextcloud.folder.wizard.line', 'wizard_id', string='Available Folders')

    def action_fetch_folders(self):
        """Fetch top-level folders from the connected AWS S3 account."""
        self.ensure_one()
        # Clear existing lines
        self.line_ids.unlink()
        
        sync_obj = self.env['nextcloud.sync'].sudo()
        config = self.config_id
        access_token = sync_obj._get_access_token(config)
        
        if not access_token:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Error'),
                    'message': _('Could not connect to AWS S3. Please check your credentials.'),
                    'sticky': False,
                }
            }

        import requests as http_requests
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Use AWS S3 list_folder to get top-level folders
        root_path = config.shared_drive_id if config.shared_drive_id else ""
        import json as _json
        dbx_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        try:
            response = http_requests.post(
                "https://api.nextcloudapi.com/2/files/list_folder",
                headers=dbx_headers,
                json={"path": root_path, "recursive": False},
            )
            if response.status_code == 200:
                entries = response.json().get("entries", [])
                lines = []
                for entry in entries:
                    if entry.get(".tag") == "folder":
                        raw_id = entry.get("id","").lstrip("id:")
                        lines.append((0, 0, {
                            "name": entry.get("name"),
                            "google_id": raw_id,
                            "selected": False,
                        }))
                self.write({"line_ids": lines})
            else:
                _logger.warning("AWS S3 API error during folder fetch: %s", response.text)
        except Exception as e:
            _logger.error("Error fetching folders: %s", str(e))
            
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_apply(self):
        self.ensure_one()
        root_obj = self.env['nextcloud.root.folder']
        
        # 1. Process Raw IDs
        if self.folder_ids_raw:
            raw_ids = [rid.strip() for rid in self.folder_ids_raw.split('\n') if rid.strip()]
            for rid in raw_ids:
                # Basic check to avoid duplicates for this config
                existing = root_obj.search([('config_id', '=', self.config_id.id), ('root_id', '=', rid)])
                if not existing:
                    root_obj.create({
                        'name': f'Folder {rid[:8]}...',
                        'root_id': rid,
                        'config_id': self.config_id.id
                    })
        
        # 2. Process Selected Lines
        selected_lines = self.line_ids.filtered(lambda l: l.selected)
        for line in selected_lines:
            existing = root_obj.search([('config_id', '=', self.config_id.id), ('root_id', '=', line.google_id)])
            if not existing:
                root_obj.create({
                    'name': line.name,
                    'root_id': line.google_id,
                    'config_id': self.config_id.id
                })
        
        return {'type': 'ir.actions.act_window_close'}


class NextcloudFolderWizardLine(models.TransientModel):
    _name = 'nextcloud.folder.wizard.line'
    _description = 'AWS S3 Folder Bulk Add Wizard Line'

    wizard_id = fields.Many2one('nextcloud.folder.wizard', string='Wizard')
    name = fields.Char('Folder Name')
    google_id = fields.Char('Google Folder ID')
    selected = fields.Boolean('Select')
