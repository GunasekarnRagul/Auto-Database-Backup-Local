# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
import logging
_logger = logging.getLogger(__name__)


class CloudFolderWizard(models.TransientModel):
    _name = 'cloud.folder.wizard'
    _description = 'Cloud Storage Bulk Add Folder Wizard'

    config_id = fields.Many2one('cloud.provider.config', string='Provider Configuration', required=True)
    line_ids   = fields.One2many('cloud.folder.wizard.line', 'wizard_id', string='Available Folders')

    def action_apply(self):
        self.ensure_one()
        root_obj = self.env['cloud.root.folder']
        for line in self.line_ids.filtered(lambda l: l.selected):
            existing = root_obj.search([('config_id', '=', self.config_id.id), ('name', '=', line.name)])
            if not existing:
                root_obj.create({'name': line.name, 'root_id': line.remote_id, 'config_id': self.config_id.id})
        return {'type': 'ir.actions.act_window_close'}


class CloudFolderWizardLine(models.TransientModel):
    _name = 'cloud.folder.wizard.line'
    _description = 'Cloud Folder Wizard Line'

    wizard_id = fields.Many2one('cloud.folder.wizard', string='Wizard')
    name      = fields.Char('Folder Name')
    remote_id = fields.Char('Remote ID / Path')
    selected  = fields.Boolean('Select')
