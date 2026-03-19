# -*- coding: utf-8 -*-
from odoo import models, fields


class CloudBackupDb(models.Model):
    _name = 'cloud.backup.db'
    _description = 'Cloud Backup Database'

    name = fields.Char(string='Database Name', required=True)
