# -*- coding: utf-8 -*-
{
    'name': 'Cloud Storage | Dropbox',
    'version': '17.0.1.0.0',
    'category': 'Document Management',
    'summary': 'Connect Odoo with DropBox to automatically upload, sync, organize, and manage documents across both platforms.',
    'description': """
        This module provides integration between Odoo and Dropbox.
        - OAuth 2.0 Authentication.
        - Automatic upload of Odoo attachments to Dropbox.
        - Sync files from Dropbox back to Odoo.
    """,
    'author': 'CloudAddons Technologies',
    'depends': ['base', 'mail', 'bus'],
    'price': 147.00,
    'currency': 'USD',
    'support': 'cloudaddonstechnologies@gmail.com',
    'images': ['static/description/main_screenshot.png'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron_sync.xml',
        'data/dropbox_model_config.xml',
        'wizard/dropbox_folder_wizard_views.xml',
        'wizard/dropbox_file_delete_wizard_views.xml',
        'wizard/duplicate_pruner_wizard_views.xml',
        'views/dropbox_dashboard_views.xml',
        'views/attachment_sync_config_views.xml',
        'views/dropbox_config_views.xml',
        'views/dropbox_file_views.xml',
        'views/dropbox_model_config_views.xml',
        'views/ir_attachment_views.xml',
        'views/client_action_views.xml',
        'views/dropbox_sync_log_views.xml',
        'views/dropbox_documentation_views.xml',
        'views/menu.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend': [
            # Third-party libraries
            'dropbox_odoo_integration/static/lib/chart.min.js',
            # Module CSS
            'dropbox_odoo_integration/static/src/css/common_font.css',
            'dropbox_odoo_integration/static/src/css/kanban_premium.css',
            'dropbox_odoo_integration/static/src/css/file_explorer.css',
            'dropbox_odoo_integration/static/src/css/file_manager.css',
            'dropbox_odoo_integration/static/src/css/sync_log_terminal.css',
            'dropbox_odoo_integration/static/src/css/dropbox_dashboard.css',
            'dropbox_odoo_integration/static/src/css/dropbox_documentation.css',
            # Module JS
            'dropbox_odoo_integration/static/src/js/file_explorer/file_explorer.js',
            'dropbox_odoo_integration/static/src/js/dropbox_config_form.js',
            'dropbox_odoo_integration/static/src/js/file_explorer/trash_restricted_dialog.js',
            'dropbox_odoo_integration/static/src/js/sync_log_terminal.js',
            'dropbox_odoo_integration/static/src/js/dashboard/dropbox_dashboard.js',
            'dropbox_odoo_integration/static/src/js/attachment_upload_button.js',
            'dropbox_odoo_integration/static/src/js/documentation/dropbox_documentation.js',
            # Module XML templates
            'dropbox_odoo_integration/static/src/xml/file_explorer.xml',
            'dropbox_odoo_integration/static/src/xml/trash_restricted_dialog.xml',
            'dropbox_odoo_integration/static/src/xml/sync_log_terminal.xml',
            'dropbox_odoo_integration/static/src/xml/dropbox_dashboard.xml',
            'dropbox_odoo_integration/static/src/xml/dropbox_documentation.xml',
        ],
    },
}
