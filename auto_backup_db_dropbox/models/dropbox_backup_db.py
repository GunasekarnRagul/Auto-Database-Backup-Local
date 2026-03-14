# -*- coding: utf-8 -*-
from odoo import models, fields

class DropboxBackupDb(models.Model):
    _name = 'dropbox.backup.db'
    _description = 'Dropbox Backup Database'

    name = fields.Char(string='Database Name', required=True)
