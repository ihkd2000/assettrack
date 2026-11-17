import tempfile
import threading
import unittest
from pathlib import Path
from assettrack.store import Store, Problem

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'test.sqlite3')
        self.store.bootstrap('safe-password-123')
        self.admin=self.store.authenticate('admin','safe-password-123')[1]
        self.employee=self.store.create_user(self.admin,{'username':'worker','full_name':'Alex Worker','role':'employee','password':'worker-password-123'})
        self.manager=self.store.create_user(self.admin,{'username':'manager','full_name':'Sam Manager','role':'manager','password':'manager-password-123'})

    def asset(self):
        return self.store.create_asset(self.admin,{'code':'LAP-001','name':'Laptop','category':'Computer','condition':'good'})['id']

    def test_bootstrap_only_once(self):
        with self.assertRaises(Problem): self.store.bootstrap('another-secret-123')

    def test_authentication_and_session(self):
        with self.assertRaises(Problem): self.store.authenticate('admin','wrong')
        token,account=self.store.authenticate('worker','worker-password-123')
        self.assertEqual('employee',self.store.actor(token)['role'])
        self.store.logout(token)
        with self.assertRaises(Problem): self.store.actor(token)

    def test_unique_code_case_insensitive(self):
        self.asset()
        with self.assertRaises(Problem) as cm:
            self.store.create_asset(self.admin,{'code':'lap-001','name':'Other','category':'Computer'})
        self.assertEqual(cm.exception.status,409)

    def test_employee_cannot_modify_assets(self):
        with self.assertRaises(Problem) as cm:
            self.store.create_asset(self.employee,{'code':'X','name':'Bad','category':'Other'})
        self.assertEqual(cm.exception.status,403)
        with self.assertRaises(Problem): self.store.create_user(self.manager,{'username':'abc','full_name':'New','role':'admin','password':'example-test-pass-123'})

    def test_assign_return_reassign(self):
        aid=self.asset()
        self.store.assign(self.manager,aid,self.employee['id'])
        self.assertEqual(self.store.assets(self.admin)[0]['status'],'assigned')
        with self.assertRaises(Problem): self.store.assign(self.admin,aid,self.employee['id'])
        self.store.return_asset(self.manager,aid,'fair')
        self.assertEqual(self.store.assets(self.admin)[0]['condition'],'fair')
        self.store.assign(self.admin,aid,self.employee['id'])
        self.assertEqual(len(self.store.assignments(self.admin)),2)
        self.assertEqual(len(self.store.events(self.admin)),4)

    def test_employee_only_sees_assigned_assets(self):
        aid=self.asset()
        self.assertEqual(self.store.assets(self.employee),[])
        self.store.assign(self.admin,aid,self.employee['id'])
        self.assertEqual(len(self.store.assets(self.employee)),1)
        self.store.return_asset(self.admin,aid,'good')
        self.assertEqual(self.store.assets(self.employee),[])

    def test_cannot_retire_assigned_asset(self):
        aid=self.asset()
        self.store.assign(self.admin,aid,self.employee['id'])
        with self.assertRaises(Problem):self.store.update_asset(self.admin,aid,{'retired':True})
        self.store.return_asset(self.admin,aid,'good')
        self.store.update_asset(self.admin,aid,{'retired':True})
        with self.assertRaises(Problem):self.store.assign(self.admin,aid,self.employee['id'])

    def test_bad_condition(self):
        aid=self.asset()
        with self.assertRaises(Problem):self.store.return_asset(self.admin,aid,'broken')

    def test_concurrent_assignment_only_one_wins(self):
        aid=self.asset()
        successes=[]
        def attempt():
            try:
                self.store.assign(self.admin,aid,self.employee['id'])
                successes.append(True)
            except Problem as ex:
                self.assertEqual(ex.status,409)
        threads=[threading.Thread(target=attempt) for _ in range(6)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(successes),1)
        self.assertEqual(self.store.dashboard(self.admin)['assigned'],1)

    def test_data_survives_reopening(self):
        aid=self.asset()
        reopened=Store(self.store.path)
        self.assertEqual(reopened.assets(self.admin)[0]['id'],aid)

if __name__=='__main__': unittest.main()
