from odoo import models, fields


class AiMessage(models.Model):
    _name = 'ai.message'
    _description = 'AI Message'

    conversation_id = fields.Many2one(
        comodel_name='ai.conversation', required=True, ondelete='cascade',
    )
    role = fields.Selection(
        selection=[
            ('user', 'User'),
            ('assistant', 'Assistant'),
        ],
        required=True,
    )
    content = fields.Text(required=True)
    response_format = fields.Selection(
        selection=[
            ('table', 'Table'),
            ('list', 'List'),
            ('text', 'Text'),
            ('card', 'Card'),
            ('error', 'Error'),
        ],
        string='Display Format',
    )
    raw_data = fields.Text(string='Raw ORM Result (JSON)')
    create_date = fields.Datetime(readonly=True)
