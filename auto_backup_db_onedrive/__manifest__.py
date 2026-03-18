{
    'name': 'Auto Backup Database to OneDrive',
    'version': '1.0',
    'category': 'Administration',
    'summary': 'Authenticate and backup Odoo database to Microsoft OneDrive',
    'description': """
        This module allows you to authenticate with Microsoft OneDrive and 
        configure a folder for database backups using Microsoft Graph API.
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
