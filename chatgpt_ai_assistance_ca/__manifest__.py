{
    'name': 'ChatGPT AI Assistance',
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Integrate ChatGPT AI into Odoo to query and analyze your business data using natural language.',
    'author': 'Your Name',
    'depends': ['base', 'mail', 'web', 'sale', 'account', 'purchase',
                'stock', 'crm', 'project', 'hr', 'hr_holidays'],
    'data': [
        
        'security/ir.model.access.csv',
        'views/chat_ai_main_view.xml',
        'views/ai_connector_views.xml',
        'views/ai_agent_views.xml',
        'views/ai_conversation_views.xml',
        'views/menus.xml',
        'data/default_models.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'chatgpt_ai_assistance_ca/static/src/css/chat_ai.css',
            'chatgpt_ai_assistance_ca/static/src/xml/chat_message.xml',
            'chatgpt_ai_assistance_ca/static/src/xml/drawer_component.xml',
            'chatgpt_ai_assistance_ca/static/src/xml/chat_ai_component.xml',
            'chatgpt_ai_assistance_ca/static/src/js/chat_message.js',
            'chatgpt_ai_assistance_ca/static/src/js/drawer_component.js',
            'chatgpt_ai_assistance_ca/static/src/js/chat_ai_component.js',
        ],
    },
    'license': 'LGPL-3',
    'installable': True,
    'application': True,
}
