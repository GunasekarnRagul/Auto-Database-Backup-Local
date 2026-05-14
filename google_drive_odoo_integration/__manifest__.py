# -*- coding: utf-8 -*-
{
    'name': 'Cloud Storage — Google Drive ',
    'version': '18.0.1.0.0',
    'category': 'Document Management',
    'summary': 'Seamless bi-directional synchronization between Odoo and Google Drive.',
    'description': """
        This module provides integration between Odoo and Google Drive.
        - OAuth 2.0 Authentication.
        - Automatic upload of Odoo attachments to Google Drive.
        - Sync files from Google Drive back to Odoo.
    """,
    'author': 'CloudAddons Technologies',
    'depends': ['base', 'mail', 'bus'],
    'price': 147.00,
    'currency': 'USD',
    'support': 'cloudaddonstechnologies@gmail.com',
    'data': [
        'security/ir.model.access.csv',
        'data/cron_sync.xml',
        'data/gdrive_model_config.xml',
        'wizard/google_drive_folder_wizard_views.xml',
        'wizard/google_drive_file_delete_wizard_views.xml',
        'wizard/duplicate_pruner_wizard_views.xml',
        'views/google_drive_dashboard_views.xml',
        'views/attachment_sync_config_views.xml',
        'views/google_drive_config_views.xml',
        'views/google_drive_file_views.xml',
        'views/gdrive_model_config_views.xml',
        'views/ir_attachment_views.xml',
        'views/client_action_views.xml',
        'views/google_drive_sync_log_views.xml',
        'views/google_drive_documentation_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend': [
            # Third-party libraries
            'google_drive_odoo_integration/static/lib/chart.min.js',
            # Module CSS
            'google_drive_odoo_integration/static/src/css/common_font.css',
            'google_drive_odoo_integration/static/src/css/kanban_premium.css',
            'google_drive_odoo_integration/static/src/css/file_explorer.css',
            'google_drive_odoo_integration/static/src/css/file_manager.css',
            'google_drive_odoo_integration/static/src/css/sync_log_terminal.css',
            'google_drive_odoo_integration/static/src/css/google_drive_dashboard.css',
            'google_drive_odoo_integration/static/src/css/google_drive_documentation.css',
            # Module JS
            'google_drive_odoo_integration/static/src/js/file_explorer/file_explorer.js',
            'google_drive_odoo_integration/static/src/js/google_drive_config_form.js',
            'google_drive_odoo_integration/static/src/js/file_explorer/trash_restricted_dialog.js',
            'google_drive_odoo_integration/static/src/js/sync_log_terminal.js',
            'google_drive_odoo_integration/static/src/js/dashboard/google_drive_dashboard.js',
            'google_drive_odoo_integration/static/src/js/attachment_upload_button.js',
            'google_drive_odoo_integration/static/src/js/documentation/google_drive_documentation.js',
            # Module XML templates
            'google_drive_odoo_integration/static/src/xml/file_explorer.xml',
            'google_drive_odoo_integration/static/src/xml/trash_restricted_dialog.xml',
            'google_drive_odoo_integration/static/src/xml/sync_log_terminal.xml',
            'google_drive_odoo_integration/static/src/xml/google_drive_dashboard.xml',
            'google_drive_odoo_integration/static/src/xml/google_drive_documentation.xml',
        ],
    },
}
