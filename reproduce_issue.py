from app import app
from flask import session
import unittest

class StaplesTestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test'
        self.client = app.test_client()

    def test_staples_save(self):
        with self.client.session_transaction() as sess:
            # Manually set session variables to simulate logged in user
            sess['username'] = 'JohnnyRose'
            sess['personal_profile_id'] = 4 # From debug script
            sess['user_key'] = 'test_key'

        # Test POST
        data = {
            'staple_name[]': ['Item 1', 'Item 2'],
            'staple_cost[]': ['10.00', '20.00'],
            'staple_hourly_rate': '15.00'
        }
        response = self.client.post('/staples', data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        
        # Check if saved in DB
        import sqlite3
        conn = sqlite3.connect("timecost.db")
        cursor = conn.cursor()
        cursor.execute("SELECT name, cost FROM staples WHERE owner_key = 'JohnnyRose'")
        rows = cursor.fetchall()
        print(f"Saved rows: {rows}")
        self.assertTrue(len(rows) >= 2)
        conn.close()

if __name__ == '__main__':
    unittest.main()
