# -*- coding: utf-8 -*-
from odoo import models, fields, api

class DuplicatePrunerWizard(models.TransientModel):
    _name = 'duplicate.pruner.wizard'
    _description = 'Duplicate File Pruner Wizard'

    duplicate_groups_count = fields.Integer('Number of Groups', readonly=True)
    duplicate_count = fields.Integer('Total Files to Delete', readonly=True)
    summary_text = fields.Text('Duplicate Details', readonly=True)

    prune_strategy = fields.Selection([
        ('oldest', 'Keep Oldest Record (Recommended)'),
        ('newest', 'Keep Newest Record'),
        ('manual', 'Manual Selection (Review List)')
    ], string='Pruning Strategy', default='oldest', required=True,
       help="Choose how the pruner decides which file to keep out of the duplicate groups.")

    @api.model
    def default_get(self, fields_list):
        res = super(DuplicatePrunerWizard, self).default_get(fields_list)
        groups = self.env['google.drive.file'].get_duplicate_groups()
        groups_count = len(groups)
        dup_count = sum(len(g['records']) for g in groups) - groups_count if groups_count > 0 else 0
        
        detail_lines = []
        for g in groups:
            detail_lines.append(f"• '{g['name']}' in {g['parent_name']} ({g['count']} total copies)")

        res.update({
            'duplicate_groups_count': groups_count,
            'duplicate_count': dup_count,
            'summary_text': "Found Duplicate File Groups:\n" + "\n".join(detail_lines) if groups_count else "No duplicates found."
        })
        return res

    def action_prune_duplicates(self):
        if self.duplicate_groups_count > 0:
            if self.prune_strategy == 'oldest':
                self.env['google.drive.file'].prune_duplicates(keep_newest=False)
                return {'type': 'ir.actions.client', 'tag': 'reload'}
            elif self.prune_strategy == 'newest':
                self.env['google.drive.file'].prune_duplicates(keep_newest=True)
                return {'type': 'ir.actions.client', 'tag': 'reload'}
            elif self.prune_strategy == 'manual':
                groups = self.env['google.drive.file'].get_duplicate_groups()
                duplicate_ids = []
                for group in groups:
                    duplicate_ids.extend(group['duplicate_ids'])
                
                return {
                    'name': 'Duplicate Files for Manual Pruning',
                    'type': 'ir.actions.act_window',
                    'res_model': 'google.drive.file',
                    'view_mode': 'tree,form',
                    'domain': [('id', 'in', duplicate_ids)],
                    'context': {'create': False, 'active_test': False}
                }
        return {'type': 'ir.actions.client', 'tag': 'reload'}
