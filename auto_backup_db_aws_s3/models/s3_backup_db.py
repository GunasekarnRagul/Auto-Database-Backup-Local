# -*- coding: utf-8 -*-
from odoo import models, fields

class S3BackupDb(models.Model):
    _name = 's3.backup.db'
    _description = 'AWS S3 Backup Database'

    name = fields.Char(string='Database Name', required=True)
