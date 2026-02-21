# -*- coding: utf-8 -*-
{
    'name': 'WhatsApp Integration | Base',
    'version': '19.0.1.0.0',
    "author": "CloudAddons Technologies",
    'category': 'Marketing',
    'depends': ['base', 'mail'],
    'price': 10.0,
    'currency': 'USD',
    'images': ['static/description/banner.png'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron_data.xml',
        'wizard/whatsapp_import_wizard_views.xml',
        'wizard/whatsapp_composer_views.xml',
        'views/whatsapp_config_views.xml',
        'views/whatsapp_message_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'whatsapp_integration_ca/static/src/css/whatsapp.css',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'OPL-1',
}
