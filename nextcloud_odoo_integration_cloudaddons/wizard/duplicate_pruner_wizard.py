# -*- coding: utf-8 -*-
from odoo import models, fields, api

class DuplicatePrunerWizard(models.TransientModel):
    _name = 'duplicate.pruner.wizard'
    _description = 'Duplicate File Pruner Wizard'

    drive_config_id = fields.Many2one('nextcloud.config', string='Nextcloud', 
                                      help="Select a specific drive to find duplicates within, or leave empty for all.")
    duplicate_groups_count = fields.Integer('Number of Groups', readonly=True)
    duplicate_count = fields.Integer('Total Files to Delete', readonly=True)
    line_ids = fields.One2many('duplicate.pruner.wizard.line', 'wizard_id', string='Duplicate Files')

    @api.model
    def default_get(self, fields_list):
        res = super(DuplicatePrunerWizard, self).default_get(fields_list)
        # Initially load all duplicates
        groups = self.env['nextcloud.file'].get_duplicate_groups()
        res.update(self._prepare_wizard_values(groups))
        return res

    @api.onchange('drive_config_id')
    def _onchange_drive_config_id(self):
        groups = self.env['nextcloud.file'].get_duplicate_groups(config_id=self.drive_config_id.id)
        vals = self._prepare_wizard_values(groups)
        self.update(vals)

    def _prepare_wizard_values(self, groups):
        groups_count = len(groups)
        dup_count = sum(len(g['records']) for g in groups) - groups_count if groups_count > 0 else 0
        
        lines = []
        for g in groups:
            for rec in g['records']:
                lines.append((0, 0, {
                    'file_id': rec.id,
                    'name': rec.name,
                    'group_name': g['group_name'],
                    'duplicate_type': g['type'],
                    'display_path': rec.display_path or '/',
                    'file_size': rec.file_size,
                    'create_date': rec.create_date,
                    'drive_config_id': rec.drive_config_id.id if rec.drive_config_id else False,
                    'root_folder_id': rec.root_folder_id.id if rec.root_folder_id else False,
                    'parent_folder_id': rec.parent_folder_id.id if rec.parent_folder_id else False,
                }))
        return {
            'duplicate_groups_count': groups_count,
            'duplicate_count': dup_count,
            'line_ids': [(5, 0, 0)] + lines,
        }


class DuplicatePrunerWizardLine(models.TransientModel):
    _name = 'duplicate.pruner.wizard.line'
    _description = 'Duplicate File Pruner Wizard Line'

    wizard_id = fields.Many2one('duplicate.pruner.wizard', ondelete='cascade')
    file_id = fields.Many2one('nextcloud.file', string="File")
    name = fields.Char('File Name')
    group_name = fields.Char('Duplicate Group Name')
    duplicate_type = fields.Selection([
        ('exact', 'Exact Match'),
        ('conflict', 'Name Conflict')
    ], string='Duplicate Type')
    display_path = fields.Char('Location Path')
    file_size = fields.Float('Size (KB)')
    create_date = fields.Datetime('Created On')
    drive_config_id = fields.Many2one('nextcloud.config')
    root_folder_id = fields.Many2one('nextcloud.root.folder')
    parent_folder_id = fields.Many2one('nextcloud.file')

    def action_view_in_explorer(self):
        self.ensure_one()
        url = "/web#action=nextcloud_odoo_integration.action_nextcloud_file_explorer"
        if self.drive_config_id:
            url += f"&gd_drive_id={self.drive_config_id.id}"
        if self.root_folder_id:
            url += f"&gd_root_id={self.root_folder_id.id}"
        if self.parent_folder_id:
            url += f"&gd_parent_id={self.parent_folder_id.id}"
        if self.file_id:
            url += f"&gd_file_id={self.file_id.id}"
            
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'self',
        }
