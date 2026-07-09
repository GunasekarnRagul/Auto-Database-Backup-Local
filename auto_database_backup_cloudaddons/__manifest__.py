# -*- coding: utf-8 -*-
{
    'name': 'Auto Database Backup | Local & Network Drive Storage',
    'version': '18.0.1.0.0',
    'category': 'Administration',
    'summary': 'Automatically backup your Odoo database to local filesystem or network-mounted drives on a scheduled basis.',
    'description': """
        Auto Database Backup — Automated Local & Network Drive Backup for Odoo 19.

        Reliably backup your Odoo PostgreSQL database to any path on the server:
        - Local filesystem paths (e.g. /var/backups/odoo/)
        - Network-mounted drives (NFS, SMB/CIFS, etc.)

        Features:
        - Configurable backup directory path
        - Multiple backup formats: ZIP (with filestore) or pg_dump (.dump)
        - Scheduled cron-based automatic backups
        - Configurable retention policy (auto-delete old backups)
        - On-demand manual backup from the UI
        - Backup history log with size, duration, and status
        - Dashboard with storage stats and last-run indicators
        - Email notifications on failure
        - Download backup files directly from the UI
    """,
    'author': 'CloudAddons Technologies',
    'support': 'cloudaddonstechnologies@gmail.com',
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron_backup.xml',
        'views/backup_config_views.xml',
        'views/backup_history_views.xml',
        'views/upgrade_page_actions.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend': [
            'auto_database_backup_cloudaddons/static/src/css/backup_dashboard.css',
            'auto_database_backup_cloudaddons/static/src/css/upgrade_page.css',
            'auto_database_backup_cloudaddons/static/src/xml/upgrade_page.xml',
            'auto_database_backup_cloudaddons/static/src/js/upgrade_page.js',
        ],
    },
}
