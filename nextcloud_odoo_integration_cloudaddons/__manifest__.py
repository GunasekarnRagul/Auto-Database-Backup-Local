# -*- coding: utf-8 -*-
{
    'name': 'Cloud Storage | Nextcloud',
    'version': '17.0.1.0.1',
    'category': 'Document Management',
    'summary': 'Connect Odoo with DropBox to automatically upload, sync, organize, and manage documents across both platforms.',
    'description': """
        This module provides integration between Odoo and Nextcloud.
        - OAuth 2.0 Authentication.
        - Automatic upload of Odoo attachments to Nextcloud.
        - Sync files from Nextcloud back to Odoo.
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
        'data/nextcloud_model_config.xml',
        'wizard/nextcloud_folder_wizard_views.xml',
        'wizard/nextcloud_file_delete_wizard_views.xml',
        'wizard/duplicate_pruner_wizard_views.xml',
        'views/nextcloud_dashboard_views.xml',
        'views/attachment_sync_config_views.xml',
        'views/nextcloud_config_views.xml',
        'views/nextcloud_file_views.xml',
        'views/nextcloud_model_config_views.xml',
        'views/ir_attachment_views.xml',
        'views/client_action_views.xml',
        'views/nextcloud_sync_log_views.xml',
        'views/nextcloud_documentation_views.xml',
        'views/menu.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend': [
            # Third-party libraries
            'nextcloud_odoo_integration_cloudaddons/static/lib/chart.min.js',
            # Module CSS
            'nextcloud_odoo_integration_cloudaddons/static/src/css/common_font.css',
            'nextcloud_odoo_integration_cloudaddons/static/src/css/kanban_premium.css',
            'nextcloud_odoo_integration_cloudaddons/static/src/css/file_explorer.css',
            'nextcloud_odoo_integration_cloudaddons/static/src/css/file_manager.css',
            'nextcloud_odoo_integration_cloudaddons/static/src/css/sync_log_terminal.css',
            'nextcloud_odoo_integration_cloudaddons/static/src/css/nextcloud_dashboard.css',
            'nextcloud_odoo_integration_cloudaddons/static/src/css/nextcloud_documentation.css',
            # Module JS
            'nextcloud_odoo_integration_cloudaddons/static/src/js/file_explorer/file_explorer.js',
            'nextcloud_odoo_integration_cloudaddons/static/src/js/nextcloud_config_form.js',
            'nextcloud_odoo_integration_cloudaddons/static/src/js/file_explorer/trash_restricted_dialog.js',
            'nextcloud_odoo_integration_cloudaddons/static/src/js/sync_log_terminal.js',
            'nextcloud_odoo_integration_cloudaddons/static/src/js/dashboard/nextcloud_dashboard.js',
            'nextcloud_odoo_integration_cloudaddons/static/src/js/attachment_upload_button.js',
            'nextcloud_odoo_integration_cloudaddons/static/src/js/documentation/nextcloud_documentation.js',
            # Module XML templates
            'nextcloud_odoo_integration_cloudaddons/static/src/xml/file_explorer.xml',
            'nextcloud_odoo_integration_cloudaddons/static/src/xml/trash_restricted_dialog.xml',
            'nextcloud_odoo_integration_cloudaddons/static/src/xml/sync_log_terminal.xml',
            'nextcloud_odoo_integration_cloudaddons/static/src/xml/nextcloud_dashboard.xml',
            'nextcloud_odoo_integration_cloudaddons/static/src/xml/nextcloud_documentation.xml',
        ],
    },
}
