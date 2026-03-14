# -*- coding: utf-8 -*-
{
    'name': 'Auto Backup Database to Dropbox',
    'version': '1.0',
    'category': 'Extra Tools',
    'summary': 'Automated and Manual Database Backups to Dropbox with Retention Policy',
    'description': """
        This module allows you to backup your Odoo databases to Dropbox.
        Features:
        - Automated backups at specified intervals
        - Manual backups
        - Retention policy (Keep last X backups or keep for X days)
        - Supports multiple databases
    """,
    'author': 'Ragulg',
    'depends': ['base', 'base_setup'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/res_config_settings_views.xml',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
