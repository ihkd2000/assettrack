"""Local administrator recovery tests; no web password reset endpoint."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assettrack.__main__ import main

from assettrack.store import Problem, Store


class AdminRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'recovery.sqlite3')
        self.store.bootstrap('first-password-123')
        self.token, self.admin = self.store.authenticate('admin', 'first-password-123')
        self.employee = self.store.create_user(self.admin, {
            'username': 'worker', 'full_name': 'Worker', 'role': 'employee',
            'password': 'employee-password-123',
        })

    def test_reset_changes_password_revokes_sessions_and_keeps_data(self):
        self.store.reset_admin_password('admin', 'replacement-password-456')
        with self.assertRaises(Problem):
            self.store.authenticate('admin', 'first-password-123')
        with self.assertRaises(Problem):
            self.store.actor(self.token)
        _, admin = self.store.authenticate('admin', 'replacement-password-456')
        self.assertEqual(admin['role'], 'admin')
        self.assertEqual(len(self.store.users(admin)), 2)

    def test_cannot_reset_non_admin_or_unknown_user(self):
        for username in ['worker', 'missing']:
            with self.subTest(username=username), self.assertRaises(Problem):
                self.store.reset_admin_password(username, 'replacement-password-456')

    def test_invalid_password_cannot_change_credentials(self):
        with self.assertRaises(Problem):
            self.store.reset_admin_password('admin', 'short')
        self.assertIsNotNone(self.store.actor(self.token))
        self.store.authenticate('admin', 'first-password-123')

    def test_cli_reset_password_uses_selected_database(self):
        with patch('sys.argv', [
            'assettrack', '--db', self.store.path,
            '--reset-admin-password', 'admin',
        ]), patch('getpass.getpass', side_effect=[
            'replacement-password-456', 'replacement-password-456',
        ]):
            main()
        self.store.authenticate('admin', 'replacement-password-456')

    def test_reset_does_not_reactivate_disabled_admin(self):
        second = self.store.create_user(self.admin, {
            'username': 'second', 'full_name': 'Second Admin', 'role': 'admin',
            'password': 'second-password-123',
        })
        self.store.set_user_active(self.admin, second['id'], False)
        self.store.reset_admin_password('second', 'replacement-password-456')
        with self.assertRaises(Problem):
            self.store.authenticate('second', 'replacement-password-456')


if __name__ == '__main__':
    unittest.main()
