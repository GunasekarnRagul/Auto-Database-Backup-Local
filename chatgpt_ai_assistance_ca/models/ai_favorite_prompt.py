from odoo import models, fields


class AiFavoritePrompt(models.Model):
    _name = 'ai.favorite.prompt'
    _description = 'AI Favorite Prompt'

    name = fields.Char(required=True, string='Label')
    prompt_text = fields.Text(required=True, string='Prompt')
    user_id = fields.Many2one(
        comodel_name='res.users',
        default=lambda self: self.env.user,
        required=True,
        ondelete='cascade',
    )
    is_global = fields.Boolean(default=False, string='Global (visible to all users)')
    sequence = fields.Integer(default=10, string='Order')
