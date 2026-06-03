# -*- coding: utf-8 -*-
from odoo import models, fields, api

class DropboxModelConfig(models.Model):
    _name = 'one_drive.model.config'
    _description = 'Dropbox Model Configuration'
    _rec_name = 'model_label'

    res_model = fields.Char('Technical Model Name', required=True, index=True)
    model_label = fields.Char('Model Label', required=True)
    is_enabled = fields.Boolean('Enabled for Sync', default=True)
    drive_root_folder_name = fields.Char('Drive Root Folder Name', required=True)

    _sql_constraints = [
        ('res_model_unique', 'unique(res_model)', 'Each model can only have one configuration.'),
    ]