from datetime import datetime

_schema_cache = {}  # module-level cache: {agent_id: (timestamp, schema_dict)}
CACHE_TTL_HOURS = 24


class OdooSchemaService:

    def get_schema(self, env, agent_id):
        """
        Returns a dict of all Odoo models + fields the agent is allowed to read.
        This dict is sent to ChatGPT so it knows what data is available.
        Cached for 24 hours per agent_id.
        """
        # Check cache
        if agent_id in _schema_cache:
            cached_time, cached_schema = _schema_cache[agent_id]
            age_hours = (datetime.now() - cached_time).total_seconds() / 3600
            if age_hours < CACHE_TTL_HOURS:
                return cached_schema

        # Fetch permitted models for this agent
        permissions = env['ai.agent.permission'].search([
            ('agent_id', '=', agent_id),
            ('can_read', '=', True),
        ])

        schema = {}
        for perm in permissions:
            model_name = perm.model_id.model
            model_label = perm.model_id.name

            # Get fields for this model (skip one2many and many2many to keep schema small)
            fields = env['ir.model.fields'].search([
                ('model_id', '=', perm.model_id.id),
                ('ttype', 'not in', ['one2many', 'many2many']),
            ])

            field_dict = {}
            for f in fields:
                entry = {
                    'type': f.ttype,
                    'label': f.field_description,
                }
                if f.ttype == 'many2one' and f.relation:
                    entry['relation'] = f.relation
                if f.ttype == 'selection':
                    # Get selection values
                    try:
                        sel = env[model_name]._fields[f.name].selection
                        if callable(sel):
                            sel = sel(env[model_name])
                        entry['selection'] = [[k, v] for k, v in sel]
                    except Exception:
                        pass
                field_dict[f.name] = entry

            schema[model_name] = {
                'label': model_label,
                'fields': field_dict,
            }

        # Store in cache
        _schema_cache[agent_id] = (datetime.now(), schema)
        return schema
