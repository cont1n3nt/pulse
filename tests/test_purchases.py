"""Run with: .venv/bin/python -m unittest discover -s tests -v"""
import os
import tempfile
import unittest
from datetime import date, timedelta

_TEMP = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ['DATA_DIR'] = _TEMP.name + '/agent'
os.environ['PULSE_DB_PATH'] = _TEMP.name + '/app.sqlite3'
os.environ['PULSE_AUTH_KEY'] = 'test-secret-' * 5
os.environ['LLM_API_KEY'] = 'test-key-no-network'
os.environ['LANGSMITH_TRACING'] = 'false'

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from backend.main import create_app
from backend.agent import service
from backend.agent import aftersale, orders
from backend.agent.adapters import hotels
from backend.agent.adapters import trains
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

    def prepare_change(self, c, mode):
        chat, cart = self.seed(c)
        response = c.post('/api/purchases/' + chat + '/pay', json={'version': cart['version']})
        self.assertEqual(response.status_code, 200, response.text)
        original = orders.get(response.json()['order']['id'])
        if mode == 'replace':
            start = date(2027, 6, 10)
            items = None
            # Демо-наличие детерминировано: ищем доступный сдвиг того же отеля.
            for shift in range(1, 15):
                try:
                    items, _ = aftersale.moved(original, start + timedelta(days=shift),
                                               start + timedelta(days=shift + 5), 2, None,
                                               lambda *args: None)
                    break
                except aftersale.CannotChange:
                    continue
            self.assertIsNotNone(items)
            changes = {'check_in': items[0].snapshot['check_in'],
                       'check_out': items[0].snapshot['check_out']}
        else:
            items = [aftersale.transfer_for(original, 2)]
            changes = {}
        service._agent().update_state(service._config(chat), {'trip': [
            {'op': 'requirements', 'changes': changes},
            {'op': 'cart_set', 'items': [item.model_dump(mode='json') for item in items],
             'order_change': {'order_id': original.id, 'mode': mode}},
        ]}, as_node='tools')
        return chat, original, c.get('/api/purchases').json()['carts'][0]

    def test_profile_payment_replaces_order_and_resolves_pending_checkout(self):
        c = self.client()
        chat, original, cart = self.prepare_change(c, 'replace')
        self.assertEqual(cart['replaces'], original.id)
        graph, config = service._agent(), service._config(chat)
        graph.update_state(config, {'messages': [AIMessage(content='', tool_calls=[
            {'name': 'checkout', 'args': {}, 'id': 'replace-checkout', 'type': 'tool_call'}
        ])]}, as_node='model')
        graph.invoke(None, config)
        self.assertEqual(service.pending_checkout(chat)['replaces'], original.id)
        # Архивирование не должно терять семантику переноса.
        self.assertEqual(c.delete('/api/chats/' + chat).status_code, 200)
        paid = c.post('/api/purchases/' + chat + '/pay', json={'version': cart['version']})
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertEqual(orders.get(original.id).status, 'cancelled')
        state = service.get_state(chat)
        self.assertEqual(state.cancelled_orders, [original.id])
        self.assertIsNone(state.order_change)
        self.assertIsNone(service.pending_checkout(chat))
        history = c.get('/api/purchases').json()['orders']
        self.assertEqual({o['status'] for o in history}, {'confirmed', 'cancelled'})
        self.assertEqual(c.post('/api/purchases/' + chat + '/pay',
                               json={'version': cart['version']}).status_code, 409)

    def test_profile_payment_can_extend_order_with_transfer_only(self):
        c = self.client()
        chat, original, cart = self.prepare_change(c, 'extend')
        self.assertEqual(cart['extends'], original.id)
        paid = c.post('/api/purchases/' + chat + '/pay', json={'version': cart['version']})
        self.assertEqual(paid.status_code, 200, paid.text)
        self.assertEqual([i['kind'] for i in paid.json()['order']['items']], ['transfer'])
        self.assertEqual(orders.get(original.id).status, 'confirmed')
        self.assertEqual(service.get_state(chat).cancelled_orders, [])

    def test_removing_replacement_cart_keeps_original_order(self):
        c = self.client()
        chat, original, cart = self.prepare_change(c, 'replace')
        result = c.request('DELETE', '/api/purchases/' + chat, json={'version': cart['version']})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(orders.get(original.id).status, 'confirmed')
        self.assertIsNone(service.get_state(chat).order_change)

    def test_train_cart_can_be_paid_from_profile(self):
        c = self.client()
        chat, _ = self.seed(c)
        from backend.agent.trip import flight_item
        outbound, back = trains.legs('MOW', date(2027, 6, 10), date(2027, 6, 15))
        service._agent().update_state(service._config(chat), {'trip': [
            {'op': 'cart_add', 'item': flight_item(outbound, 'outbound').model_dump(mode='json')},
            {'op': 'cart_add', 'item': flight_item(back, 'return').model_dump(mode='json')},
        ]}, as_node='tools')
        cart = c.get('/api/purchases').json()['carts'][0]
        self.assertTrue(any(step['kind'] == 'train' for step in cart['itinerary']))
        paid = c.post('/api/purchases/' + chat + '/pay', json={'version': cart['version']})
        self.assertEqual(paid.status_code, 200, paid.text)
        legs = [i for i in paid.json()['order']['items'] if i['kind'] == 'flight']
        self.assertEqual([i['snapshot']['mode'] for i in legs], ['train', 'train'])

    def test_location_health_and_asset_versions_are_preserved(self):
        c = self.client()
        self.assertEqual(c.get('/health').json(), {'status': 'ok'})
        self.assertTrue(c.get('/api/location').json()['trains'])
        index = c.get('/')
        self.assertEqual(index.headers['cache-control'], 'no-store')
        self.assertIn('/static/app.js?v=', index.text)
        self.assertIn('Pulse', index.text)
        self.assertEqual(c.get('/static/app.js').headers['cache-control'], 'no-cache')


if __name__ == '__main__':
    unittest.main()
