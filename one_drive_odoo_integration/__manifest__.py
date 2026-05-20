# -*- coding: utf-8 -*-
{
    'name': 'Cloud Storage | OneDrive ',
    'version': '18.0.1.0.0',
    'category': 'Document Management',
    'summary': 'Seamless bi-directional synchronization between Odoo and OneDrive.',
    'description': """
        This module provides integration between Odoo and OneDrive.
        - OAuth 2.0 Authentication.
        - Automatic upload of Odoo attachments to OneDrive.
        - Sync files from OneDrive back to Odoo.
    """,
    'author': 'CloudAddons Technologies',
    'depends': ['base', 'mail', 'bus'],
    'price': 147.00,
    'currency': 'USD',
    'support': 'cloudaddonstechnologies@gmail.com',
    'depends': ['base', 'mail', 'bus'],
    'images': ['static/description/main_screenshot.png'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron_sync.xml',
        'data/one_drive_model_config.xml',
        'wizard/one_drive_folder_wizard_views.xml',
        'wizard/one_drive_file_delete_wizard_views.xml',
        'wizard/duplicate_pruner_wizard_views.xml',
        'views/one_drive_dashboard_views.xml',
        'views/attachment_sync_config_views.xml',
        'views/one_drive_config_views.xml',
        'views/one_drive_file_views.xml',
        'views/one_drive_model_config_views.xml',
        'views/ir_attachment_views.xml',
        'views/client_action_views.xml',
        'views/one_drive_sync_log_views.xml',
        'views/one_drive_documentation_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend': [
            # Third-party libraries
            'one_drive_odoo_integration/static/lib/chart.min.js',
            # Module CSS
            'one_drive_odoo_integration/static/src/css/common_font.css',
            'one_drive_odoo_integration/static/src/css/kanban_premium.css',
            'one_drive_odoo_integration/static/src/css/file_explorer.css',
            'one_drive_odoo_integration/static/src/css/file_manager.css',
            'one_drive_odoo_integration/static/src/css/sync_log_terminal.css',
            'one_drive_odoo_integration/static/src/css/one_drive_dashboard.css',
            'one_drive_odoo_integration/static/src/css/one_drive_documentation.css',
            # Module JS
            'one_drive_odoo_integration/static/src/js/file_explorer/file_explorer.js',
            'one_drive_odoo_integration/static/src/js/one_drive_config_form.js',
            'one_drive_odoo_integration/static/src/js/file_explorer/trash_restricted_dialog.js',
            'one_drive_odoo_integration/static/src/js/sync_log_terminal.js',
            'one_drive_odoo_integration/static/src/js/dashboard/one_drive_dashboard.js',
            'one_drive_odoo_integration/static/src/js/attachment_upload_button.js',
            'one_drive_odoo_integration/static/src/js/documentation/one_drive_documentation.js',
            # Module XML templates
            'one_drive_odoo_integration/static/src/xml/file_explorer.xml',
            'one_drive_odoo_integration/static/src/xml/trash_restricted_dialog.xml',
            'one_drive_odoo_integration/static/src/xml/sync_log_terminal.xml',
            'one_drive_odoo_integration/static/src/xml/one_drive_dashboard.xml',
            'one_drive_odoo_integration/static/src/xml/one_drive_documentation.xml',
        ],
    },
}
