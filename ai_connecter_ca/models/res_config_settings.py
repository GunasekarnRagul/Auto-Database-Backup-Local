import requests
import json
from odoo import fields, models, _
from odoo.exceptions import UserError
try:
    from odoo.tools import Markup
except ImportError:
    try:
        from markupsafe import Markup
    except ImportError:
        Markup = lambda x: x

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    openai_api_key = fields.Char(
        string='OpenAI API Key',
        config_parameter='ai_connecter_ca.openai_api_key',
        help="API Key for OpenAI (ChatGPT)"
    )
    gemini_api_key = fields.Char(
        string='Gemini API Key',
        config_parameter='ai_connecter_ca.gemini_api_key',
        help="API Key for Google Gemini"
    )
    claude_api_key = fields.Char(
        string='Claude API Key',
        config_parameter='ai_connecter_ca.claude_api_key',
        help="API Key for Anthropic Claude"
    )

    def action_test_openai_connection(self):
        if not self.openai_api_key:
            raise UserError(_("Please enter an OpenAI API Key."))
        
        url = "https://api.openai.com/v1/models"
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}"
        }
        try:
            response = requests.get(url, headers=headers, timeout=10)
        except Exception as e:
            raise UserError(_("ChatGPT Connection Error: %s") % str(e))

        if response.status_code == 200:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('ChatGPT Success'),
                    'message': _('ChatGPT Connection Successful!'),
                    'sticky': False,
                    'type': 'success',
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('ChatGPT Connection Failed'),
                    'message': _('Invalid API Key! Please check your credentials.'),
                    'sticky': True,
                    'type': 'danger',
                }
            }

    def action_test_gemini_connection(self):
        if not self.gemini_api_key:
            raise UserError(_("Please enter a Gemini API Key."))
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.gemini_api_key}"
        try:
            response = requests.get(url, timeout=10)
        except Exception as e:
            raise UserError(_("Gemini Connection Error: %s") % str(e))

        if response.status_code == 200:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Gemini Success'),
                    'message': _('Gemini Connection Successful!'),
                    'sticky': False,
                    'type': 'success',
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Gemini Connection Failed'),
                    'message': _('Invalid API Key! Please check your credentials.'),
                    'sticky': True,
                    'type': 'danger',
                }
            }

    def action_test_claude_connection(self):
        if not self.claude_api_key:
            raise UserError(_("Please enter a Claude API Key."))
        
        url = "https://api.anthropic.com/v1/models"
        headers = {
            "x-api-key": self.claude_api_key,
            "anthropic-version": "2023-06-01"
        }
        try:
            response = requests.get(url, headers=headers, timeout=10)
        except Exception as e:
            raise UserError(_("Claude Connection Error: %s") % str(e))

        if response.status_code == 200:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Claude Success'),
                    'message': _('Claude Connection Successful!'),
                    'sticky': False,
                    'type': 'success',
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Claude Connection Failed'),
                    'message': _('Invalid API Key! Please check your credentials.'),
                    'sticky': True,
                    'type': 'danger',
                }
            }
