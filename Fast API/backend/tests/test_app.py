import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as api
from fastapi.testclient import TestClient
from services.gemini_service import GeminiTurn


OBJECTS = [
    {'name': 'top_selling_skus_count', 'columns': [
        {'name': 'sku_id'}, {'name': 'sku_name'}, {'name': 'total_quantity'}, {'name': 'rank'}
    ]}
]


class FakeGemini:
    calls = []

    def __init__(self, api_key, default_model):
        if not api_key:
            raise api.GeminiConfigurationError('missing key')

    def list_models(self):
        return ['gemini-3.5-flash-lite', 'gemini-3.8-flash']

    def check_generation(self, model):
        return 'OK'

    def generate_turn(self, model, system_instruction, history):
        self.calls.append((model, history))
        return GeminiTurn(intent='conversation', reply=f"Reply from {model}")

    def generate_text(self, model, system_instruction, prompt):
        return 'Result explanation.'


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_store = api.STORE_PATH
        self.old_key = api.GEMINI_API_KEY
        api.STORE_PATH = Path(self.temp.name) / 'conversations.sqlite3'
        api.GEMINI_API_KEY = 'test-key'
        FakeGemini.calls = []
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        api.STORE_PATH = self.old_store
        api.GEMINI_API_KEY = self.old_key
        self.temp.cleanup()

    def create_conversation(self):
        response = self.client.post('/api/conversations', json={})
        self.assertEqual(response.status_code, 200)
        return response.json()['id']

    def test_sql_parser_allows_capped_select_and_rejects_unsafe_sql(self):
        safe = api.valid_sql('SELECT sku_name FROM top_selling_skus_count LIMIT 5000', OBJECTS)
        self.assertRegex(safe, r'(?i)LIMIT\s+200')
        self.assertIsNone(api.valid_sql('DROP TABLE top_selling_skus_count', OBJECTS))
        self.assertIsNone(api.valid_sql('SELECT sku_name FROM top_selling_skus_count; DELETE FROM top_selling_skus_count', OBJECTS))
        self.assertIsNone(api.valid_sql('SELECT * FROM read_csv_auto(\'secret.csv\')', OBJECTS))

    def test_duckdb_binding_rejects_unknown_columns_and_database_is_read_only(self):
        objects = api.schema()
        candidate = api.valid_sql('SELECT not_a_real_column FROM top_selling_skus_count', objects)
        with self.assertRaises(Exception):
            api.execute_readonly(candidate)
        connection = api.db()
        try:
            with self.assertRaises(Exception):
                connection.execute('CREATE TABLE codex_test_must_not_exist(value INTEGER)')
        finally:
            connection.close()

    def test_chat_keeps_history_isolated_and_sends_followup_context(self):
        first, second = self.create_conversation(), self.create_conversation()
        with patch.object(api, 'GeminiService', FakeGemini):
            one = self.client.post('/api/chat', json={'conversation_id': first, 'message': 'What can you help me analyze?', 'model': 'gemini-3.5-flash-lite'})
            self.assertEqual(one.status_code, 200, one.text)
            two = self.client.post('/api/chat', json={'conversation_id': first, 'message': 'Tell me more.', 'model': 'gemini-3.5-flash-lite'})
            self.assertEqual(two.status_code, 200, two.text)
            other = self.client.post('/api/chat', json={'conversation_id': second, 'message': 'What can you help me analyze?', 'model': 'gemini-3.5-flash-lite'})
            self.assertEqual(other.status_code, 200, other.text)
        followup_history = FakeGemini.calls[1][1]
        other_history = FakeGemini.calls[2][1]
        self.assertEqual(followup_history[-2]['role'], 'assistant')
        self.assertEqual(followup_history[-2]['content'], 'Reply from gemini-3.5-flash-lite')
        self.assertEqual(len(other_history), 1)

    def test_chat_requires_gemini_api_key_for_model_requests(self):
        conversation = self.create_conversation()
        api.GEMINI_API_KEY = ''
        response = self.client.post('/api/chat', json={'conversation_id': conversation, 'message': 'What can you help me analyze?'})
        self.assertEqual(response.status_code, 503)
        self.assertIn('GEMINI_API_KEY', response.text)

    def test_health_reports_database_without_leaking_key(self):
        with patch.object(api, 'GeminiService', FakeGemini):
            response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['database'])
        self.assertNotIn('test-key', response.text)


if __name__ == '__main__':
    unittest.main()
