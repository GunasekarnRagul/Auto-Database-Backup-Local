# -*- coding: utf-8 -*-
{
    'name': 'Cloud Storage Hub | AWS S3 · Google Drive · OneDrive · Nextcloud · Dropbox | All-in-One Cloud Connector',
    'version': '19.0.1.0.0',
    'category': 'Document Management',
    'summary': 'Connect Odoo with all 5 major cloud providers in one unified module — AWS S3, Google Drive, OneDrive, Nextcloud, Dropbox.',
    'description': """
        Cloud Storage Hub — Unified Multi-Provider Cloud Connector for Odoo 19.

        Connect and manage all your cloud storage providers from a single module:
        - AWS S3 (Access Key + Secret Key)
        - Google Drive (OAuth 2.0)
        - Microsoft OneDrive (OAuth 2.0)
        - Nextcloud (WebDAV / Basic Auth)
        - Dropbox (OAuth 2.0)

        Features:
        - Single provider dropdown to switch between connected accounts
        - Unified Dashboard with KPIs across all providers
        - Unified File Explorer with provider switcher
        - Attachment auto-sync rules per Odoo model
        - Scheduled cron sync
        - Activity log across all providers
    """,
    'author': 'CloudAddons Technologies',
    'support': 'cloudaddonstechnologies@gmail.com',
    'depends': ['base', 'mail', 'bus'],
    'price': 199.00,
    'currency': 'USD',
    'images': ['static/description/main_screenshot.png'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron_sync.xml',
        'data/cloud_model_config.xml',
        'wizard/cloud_folder_wizard_views.xml',
        'wizard/cloud_file_delete_wizard_views.xml',
        'wizard/duplicate_pruner_wizard_views.xml',
        'views/cloud_provider_config_views.xml',
        'views/cloud_dashboard_views.xml',
        'views/cloud_attachment_config_views.xml',
        'views/cloud_file_views.xml',
        'views/cloud_model_config_views.xml',
        'views/ir_attachment_views.xml',
        'views/client_action_views.xml',
        'views/cloud_sync_log_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend': [
            # Third-party libraries
            'cloud_storage_hub/static/lib/chart.min.js',
            # Module CSS
            'cloud_storage_hub/static/src/css/common_font.css',
            'cloud_storage_hub/static/src/css/kanban_premium.css',
            'cloud_storage_hub/static/src/css/file_explorer.css',
            'cloud_storage_hub/static/src/css/file_manager.css',
            'cloud_storage_hub/static/src/css/sync_log_terminal.css',
            'cloud_storage_hub/static/src/css/cloud_hub_dashboard.css',
            # Module JS
            'cloud_storage_hub/static/src/js/file_explorer/file_explorer.js',
            'cloud_storage_hub/static/src/js/cloud_config_form.js',
            'cloud_storage_hub/static/src/js/file_explorer/trash_restricted_dialog.js',
            'cloud_storage_hub/static/src/js/sync_log_terminal.js',
            'cloud_storage_hub/static/src/js/dashboard/cloud_hub_dashboard.js',

            'cloud_storage_hub/static/src/js/attachment_upload_button.js',
            'cloud_storage_hub/static/src/js/documentation/cloud_hub_documentation.js',
            # Module XML templates
            'cloud_storage_hub/static/src/xml/file_explorer.xml',
            'cloud_storage_hub/static/src/xml/trash_restricted_dialog.xml',
            'cloud_storage_hub/static/src/xml/sync_log_terminal.xml',
            'cloud_storage_hub/static/src/xml/cloud_hub_dashboard.xml',
            'cloud_storage_hub/static/src/xml/cloud_hub_documentation.xml',
        ],
    },
}
