from odoo import models, fields, api
import requests


class AiConnector(models.Model):
    _name = 'ai.connector'
    _description = 'AI Connector'

    name = fields.Char(required=True, default='Default Connector')
    api_key = fields.Char(required=True, string='OpenAI API Key')
    api_base_url = fields.Char(default='https://api.openai.com/v1')
    model_name = fields.Selection(
        selection=[
            ('gpt-5-nano', 'GPT-5 Nano'),
            ('gpt-4o', 'GPT-4o'),
            ('gpt-4-turbo', 'GPT-4 Turbo'),
            ('gpt-3.5-turbo', 'GPT-3.5 Turbo'),
        ],
        required=True,
        default='gpt-5-nano',
    )
    request_timeout = fields.Integer(default=30, string='Timeout (seconds)')
    active = fields.Boolean(default=True)
    status_message = fields.Char(readonly=True)
    connection_status = fields.Selection(
        selection=[
            ('not_tested', 'Not Tested'),
            ('connected', 'Connected'),
            ('failed', 'Failed'),
        ],
        default='not_tested',
        readonly=True,
    )
    is_active_connection = fields.Boolean(
        compute='_compute_is_active', store=False,
    )

    @api.depends('connection_status')
    def _compute_is_active(self):
        for rec in self:
            rec.is_active_connection = (rec.connection_status == 'connected')

    def action_test_connection(self):
        self.ensure_one()
        try:
            headers = {'Authorization': f'Bearer {self.api_key}'}
            url = f'{self.api_base_url}/models'
            resp = requests.get(url, headers=headers, timeout=self.request_timeout)
            resp.raise_for_status()
            self.connection_status = 'connected'
            self.status_message = 'Connection successful'
        except Exception as e:
            self.connection_status = 'failed'
            self.status_message = f'Connection failed: {str(e)}'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Connection Test',
                'message': self.status_message,
                'type': 'success' if self.connection_status == 'connected' else 'danger',
            },
        }

