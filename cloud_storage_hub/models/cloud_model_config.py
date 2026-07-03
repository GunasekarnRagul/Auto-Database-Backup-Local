# -*- coding: utf-8 -*-
from odoo import models, fields


class CloudModelConfig(models.Model):
    _name = 'cloud.model.config'
    _description = 'Cloud Storage Model Configuration'
    _rec_name = 'model_label'

    res_model            = fields.Char('Technical Model Name', required=True, index=True)
    model_label          = fields.Char('Model Label', required=True)
    is_enabled           = fields.Boolean('Enabled for Sync', default=True)
    drive_root_folder_name = fields.Char('Default Root Folder Name', required=True)

    _sql_constraints = [
        ('res_model_unique', 'unique(res_model)', 'Each Odoo model can only have one configuration.'),
    ]
