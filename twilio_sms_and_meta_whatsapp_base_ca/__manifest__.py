# -*- coding: utf-8 -*-
{
    'name': 'Twilio SMS & Meta WhatsApp | Base',
    'version': '18.0.1.0.0',
    'category': 'Marketing',
    'summary': 'SMS (Twilio) & WhatsApp Cloud API',
    'description': """
        Send SMS and WhatsApp messages directly from Odoo.
        Supports SMS gateway: Twilio.
        WhatsApp Cloud API integration with multi-account support.
        Features: bulk messaging, scheduling, delivery reports, message queue.
    """,
    'author': 'CloudAddons Technologies',
    'depends': ['base', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'data/cron_data.xml',
        'wizard/whatsapp_import_wizard_views.xml',
        'views/twilio_config_views.xml',
        'views/whatsapp_config_views.xml',
        'views/whatsapp_message_views.xml',
        'views/sms_message_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'OPL-1',
    'price': 20,
    'currency': 'USD',
}
