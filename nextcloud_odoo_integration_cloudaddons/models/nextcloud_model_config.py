# -*- coding: utf-8 -*-
from odoo import models, fields, api

class NextcloudModelConfig(models.Model):
    _name = 'nextcloud.model.config'
    _description = 'Nextcloud Model Configuration'
    _rec_name = 'model_label'

    res_model = fields.Char('Technical Model Name', required=True, index=True)
    model_label = fields.Char('Model Label', required=True)
    is_enabled = fields.Boolean('Enabled for Sync', default=True)
    active = fields.Boolean('Active', default=True)
    drive_root_folder_name = fields.Char('Drive Root Folder Name', required=True)

    _sql_constraints = [
        ('res_model_unique', 'unique(res_model)', 'Each model can only have one configuration.'),
    ]

    def unlink(self):
        """
        Manually delete related attachment.sync.config records before deleting to 
        prevent PostgreSQL foreign key constraint errors if the DB constraint
        was not created with ON DELETE CASCADE.
        """
        for record in self:
            related_sync_configs = self.env['attachment.sync.config'].search([('module_config_id', '=', record.id)])
            if related_sync_configs:
                related_sync_configs.unlink()
        return super(NextcloudModelConfig, self).unlink()