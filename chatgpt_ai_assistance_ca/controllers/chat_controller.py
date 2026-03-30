import json

from odoo import http
from odoo.http import request

from odoo.addons.chatgpt_ai_assistance_ca.services.schema_service import OdooSchemaService
from odoo.addons.chatgpt_ai_assistance_ca.services.openai_service import OpenAIService
from odoo.addons.chatgpt_ai_assistance_ca.services.query_service import QueryService


class ChatController(http.Controller):

    # ------------------------------------------------------------------
    # ENDPOINT 1: /chatgpt_ai/send_message
    # ------------------------------------------------------------------
    @http.route('/chatgpt_ai/send_message', type='json', auth='user',
                methods=['POST'], csrf=False)
    def send_message(self, conversation_id=None, message=''):
        env = request.env

        # 1. Load active connector
        connector = env['ai.connector'].search(
            [('active', '=', True)], limit=1
        )
        if not connector:
            return {
                'error': 'No connector configured. '
                         'Go to Configuration > Connector.',
            }

        # 2. Load default agent
        agent = env['ai.agent'].search(
            [('is_default', '=', True)], limit=1
        )
        if not agent:
            return {
                'error': 'No default AI Agent. '
                         'Go to Configuration > AI Agent.',
            }

        # 3. Load or create conversation
        if conversation_id:
            conversation = env['ai.conversation'].search([
                ('id', '=', conversation_id),
                ('user_id', '=', env.user.id),
            ], limit=1)
            if not conversation:
                return {'error': 'Conversation not found.'}
        else:
            conversation = env['ai.conversation'].create({
                'name': 'New Conversation',
                'user_id': env.user.id,
                'agent_id': agent.id,
            })

        # 4. Save user message
        env['ai.message'].create({
            'conversation_id': conversation.id,
            'role': 'user',
            'content': message,
        })

        # 5. Update conversation name from first message
        if conversation.name == 'New Conversation':
            conversation.name = message[:60]

        # 6. Get schema
        schema = OdooSchemaService().get_schema(env, agent.id)

        # 7. Detect intent
        try:
            intent_dict = OpenAIService(connector, agent).detect_intent(
                message, schema
            )
        except Exception as e:
            return {'error': f'AI service error: {str(e)}'}

        # 8. Handle refuse
        if intent_dict.get('intent_type') == 'refuse':
            reason = intent_dict.get(
                'refuse_reason', 'I cannot help with that request.'
            )
            msg = env['ai.message'].create({
                'conversation_id': conversation.id,
                'role': 'assistant',
                'content': reason,
                'response_format': 'error',
            })
            return {
                'conversation_id': conversation.id,
                'narration': reason,
                'response_format': 'error',
                'records': [],
                'fields': [],
                'message_id': msg.id,
            }

        # 9. Execute query
        result = QueryService(env).execute(intent_dict, agent.id)

        # 10. Handle permission denied or query error
        if isinstance(result, dict) and result.get('error'):
            msg = env['ai.message'].create({
                'conversation_id': conversation.id,
                'role': 'assistant',
                'content': result['message'],
                'response_format': 'error',
            })
            return {
                'conversation_id': conversation.id,
                'narration': result['message'],
                'response_format': 'error',
                'records': [],
                'fields': [],
                'message_id': msg.id,
            }

        # 11. Generate narration
        try:
            narration = OpenAIService(connector, agent).generate_narration(
                message, result or [], intent_dict, agent
            )
        except Exception as e:
            narration = f'Data retrieved successfully. ({str(e)})'

        # 12. Save assistant message
        msg = env['ai.message'].create({
            'conversation_id': conversation.id,
            'role': 'assistant',
            'content': narration,
            'response_format': intent_dict.get('response_format', 'text'),
            'raw_data': json.dumps(result or [], default=str),
        })

        # 13. Return response (NEVER include api_key)
        return {
            'conversation_id': conversation.id,
            'narration': narration,
            'response_format': intent_dict.get('response_format', 'text'),
            'records': result or [],
            'fields': intent_dict.get('fields', []),
            'message_id': msg.id,
        }

    # ------------------------------------------------------------------
    # ENDPOINT 2: /chatgpt_ai/get_conversations
    # ------------------------------------------------------------------
    @http.route('/chatgpt_ai/get_conversations', type='json', auth='user',
                methods=['GET', 'POST'], csrf=False)
    def get_conversations(self):
        env = request.env
        convs = env['ai.conversation'].search(
            [('user_id', '=', env.user.id)],
            order='create_date desc',
        )
        return [{
            'id': c.id,
            'name': c.name,
            'create_date': (
                c.create_date.isoformat() if c.create_date else ''
            ),
            'message_count': c.message_count,
            'is_favorite': c.is_favorite,
        } for c in convs]

    # ------------------------------------------------------------------
    # ENDPOINT 3: /chatgpt_ai/get_messages
    # ------------------------------------------------------------------
    @http.route('/chatgpt_ai/get_messages', type='json', auth='user',
                methods=['POST'], csrf=False)
    def get_messages(self, conversation_id):
        env = request.env
        conv = env['ai.conversation'].search([
            ('id', '=', conversation_id),
            ('user_id', '=', env.user.id),
        ], limit=1)
        if not conv:
            return {'error': 'Conversation not found.'}
        return [{
            'id': m.id,
            'role': m.role,
            'content': m.content,
            'response_format': m.response_format,
            'raw_data': m.raw_data,
            'create_date': (
                m.create_date.isoformat() if m.create_date else ''
            ),
        } for m in conv.message_ids.sorted('create_date')]

    # ------------------------------------------------------------------
    # ENDPOINT 4: /chatgpt_ai/save_favorite
    # ------------------------------------------------------------------
    @http.route('/chatgpt_ai/save_favorite', type='json', auth='user',
                methods=['POST'], csrf=False)
    def save_favorite(self, prompt_text, label):
        env = request.env
        fav = env['ai.favorite.prompt'].create({
            'name': label,
            'prompt_text': prompt_text,
            'user_id': env.user.id,
        })
        return {'id': fav.id, 'success': True}

    # ------------------------------------------------------------------
    # ENDPOINT 5: /chatgpt_ai/get_favorites
    # ------------------------------------------------------------------
    @http.route('/chatgpt_ai/get_favorites', type='json', auth='user',
                methods=['GET', 'POST'], csrf=False)
    def get_favorites(self):
        env = request.env
        favs = env['ai.favorite.prompt'].search([
            '|',
            ('user_id', '=', env.user.id),
            ('is_global', '=', True),
        ], order='sequence asc')
        return [{
            'id': f.id,
            'name': f.name,
            'prompt_text': f.prompt_text,
            'is_global': f.is_global,
        } for f in favs]
