{
    'name': 'Auto Backup Database to Google Drive',
    'version': '19.0.1.0.0',
    'category': 'Extra Tools',
    'summary': 'Daily automated database backups with secure Google Drive integration.',
    'description': """
        This module allows you to authenticate with Google Drive and 
        configure a folder for database backups.
    """,
    'author': 'CloudAddons Technologies',
    'depends': ['base_setup'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/res_config_settings_views.xml',
    ],
    'price': 10.0,
    'currency': 'USD',
    'installable': True,
    'application': True,
    'license': 'OPL-1',
}
