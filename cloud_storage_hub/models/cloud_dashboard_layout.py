# -*- coding: utf-8 -*-
from odoo import models, fields, api


class CloudDashboardLayout(models.Model):
    """Stores per-user widget layout for the Cloud Hub dashboard (GridStack JSON)."""
    _name = 'cloud.dashboard.layout'
    _description = 'Cloud Hub Dashboard Layout (per user)'
    _rec_name = 'user_id'

    user_id = fields.Many2one('res.users', string='User', required=True,
                              ondelete='cascade', index=True,
                              default=lambda self: self.env.uid)
    layout_json = fields.Text('Layout JSON',
                              help='GridStack serialised layout blob saved by the OWL component.')

    _sql_constraints = [
        ('user_unique', 'unique(user_id)', 'Each user can only have one saved layout.'),
    ]
