# -*- coding: utf-8 -*-
from odoo import models, fields, api


class GdriveDashboardLayout(models.Model):
    """Stores per-user widget layout for the OneDrive dashboard.

    Each user has at most one row.  The layout is serialised as a JSON string
    produced by GridStack's ``grid.save()`` method and restored via
    ``grid.load()``.
    """
    _name = 'one_drive.dashboard.layout'
    _description = 'OneDrive Dashboard Layout (per user)'
    _rec_name = 'user_id'

    user_id = fields.Many2one(
        'res.users',
        string='User',
        required=True,
        ondelete='cascade',
        index=True,
        default=lambda self: self.env.uid,
    )
    layout_json = fields.Text(
        string='Layout JSON',
        help='GridStack serialised layout blob saved by the OWL component.',
    )
