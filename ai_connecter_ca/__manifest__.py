{
    'name': 'AI Agents Base: ChatGPT, Gemini & Claude',
    'version': '18.0.1.0.0',
    'category': 'Extra Tools',
    'summary': 'Connect to ChatGPT, Gemini, and Claude AI agents',
    'description': """
        This module allows you to configure API keys for:
        * OpenAI (ChatGPT)
        * Google (Gemini)
        * Anthropic (Claude)
        The keys are managed in the settings page.
    """,
    'author': 'CloudAddons Technologies',
    'depends': ['base', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'OPL-1',
    'price': 10.00,
    'currency': 'USD',
}
