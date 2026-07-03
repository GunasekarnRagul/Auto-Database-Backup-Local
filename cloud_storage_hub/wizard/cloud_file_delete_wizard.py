# -*- coding: utf-8 -*-
from odoo import models, fields


class CloudFileDeleteWizard(models.TransientModel):
    _name = 'cloud.file.delete.wizard'
    _description = 'Cloud File Delete Confirmation Wizard'

    file_ids  = fields.Many2many('cloud.file', string='Files to Delete')
    delete_remote = fields.Boolean('Also delete from cloud storage?', default=False)

    def action_confirm_delete(self):
        for f in self.file_ids:
            if self.delete_remote:
                try:
                    config = f.drive_config_id
                    engine = self.env['cloud.sync.engine'].sudo()._get_engine(config.provider_type)
                    if hasattr(engine, 'delete_file_from_drive'):
                        engine.delete_file_from_drive(f.cloud_file_id, config)
                except Exception:
                    pass
            f.sudo().unlink()
        return {'type': 'ir.actions.act_window_close'}
