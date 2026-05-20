from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)

class GoogleDriveFolderWizard(models.TransientModel):
    _name = 'one.drive.folder.wizard'
    _description = 'OneDrive Folder Bulk Add Wizard'

    config_id = fields.Many2one('one.drive.config', string='Drive Configuration', required=True)
    folder_ids_raw = fields.Text('Folder IDs', help='Paste Google Folder IDs here, one per line.')
    
    line_ids = fields.One2many('one.drive.folder.wizard.line', 'wizard_id', string='Available Folders')

    def action_fetch_folders(self):
        """Fetch top-level folders from the connected OneDrive."""
        self.ensure_one()
        # Clear existing lines
        self.line_ids.unlink()
        
        sync_obj = self.env['one.drive.sync'].sudo()
        config = self.config_id
        access_token = sync_obj._get_access_token(config)
        
        if not access_token:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Error'),
                    'message': _('Could not connect to OneDrive. Please check your credentials.'),
                    'sticky': False,
                }
            }

        import requests as http_requests
        headers = {"Authorization": f"Bearer {access_token}"}
        
        # Query: Folders in the root (or Shared Drive if shared_drive_id is set)
        query = "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        if config.shared_drive_id:
            # For Shared Drives
            query += f" and '{config.shared_drive_id}' in parents"
        else:
            # For Personal Drive
            query += " and 'root' in parents"

        url = (
            f"https://www.googleapis.com/drive/v3/files"
            f"?q={query}"
            f"&fields=files(id,name)"
            f"&pageSize=100"
        )
        if config.shared_drive_id:
            url += "&supportsAllDrives=true&includeItemsFromAllDrives=true"

        try:
            response = http_requests.get(url, headers=headers)
            if response.status_code == 200:
                files = response.json().get('files', [])
                lines = []
                for f in files:
                    lines.append((0, 0, {
                        'name': f.get('name'),
                        'google_id': f.get('id'),
                        'selected': False
                    }))
                self.write({'line_ids': lines})
            else:
                _logger.warning("Drive API error during folder fetch: %s", response.text)
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
        root_obj = self.env['one.drive.root.folder']
        
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


class GoogleDriveFolderWizardLine(models.TransientModel):
    _name = 'one.drive.folder.wizard.line'
    _description = 'OneDrive Folder Bulk Add Wizard Line'

    wizard_id = fields.Many2one('one.drive.folder.wizard', string='Wizard')
    name = fields.Char('Folder Name')
    google_id = fields.Char('Google Folder ID')
    selected = fields.Boolean('Select')
