from odoo import models, fields, api


class AiAgent(models.Model):
    _name = 'ai.agent'
    _description = 'AI Agent'

    name = fields.Char(required=True)
    system_prompt = fields.Text(required=True, string='Agent Instructions / Persona')
    temperature = fields.Float(default=0.3, digits=(2, 1))
    max_tokens = fields.Integer(default=1500)
    language = fields.Selection(
        selection=[
            ('en', 'English'),
            ('ar', 'Arabic'),
            ('fr', 'French'),
            ('auto', 'Auto-detect'),
        ],
        default='en',
    )
    response_format = fields.Selection(
        selection=[
            ('markdown', 'Markdown'),
            ('plain', 'Plain Text'),
        ],
        default='markdown',
    )
    is_default = fields.Boolean(default=False, string='Set as Default Agent')
    active = fields.Boolean(default=True)
    permission_ids = fields.One2many(
        comodel_name='ai.agent.permission',
        inverse_name='agent_id',
        string='Model Access',
    )

    def write(self, vals):
        res = super().write(vals)
        if vals.get('is_default'):
            # Ensure only one default agent at a time
            self.search([
                ('id', '!=', self.id),
                ('is_default', '=', True),
            ]).write({'is_default': False})
        return res
