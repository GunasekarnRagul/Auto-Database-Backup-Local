# -*- coding: utf-8 -*-
{
    'name': 'Auto Database Backup to Nextcloud',
    'version': '1.0',
    'category': 'Extra Tools',
    'summary': 'Automatically backup Odoo databases to Nextcloud storage via WebDAV',
    'description': """
        This module allows you to automatically backup your Odoo databases to a Nextcloud instance.
        Features:
        - Manual and Auto Backup
        - Retention Policy (Count or Days)
        - Selective database backup
        - WebDAV integration
    """,
    'author': 'Ragul G',
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
    'application': False,
    'license': 'LGPL-3',
}
