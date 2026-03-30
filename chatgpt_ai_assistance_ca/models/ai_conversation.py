from odoo import models, fields, api


class AiConversation(models.Model):
    _name = 'ai.conversation'
    _description = 'AI Conversation'

    name = fields.Char(required=True, string='Conversation Title')
    user_id = fields.Many2one(
        comodel_name='res.users',
        default=lambda self: self.env.user,
        required=True,
        ondelete='cascade',
    )
    agent_id = fields.Many2one(
        comodel_name='ai.agent', string='AI Agent Used',
    )
    message_ids = fields.One2many(
        comodel_name='ai.message', inverse_name='conversation_id',
    )
    is_favorite = fields.Boolean(default=False, string='Starred')
    ref_model = fields.Char(string='Source Model')
    ref_record_id = fields.Integer(string='Source Record ID')
    create_date = fields.Datetime(readonly=True)
    message_count = fields.Integer(
        compute='_compute_message_count', store=True,
    )

    @api.depends('message_ids')
    def _compute_message_count(self):
        for rec in self:
            rec.message_count = len(rec.message_ids)

    def action_open_in_chat(self):
        """Open this conversation in the Chat AI interface."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'ChatAIComponent',
            'context': {'default_conversation_id': self.id},
        }

    def action_export(self):
        """Export conversation as a downloadable text file."""
        self.ensure_one()
        import urllib.parse
        lines = []
        for msg in self.message_ids.sorted('create_date'):
            role = 'User' if msg.role == 'user' else 'Assistant'
            lines.append(f'{role}: {msg.content}')
            lines.append('')
        content = '\n'.join(lines)
        encoded = urllib.parse.quote(content)
        return {
            'type': 'ir.actions.act_url',
            'url': f'data:text/plain;charset=utf-8,{encoded}',
            'target': 'new',
        }

