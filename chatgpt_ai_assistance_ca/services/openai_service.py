import json
import requests


class OpenAIService:

    def __init__(self, connector, agent):
        self.connector = connector
        self.agent = agent

    def _build_headers(self):
        return {
            'Authorization': f'Bearer {self.connector.api_key}',
            'Content-Type': 'application/json',
        }

    def _is_responses_api(self):
        return self.connector.model_name == 'gpt-5-nano'

    def _get_url(self):
        endpoint = 'responses' if self._is_responses_api() else 'chat/completions'
        return f'{self.connector.api_base_url}/{endpoint}'

    def _prepare_payload(self, messages, **kwargs):
        if self._is_responses_api():
            # Responses API requires 'input' and 'store: True'
            return {
                'model': self.connector.model_name,
                'input': messages,
                'store': True,
            }
        payload = {
            'model': self.connector.model_name,
            'messages': messages,
        }
        payload.update(kwargs)
        return payload

    def _parse_response(self, resp):
        data = resp.json()
        if self._is_responses_api():
            # v1/responses structure: output[0].content[0].text
            try:
                return data['output'][0]['content'][0]['text']
            except (KeyError, IndexError):
                raise ValueError(
                    f"Unexpected response format from Responses API: {data}"
                )
        return data['choices'][0]['message']['content'].strip()

    def _schema_to_text(self, schema_dict):
        """Convert schema dict to a readable text block for ChatGPT."""
        lines = []
        for model, info in schema_dict.items():
            lines.append(f"\nModel: {model} ({info['label']})")
            for fname, finfo in info.get('fields', {}).items():
                line = f"  - {fname} ({finfo['type']}): {finfo['label']}"
                if finfo.get('relation'):
                    line += f" → {finfo['relation']}"
                if finfo.get('selection'):
                    opts = ', '.join(
                        [f"{k}={v}" for k, v in finfo['selection'][:5]]
                    )
                    line += f" [{opts}]"
                lines.append(line)
        return '\n'.join(lines)

    # ------------------------------------------------------------------
    # METHOD: detect_intent
    # ------------------------------------------------------------------
    def detect_intent(self, user_prompt, schema_dict):
        """
        Ask ChatGPT to parse the user message into a structured query plan.
        Returns a parsed dict with intent_type, target_model, domain, fields, etc.
        """
        schema_text = self._schema_to_text(schema_dict)

        system_message = f"""{self.agent.system_prompt}

AVAILABLE ODOO DATA MODELS AND FIELDS:
{schema_text}

STRICT RULES:
- You are a read-only data assistant. You can ONLY retrieve and display data.
- You CANNOT create, update, or delete any record under any circumstance.
- If the user asks to modify data, set intent_type to "refuse" and explain why.

OUTPUT FORMAT:
Respond ONLY with a valid JSON object. No markdown fences. No explanation outside the JSON.

JSON schema you must return:
{{
  "intent_type":     "read" | "report" | "refuse",
  "target_model":    "<odoo technical model name, e.g. sale.order>",
  "domain":          [["field", "operator", "value"], ...],
  "fields":          ["field1", "field2", ...],
  "order":           "field_name desc",
  "limit":           100,
  "response_format": "table" | "list" | "text" | "card",
  "refuse_reason":   "<only present when intent_type is refuse>"
}}

For date values in domain, use these string tokens (they will be resolved server-side):
  today, yesterday, this_week_start, this_week_end,
  last_month_start, last_month_end, this_year_start, this_year_end
"""

        messages = [
            {'role': 'system', 'content': system_message},
            {'role': 'user', 'content': user_prompt},
        ]
        payload = self._prepare_payload(
            messages,
            temperature=self.agent.temperature,
            max_tokens=500
        )

        resp = requests.post(
            self._get_url(),
            headers=self._build_headers(),
            json=payload,
            timeout=self.connector.request_timeout,
        )
        resp.raise_for_status()
        raw_text = self._parse_response(resp).strip()

        # Strip markdown fences if ChatGPT ignores instructions
        if raw_text.startswith('```'):
            raw_text = raw_text.split('```')[1]
            if raw_text.startswith('json'):
                raw_text = raw_text[4:]

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f'ChatGPT returned invalid JSON: {e}\nRaw: {raw_text}'
            )

    # ------------------------------------------------------------------
    # METHOD: generate_narration
    # ------------------------------------------------------------------
    def generate_narration(self, user_prompt, raw_data, intent_dict, agent):
        """
        Ask ChatGPT to write a natural language summary of the query results.
        Returns narration as a plain string.
        """
        system_message = f"""{agent.system_prompt}

Narrate the following Odoo data results concisely and professionally.
Highlight key figures such as totals, counts, and overdue items.
Be accurate. Do not invent data that is not in the results."""

        user_message = f"""User asked: {user_prompt}

Query returned {len(raw_data)} records:
{str(raw_data[:20])}"""  # limit to first 20 records for token safety

        messages = [
            {'role': 'system', 'content': system_message},
            {'role': 'user', 'content': user_message},
        ]
        payload = self._prepare_payload(
            messages,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens
        )

        resp = requests.post(
            self._get_url(),
            headers=self._build_headers(),
            json=payload,
            timeout=self.connector.request_timeout,
        )
        resp.raise_for_status()
        return self._parse_response(resp).strip()
