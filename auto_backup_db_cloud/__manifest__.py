# -*- coding: utf-8 -*-
{
    'name': 'Auto Backup Database to Cloud',
    'version': '1.0',
    'category': 'Extra Tools',
    'summary': 'Unified Database Backup to AWS S3, Google Drive, Dropbox, OneDrive & Nextcloud',
    'description': """
        All-in-one cloud backup module for Odoo databases.
        Features:
        - Support for AWS S3, Google Drive, Dropbox, OneDrive, Nextcloud
        - Automated backups at specified intervals
        - Manual backups
        - Retention policy (Keep last X backups or keep for X days)
        - Selective database backup
        - Single provider selector with separated code per provider
    """,
    'author': 'Ragul G',
    'depends': ['base', 'base_setup'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/res_config_settings_views.xml',
        'views/cloud_auth_success.xml',
        'views/aws_s3_views.xml',
        'views/gdrive_views.xml',
        'views/dropbox_views.xml',
        'views/onedrive_views.xml',
        'views/nextcloud_views.xml',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
