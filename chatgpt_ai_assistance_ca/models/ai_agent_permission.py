from odoo import models, fields


class AiAgentPermission(models.Model):
    _name = 'ai.agent.permission'
    _description = 'AI Agent Permission'

    agent_id = fields.Many2one(
        comodel_name='ai.agent', required=True, ondelete='cascade',
    )
    model_id = fields.Many2one(
        comodel_name='ir.model', required=True, ondelete='cascade',
        string='Odoo Model',
    )
    can_read = fields.Boolean(default=True, string='Allow Read Access')
    model_name = fields.Char(
        related='model_id.model', store=True, readonly=True,
        string='Technical Name',
    )

    _sql_constraints = [
        ('unique_agent_model', 'UNIQUE(agent_id, model_id)',
         'Each model can only appear once per agent.'),
    ]
