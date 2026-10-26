"""OpenRouter chat completions client and model catalog."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

import httpx

from services.gemini_service import GeminiConfigurationError, GeminiServiceError, GeminiTurn


class OpenRouterService:
    def __init__(self, api_key: str, base_url: str = 'https://openrouter.ai/api/v1'):
        if not api_key or not api_key.strip():
            raise GeminiConfigurationError('OpenRouter is not configured. Set OPENROUTER_API_KEY in backend/.env and restart FastAPI.')
        self.api_key = api_key.strip()
        self.base_url = base_url.strip().rstrip('/')

    def _headers(self) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'HTTP-Referer': 'http://localhost:5173',
            'X-Title': 'StoreNxt Assistant',
        }

    @staticmethod
    def _friendly_error(error: Exception) -> GeminiServiceError:
        if isinstance(error, httpx.TimeoutException):
            return GeminiServiceError('OpenRouter request timed out. Try again in a moment.')
        if isinstance(error, httpx.HTTPStatusError):
            detail = error.response.text[:300]
            if error.response.status_code in (401, 403):
                return GeminiServiceError('OpenRouter rejected the API key or access. Check OPENROUTER_API_KEY.')
            if error.response.status_code == 429:
                return GeminiServiceError('OpenRouter rate limit or account balance limit reached. Check your OpenRouter account.')
            return GeminiServiceError(f'OpenRouter returned HTTP {error.response.status_code}: {detail}')
        return GeminiServiceError(f'OpenRouter request failed: {str(error)[:250] or type(error).__name__}')

    @staticmethod
    def _is_free(item: dict) -> bool:
        model_id = str(item.get('id', ''))
        if model_id.endswith(':free') or model_id == 'openrouter/free':
            return True
        pricing = item.get('pricing') or {}
        try:
            return Decimal(str(pricing['prompt'])) == 0 and Decimal(str(pricing['completion'])) == 0
        except (KeyError, InvalidOperation, TypeError, ValueError):
            return False

    def list_model_catalog(self) -> dict[str, list[str]]:
        try:
            response = httpx.get(f'{self.base_url}/models', headers=self._headers(), timeout=20)
            response.raise_for_status()
            payload = response.json()
            free, paid = [], []
            for item in payload.get('data', []):
                if not isinstance(item, dict):
                    continue
                model_id = item.get('id')
                output_modalities = item.get('architecture', {}).get('output_modalities', [])
                if model_id and (not output_modalities or 'text' in output_modalities):
                    (free if self._is_free(item) else paid).append(model_id)
            return {
                'free': sorted(set(free), key=str.casefold),
                'paid': sorted(set(paid), key=str.casefold),
            }
        except Exception as error:
            raise self._friendly_error(error) from error

    def list_models(self) -> list[str]:
        catalog = self.list_model_catalog()
        return catalog['free'] + catalog['paid']

    def _complete(self, model: str, messages: list[dict[str, str]], max_tokens: int = 3000, json_mode: bool = False) -> str:
        try:
            payload = {'model': model, 'messages': messages, 'temperature': 0.2, 'max_tokens': max_tokens}
            if json_mode:
                payload['response_format'] = {'type': 'json_object'}
            response = httpx.post(
                f'{self.base_url}/chat/completions',
                headers=self._headers(),
                json=payload,
                timeout=90,
            )
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content']
            if isinstance(content, list):
                content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
            if not isinstance(content, str) or not content.strip():
                raise GeminiServiceError('OpenRouter model returned no response text. Try another model.')
            return content.strip()
        except GeminiServiceError:
            raise
        except httpx.HTTPStatusError as error:
            # Some model/provider routes do not implement constrained JSON mode.
            # Retry once with the same prompt and let the parser/repair step handle it.
            body = error.response.text.lower()
            if json_mode and error.response.status_code == 400 and 'response_format' in body:
                return self._complete(model, messages, max_tokens=max_tokens, json_mode=False)
            raise self._friendly_error(error) from error
        except Exception as error:
            raise self._friendly_error(error) from error

    def generate_turn(self, model: str, system_instruction: str, history: list[dict[str, str]]) -> GeminiTurn:
        format_instruction = (
            '\nReturn only one valid JSON object with exactly these fields: '
            '{"intent":"conversation|database_query|clarification","reply":"user-facing text","sql":null or "one SELECT query"}. '
            'Do not wrap the JSON in Markdown fences.'
        )
        messages = [{'role': 'system', 'content': system_instruction + format_instruction}]
        messages.extend({'role': item.get('role', 'user'), 'content': item.get('content', '')} for item in history[-24:])
        def parse_turn(value: str) -> GeminiTurn:
            try:
                return GeminiTurn.model_validate_json(value)
            except Exception:
                start, end = value.find('{'), value.rfind('}')
                if start >= 0 and end > start:
                    try:
                        return GeminiTurn.model_validate(json.loads(value[start:end + 1]))
                    except Exception:
                        pass
                raise ValueError('Model response was not a valid assistant turn.')

        result = self._complete(model, messages, json_mode=True)
        try:
            return parse_turn(result)
        except ValueError:
            repair_messages = messages + [
                {'role': 'assistant', 'content': result},
                {'role': 'user', 'content': 'Rewrite your previous response as exactly one JSON object with fields intent, reply, and sql. Use intent conversation, database_query, or clarification; use null for sql unless you provide one MySQL SELECT query. Return JSON only.'},
            ]
            repaired = self._complete(model, repair_messages, max_tokens=1800, json_mode=True)
            try:
                return parse_turn(repaired)
            except ValueError as error:
                raise GeminiServiceError('OpenRouter model could not format its reply for the assistant. Retry or select another model.') from error

    def generate_text(self, model: str, system_instruction: str, prompt: str) -> str:
        return self._complete(model, [
            {'role': 'system', 'content': system_instruction},
            {'role': 'user', 'content': prompt},
        ], max_tokens=1200)
