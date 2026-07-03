# -*- coding: utf-8 -*-
from odoo import models, fields


class CloudDuplicatePrunerWizard(models.TransientModel):
    _name = 'cloud.duplicate.pruner.wizard'
    _description = 'Cloud Duplicate File Pruner Wizard'

    line_ids = fields.One2many('cloud.duplicate.pruner.wizard.line', 'wizard_id', string='Duplicate Groups')

    def action_load_duplicates(self):
        self.ensure_one()
        self.line_ids.unlink()
        groups = self.env['cloud.file'].sudo().get_duplicate_groups()
        lines = []
        for g in groups:
            for fid in g['duplicate_ids']:
                lines.append((0, 0, {'file_id': fid, 'selected': True}))
        self.write({'line_ids': lines})
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new'}

    def action_prune(self):
        selected = self.line_ids.filtered(lambda l: l.selected)
        selected.mapped('file_id').unlink()
        return {'type': 'ir.actions.act_window_close'}


class CloudDuplicatePrunerWizardLine(models.TransientModel):
    _name = 'cloud.duplicate.pruner.wizard.line'
    _description = 'Duplicate Pruner Wizard Line'

    wizard_id = fields.Many2one('cloud.duplicate.pruner.wizard', string='Wizard', ondelete='cascade')
    file_id   = fields.Many2one('cloud.file', string='File')
    selected  = fields.Boolean('Mark for deletion', default=True)
