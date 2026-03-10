{
    'name': 'Auto Backup Database to Google Drive',
    'version': '1.0',
    'category': 'Administration',
    'summary': 'Authenticate and backup Odoo database to Google Drive',
    'description': """
        This module allows you to authenticate with Google Drive and 
        configure a folder for database backups.
    """,
    'author': 'Ragul G',
    'depends': ['base_setup'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
