"""Run with: .venv/bin/python -m unittest discover -s tests -v"""
import os
import tempfile
import unittest
from datetime import date

_TEMP = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = _TEMP.name + '/agent'
os.environ['PULSE_DB_PATH'] = _TEMP.name + '/app.sqlite3'
os.environ['PULSE_AUTH_KEY'] = 'test-secret-' * 5
os.environ['LLM_API_KEY'] = 'test-key-no-network'

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from backend.main import create_app
from backend.agent import service
from backend.agent.adapters import hotels
from backend.agent.trip import hotel_item


class PurchaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()

    def client(self):
        c = TestClient(self.app, headers={'Origin': 'http://testserver'})
        self.assertEqual(c.post('/api/auth/guest').status_code, 200)
        return c

    def seed(self, c):
        chat = c.post('/api/chats').json()['chat']['id']
        hotel = hotels.search(date(2027, 6, 10), date(2027, 6, 15))[0]
        service._agent().update_state(service._config(chat), {'trip': [
            {'op': 'requirements', 'changes': {'check_in': '2027-06-10', 'check_out': '2027-06-15', 'adults': 2}},
            {'op': 'cart_add', 'item': hotel_item(hotel).model_dump(mode='json')},
        ]}, as_node='tools')
        cart = c.get('/api/purchases').json()['carts'][0]
        return chat, cart

    def test_deleted_chat_keeps_purchases_and_ownership(self):
        c, other = self.client(), self.client()
        chat, cart = self.seed(c)
        self.assertEqual(other.delete('/api/chats/' + chat).status_code, 404)
        self.assertEqual(c.delete('/api/chats/' + chat).status_code, 200)
        self.assertEqual(c.get('/api/chats').json()['chats'], [])
        self.assertEqual(c.get('/api/chats/' + chat).status_code, 404)
        self.assertTrue(c.get('/api/purchases').json()['carts'][0]['archived'])
        url = '/api/purchases/' + chat + '/pay'
        self.assertEqual(other.post(url, json={'version': cart['version']}).status_code, 404)
        paid = c.post(url, json={'version': cart['version']})
        self.assertEqual(paid.status_code, 200, paid.text)
        order = paid.json()['order']['id']
        self.assertEqual(c.post(url, json={'version': cart['version']}).status_code, 409)
        self.assertEqual(len(c.get('/api/purchases').json()['orders']), 1)
        self.assertEqual(c.get('/api/purchases').json()['carts'], [])
        self.assertEqual(other.get('/api/orders/' + order).status_code, 404)
        self.assertEqual(other.delete('/api/orders/' + order).status_code, 404)
        self.assertEqual(c.delete('/api/orders/' + order).status_code, 200)
        self.assertEqual(c.get('/api/purchases').json()['orders'], [])

    def test_remove_cart_and_stale_version(self):
        c = self.client()
        chat, cart = self.seed(c)
        url = '/api/purchases/' + chat
        self.assertEqual(c.request('DELETE', url, json={'version': cart['version'] - 1}).status_code, 409)
        self.assertEqual(c.request('DELETE', url, json={'version': cart['version']}, headers={'Origin':'https://foreign.test'}).status_code, 403)
        self.assertEqual(c.request('DELETE', url, json={'version': cart['version']}).status_code, 200)
        self.assertEqual(c.get('/api/purchases').json()['carts'], [])
        self.assertFalse(service.get_state(chat).cart)

    def test_pending_checkout_is_resolved_by_cart_action(self):
        for pay in (False, True):
            with self.subTest(pay=pay):
                c = self.client()
                chat, cart = self.seed(c)
                graph = service._agent()
                config = service._config(chat)
                graph.update_state(config, {'messages': [AIMessage(content='', tool_calls=[
                    {'name': 'checkout', 'args': {}, 'id': 'checkout-test', 'type': 'tool_call'}
                ])]}, as_node='model')
                graph.invoke(None, config)
                self.assertIsNotNone(service.pending_checkout(chat))
                result = service.change_account_cart(chat, cart['version'], pay, c.get('/api/auth/me').json()['user_id'])
                self.assertEqual(bool(result['order']), pay)
                self.assertIsNone(service.pending_checkout(chat))
                self.assertFalse(service.get_state(chat).cart)

    def test_blocked_trip_cannot_be_paid(self):
        c = self.client()
        chat, _ = self.seed(c)
        service._agent().update_state(service._config(chat), {'trip': [
            {'op': 'requirements', 'changes': {'check_in': '2027-07-10', 'check_out': '2027-07-15'}}
        ]}, as_node='tools')
        cart = c.get('/api/purchases').json()['carts'][0]
        response = c.post('/api/purchases/' + chat + '/pay', json={'version': cart['version']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(c.get('/api/purchases').json()['orders'], [])
        self.assertTrue(service.get_state(chat).cart)
        self.assertEqual(TestClient(self.app).get('/api/purchases').status_code, 401)


if __name__ == '__main__':
    unittest.main()
