# -*- coding: utf-8 -*-
from odoo import models, fields

class GoogleDriveFileDeleteWizard(models.TransientModel):
    _name = 'google.drive.file.delete.wizard'
    _description = 'Confirm Deletion of Tracked Folder'

    folder_id = fields.Many2one('google.drive.file', string='Folder', required=True)
    config_ids = fields.Many2many('attachment.sync.config', string='Affected Configurations', readonly=True)
    warning_message = fields.Text('Warning Message', compute='_compute_warning_message')

    def _compute_warning_message(self):
        for wiz in self:
            if wiz.config_ids:
                models = ", ".join(wiz.config_ids.mapped('module_config_id.name'))
                wiz.warning_message = f"These folders configure the attachments for this {models} model. If you want to delete the folder, it will automatically remove the {models} configure. If you are OK for that, press the Delete button."
            else:
                wiz.warning_message = "Are you sure you want to delete this folder?"

    def action_confirm_delete(self):
        self.folder_id.unlink()
        return {'type': 'ir.actions.client', 'tag': 'reload'}
