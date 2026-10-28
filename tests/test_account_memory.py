import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.agent import memory
from langgraph.store.memory import InMemoryStore


def test_memory_limits_and_replacement():
    store = InMemoryStore()
    memory.replace(store, 'one', 'Тихий отель')
    for i in range(40):
        memory.remember_fact(store, 'one', str(i) + 'x' * 1000)
    profile = memory.load(store, 'one')
    assert len(profile['facts']) == 12
    assert all(len(f) <= 300 for f in profile['facts'])
    assert len(memory.describe(profile)) <= memory.MAX_TEXT
    assert memory.load(store, 'two') == {}
    memory.replace(store, 'one', 'Новое предпочтение')
    assert memory.describe(memory.load(store, 'one')) == 'Новое предпочтение'
    memory.replace(store, 'one', '')
    assert memory.load(store, 'one') == {}


def test_memory_api_auth_limits_and_isolation():
    with tempfile.TemporaryDirectory() as folder:
        app = create_app(f"sqlite:///{Path(folder).as_posix()}/test.db", secret_key='test-key-' * 8)
        headers = {'Origin': 'http://testserver'}
        with TestClient(app) as client:
            assert client.put('/api/memory', headers=headers, json={'text': 'x'}).status_code == 401
            client.post('/api/auth/guest', headers=headers)
            assert client.put('/api/memory', json={'text': 'x'}).status_code == 403
            assert client.put('/api/memory', headers=headers, json={'text': 'x' * (memory.MAX_TEXT + 1)}).status_code == 422
            boundary = 'я' * memory.MAX_TEXT
            result = client.put('/api/memory', headers=headers, json={'text': boundary})
            assert result.status_code == 200
            assert result.json()['max_chars'] == memory.MAX_TEXT
            assert result.json()['text'] == boundary
            value = '<script>alert(1)</script> Тихий отель'
            assert client.put('/api/memory', headers=headers, json={'text': value}).json()['text'] == value
            assert client.get('/api/memory').json()['text'] == value
            with TestClient(app) as other:
                other.post('/api/auth/guest', headers=headers)
                assert other.get('/api/memory').json()['text'] == ''
            assert client.put('/api/memory', headers=headers, json={'text': ''}).json()['text'] == ''
        app.state.db.kw['bind'].dispose()
