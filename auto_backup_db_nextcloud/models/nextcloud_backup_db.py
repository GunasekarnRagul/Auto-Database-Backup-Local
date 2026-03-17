# -*- coding: utf-8 -*-
from odoo import models, fields

class NextcloudBackupDb(models.Model):
    _name = 'nextcloud.backup.db'
    _description = 'Nextcloud Backup Database'

    name = fields.Char(string='Database Name', required=True)
