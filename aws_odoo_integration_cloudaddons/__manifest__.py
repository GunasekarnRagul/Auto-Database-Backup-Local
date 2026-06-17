# -*- coding: utf-8 -*-
{
    'name': 'Cloud Storage | AWS S3',
    'version': '17.0.1.0.2',
    'category': 'Document Management',
    'summary': 'Connect Odoo with AWS S3 to automatically upload, sync, organize, and manage documents across both platforms.',
    'description': """
        This module provides integration between Odoo and AWS S3.
        - OAuth 2.0 Authentication.
        - Automatic upload of Odoo attachments to AWS S3.
        - Sync files from AWS S3 back to Odoo.
    """,
    'author': 'CloudAddons Technologies',
    'depends': ['base', 'mail', 'bus', 'base_setup'],
    'price': 147.00,
    'currency': 'USD',
    'support': 'cloudaddonstechnologies@gmail.com',
    'images': ['static/description/main_screenshot.png'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron_sync.xml',
        'data/aws_model_config.xml',
        'wizard/aws_folder_wizard_views.xml',
        'wizard/aws_file_delete_wizard_views.xml',
        'wizard/duplicate_pruner_wizard_views.xml',
        'views/aws_dashboard_views.xml',
        'views/attachment_sync_config_views.xml',
        'views/aws_config_views.xml',
        'views/aws_file_views.xml',
        'views/aws_model_config_views.xml',
        'views/ir_attachment_views.xml',
        'views/client_action_views.xml',
        'views/aws_sync_log_views.xml',
        'views/aws_documentation_views.xml',
        'views/res_config_settings_views.xml',
        'views/menu.xml',
    ],
    'pre_init_hook': 'pre_init_hook',
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend': [
            # Third-party libraries
            'aws_odoo_integration_cloudaddons/static/lib/chart.min.js',
            # Module CSS
            'aws_odoo_integration_cloudaddons/static/src/css/common_font.css',
            'aws_odoo_integration_cloudaddons/static/src/css/kanban_premium.css',
            'aws_odoo_integration_cloudaddons/static/src/css/file_explorer.css',
            'aws_odoo_integration_cloudaddons/static/src/css/file_manager.css',
            'aws_odoo_integration_cloudaddons/static/src/css/sync_log_terminal.css',
            'aws_odoo_integration_cloudaddons/static/src/css/aws_dashboard.css',
            'aws_odoo_integration_cloudaddons/static/src/css/aws_documentation.css',
            # Module JS
            'aws_odoo_integration_cloudaddons/static/src/js/file_explorer/file_explorer.js',
            'aws_odoo_integration_cloudaddons/static/src/js/aws_config_form.js',
            'aws_odoo_integration_cloudaddons/static/src/js/file_explorer/trash_restricted_dialog.js',
            'aws_odoo_integration_cloudaddons/static/src/js/sync_log_terminal.js',
            'aws_odoo_integration_cloudaddons/static/src/js/dashboard/aws_dashboard.js',
            'aws_odoo_integration_cloudaddons/static/src/js/attachment_upload_button.js',
            'aws_odoo_integration_cloudaddons/static/src/js/documentation/aws_documentation.js',
            # Module XML templates
            'aws_odoo_integration_cloudaddons/static/src/xml/file_explorer.xml',
            'aws_odoo_integration_cloudaddons/static/src/xml/trash_restricted_dialog.xml',
            'aws_odoo_integration_cloudaddons/static/src/xml/sync_log_terminal.xml',
            'aws_odoo_integration_cloudaddons/static/src/xml/aws_dashboard.xml',
            'aws_odoo_integration_cloudaddons/static/src/xml/aws_documentation.xml',
        ],
    },
}
