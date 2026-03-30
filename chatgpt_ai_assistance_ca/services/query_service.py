from datetime import date, timedelta
from dateutil.relativedelta import relativedelta


class QueryService:

    def __init__(self, env):
        self.env = env

    def execute(self, intent_dict, agent_id):
        """
        Execute a read-only ORM query based on the parsed intent from ChatGPT.
        ONLY uses search_read(). Never create/write/unlink.
        """
        # STEP 1 — Validate intent_type
        intent_type = intent_dict.get('intent_type')
        if intent_type == 'refuse':
            return None
        if intent_type not in ('read', 'report'):
            return None

        target_model = intent_dict.get('target_model')

        # STEP 2 — Validate model permission
        permission = self.env['ai.agent.permission'].search([
            ('agent_id', '=', agent_id),
            ('model_name', '=', target_model),
            ('can_read', '=', True),
        ], limit=1)

        if not permission:
            model_rec = self.env['ir.model'].search(
                [('model', '=', target_model)], limit=1
            )
            label = model_rec.name if model_rec else target_model
            return {
                'error': 'permission_denied',
                'message': (
                    f"I don't have access to read from {label}. "
                    f"Ask your administrator to enable it in the AI Agent "
                    f"configuration."
                ),
            }

        # STEP 3 — Resolve relative date tokens in domain
        domain = self._resolve_dates(intent_dict.get('domain', []))

        # STEP 4 — Execute ORM query (ONLY search_read)
        try:
            result = self.env[target_model].search_read(
                domain=domain,
                fields=intent_dict.get('fields', []),
                order=intent_dict.get('order', 'id desc'),
                limit=intent_dict.get('limit', 100),
            )
        except Exception as e:
            return {
                'error': 'query_error',
                'message': f'Query failed: {str(e)}',
            }

        # STEP 5 — Clean many2one tuples → just the name string
        for record in result:
            for key, value in record.items():
                if (isinstance(value, (list, tuple))
                        and len(value) == 2
                        and isinstance(value[0], int)):
                    record[key] = value[1]

        return result

    def _resolve_dates(self, domain):
        """Replace string date tokens with real Python date objects."""
        today = date.today()
        token_map = {
            'today': today,
            'yesterday': today - timedelta(days=1),
            'this_week_start': today - timedelta(days=today.weekday()),
            'this_week_end': (
                today - timedelta(days=today.weekday()) + timedelta(days=6)
            ),
            'last_month_start': (
                today.replace(day=1) - relativedelta(months=1)
            ),
            'last_month_end': today.replace(day=1) - timedelta(days=1),
            'this_year_start': today.replace(month=1, day=1),
            'this_year_end': today.replace(month=12, day=31),
        }
        resolved = []
        for clause in domain:
            if isinstance(clause, (list, tuple)) and len(clause) == 3:
                field, op, value = clause
                if isinstance(value, str) and value in token_map:
                    # Convert to ISO date string for ORM
                    value = str(token_map[value])
                resolved.append([field, op, value])
            else:
                resolved.append(clause)
        return resolved
