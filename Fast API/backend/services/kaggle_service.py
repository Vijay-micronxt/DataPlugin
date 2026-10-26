"""OpenAI-compatible chat completions client for the configured Kaggle proxy."""
from __future__ import annotations

import json

import httpx

from services.gemini_service import GeminiServiceError, GeminiTurn


class KaggleService:
    def __init__(self, base_url: str, api_key: str = ''):
        if not base_url.strip():
            raise GeminiServiceError('Kaggle proxy URL is not configured. Set OLLAMA_BASE_URL in backend/.env.')
        root = base_url.strip().rstrip('/')
        self.base_url = root if root.endswith('/v1') else f'{root}/v1'
        self.api_key = api_key.strip()

    def _headers(self) -> dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    @staticmethod
    def _friendly_error(error: Exception) -> GeminiServiceError:
        if isinstance(error, httpx.TimeoutException):
            return GeminiServiceError('Kaggle proxy request timed out. Try again in a moment.')
        if isinstance(error, httpx.HTTPStatusError):
            detail = error.response.text[:250]
            return GeminiServiceError(f'Kaggle proxy returned HTTP {error.response.status_code}: {detail}')
        return GeminiServiceError(f'Kaggle proxy request failed: {str(error)[:250] or type(error).__name__}')

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f'{self.base_url}/models', headers=self._headers(), timeout=8)
            response.raise_for_status()
            payload = response.json()
            items = payload.get('data', payload.get('models', [])) if isinstance(payload, dict) else []
            names = []
            for item in items:
                if isinstance(item, str):
                    name = item
                elif isinstance(item, dict):
                    name = item.get('id') or item.get('name') or item.get('model') or ''
                else:
                    name = ''
                if name and name not in names:
                    names.append(name)
            return names
        except Exception as error:
            raise self._friendly_error(error) from error

    def _complete(self, model: str, messages: list[dict[str, str]], max_tokens: int = 1600) -> str:
        try:
            response = httpx.post(
                f'{self.base_url}/chat/completions',
                headers=self._headers(),
                json={'model': model, 'messages': messages, 'temperature': 0.2, 'max_tokens': max_tokens},
                timeout=90,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload['choices'][0]['message']['content']
            if isinstance(content, list):
                content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
            if not isinstance(content, str) or not content.strip():
                raise GeminiServiceError('Kaggle model returned no response text. Try another model.')
            return content.strip()
        except GeminiServiceError:
            raise
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
        result = self._complete(model, messages)
        try:
            return GeminiTurn.model_validate_json(result)
        except Exception:
            start, end = result.find('{'), result.rfind('}')
            if start >= 0 and end > start:
                try:
                    return GeminiTurn.model_validate(json.loads(result[start:end + 1]))
                except Exception:
                    pass
            raise GeminiServiceError('Kaggle model did not return the required structured response. Try another model.')

    def generate_text(self, model: str, system_instruction: str, prompt: str) -> str:
        return self._complete(model, [
            {'role': 'system', 'content': system_instruction},
            {'role': 'user', 'content': prompt},
        ], max_tokens=1200)
