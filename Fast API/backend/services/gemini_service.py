"""Google Gemini integration using the official google-genai SDK."""
from __future__ import annotations

from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError


class GeminiConfigurationError(RuntimeError):
    pass


class GeminiServiceError(RuntimeError):
    pass


class GeminiTurn(BaseModel):
    intent: Literal['conversation', 'database_query', 'clarification']
    reply: str = Field(description='A concise user-facing response or clarification question.')
    sql: str | None = Field(default=None, description='One MySQL SELECT query, only for database_query intent.')


class GeminiService:
    def __init__(self, api_key: str | None, default_model: str):
        if not api_key or not api_key.strip() or api_key.strip() == 'your_gemini_api_key':
            raise GeminiConfigurationError('Gemini is not configured. Set GEMINI_API_KEY in backend/.env and restart FastAPI.')
        self.default_model = default_model.strip()
        if not self.default_model:
            raise GeminiConfigurationError('GEMINI_MODEL is empty. Set it in backend/.env and restart FastAPI.')
        self.client = genai.Client(api_key=api_key.strip(), http_options=types.HttpOptions(timeout=60_000))

    @staticmethod
    def _friendly_error(error: Exception) -> GeminiServiceError:
        message = str(error)
        folded = message.lower()
        if '10013' in message or 'forbidden by its access permissions' in folded:
            return GeminiServiceError('Windows blocked the Gemini connection. Allow outbound HTTPS (TCP 443) for the Python process to generativelanguage.googleapis.com, then restart the chatbot backend.')
        if '429' in message or 'resource_exhausted' in folded or 'quota' in folded:
            return GeminiServiceError('Gemini quota or rate limit reached. Check Google AI Studio usage and try again later.')
        if '401' in message or '403' in message or 'api key' in folded or 'permission_denied' in folded:
            return GeminiServiceError('Gemini rejected the API key or access. Check GEMINI_API_KEY and model availability.')
        if '404' in message or 'not_found' in folded:
            return GeminiServiceError('Gemini model was not found. Check GEMINI_MODEL or choose a model shown by /api/health.')
        if 'timeout' in folded or 'timed out' in folded:
            return GeminiServiceError('Gemini request timed out. Try again in a moment.')
        return GeminiServiceError(f'Gemini request failed: {message[:350] or type(error).__name__}')

    def list_models(self) -> list[str]:
        try:
            found: list[str] = []
            for model in self.client.models.list():
                name = (getattr(model, 'name', '') or '').removeprefix('models/')
                actions = getattr(model, 'supported_actions', None) or []
                if name.startswith('gemini-') and (not actions or 'generateContent' in actions):
                    found.append(name)
            return list(dict.fromkeys(found))
        except Exception as error:
            raise self._friendly_error(error) from error

    @staticmethod
    def _contents(history: list[dict[str, str]]) -> list[types.Content]:
        contents = []
        for item in history[-24:]:
            role = 'model' if item.get('role') == 'assistant' else 'user'
            value = item.get('content', '').strip()
            if value:
                contents.append(types.Content(role=role, parts=[types.Part(text=value)]))
        return contents

    def generate_turn(self, model: str, system_instruction: str, history: list[dict[str, str]]) -> GeminiTurn:
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=self._contents(history),
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type='application/json',
                    response_schema=GeminiTurn,
                    temperature=0.2,
                    max_output_tokens=2048,
                ),
            )
            parsed = getattr(response, 'parsed', None)
            if isinstance(parsed, GeminiTurn):
                return parsed
            if parsed is not None:
                return GeminiTurn.model_validate(parsed)
            text = getattr(response, 'text', None)
            if not text:
                raise GeminiServiceError('Gemini returned no response text. Try again or choose another Gemini model.')
            return GeminiTurn.model_validate_json(text)
        except GeminiServiceError:
            raise
        except ValidationError as error:
            raise GeminiServiceError('Gemini returned a response that did not match the required format. Please retry.') from error
        except Exception as error:
            raise self._friendly_error(error) from error

    def generate_text(self, model: str, system_instruction: str, prompt: str) -> str:
        try:
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                    max_output_tokens=1200,
                ),
            )
            text = getattr(response, 'text', None)
            if not text or not text.strip():
                raise GeminiServiceError('Gemini returned no explanation text. Please retry.')
            return text.strip()
        except GeminiServiceError:
            raise
        except Exception as error:
            raise self._friendly_error(error) from error

    def check_generation(self, model: str) -> str:
        return self.generate_text(model, 'Reply with a short health-check response.', 'Reply exactly: OK')
