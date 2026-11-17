import tempfile
import threading
import unittest
from pathlib import Path
from assettrack.store import Store, Problem

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'test.sqlite3')
        self.store.bootstrap('safe-password-123')
        self.admin=self.store.authenticate('admin','safe-password-123')[1]
        mk=lambda u,n,r:self.store.create_user(self.admin,{'username':u,'full_name':n,'role':r,'password':'long-password-123'})
        self.alex=mk('alex','Alex Worker','employee')
        self.bea=mk('bea','Bea Colleague','employee')
        self.carl=mk('carl','Carl Outsider','employee')
        self.manager=mk('manager','Sam Manager','manager')
        self.aid=self.store.create_asset(self.admin,{'code':'LAP-001','name':'Laptop','category':'Computer'})['id']
        self.store.assign(self.admin,self.aid,self.alex['id'])

    def request(self,to=None,actor=None,reason='Moving to Sales'):
        return self.store.request_transfer(actor or self.manager,self.aid,(to or self.bea)['id'],reason)['id']

    def holder(self):
        return self.store.assets(self.admin)[0]['employee_id']

    def assertProblem(self,status,fn,*args):
        with self.assertRaises(Problem) as cm: fn(*args)
        self.assertEqual(cm.exception.status,status,cm.exception.message)

    def test_accept_moves_assignment_and_records_history(self):
        tid=self.request()
        self.assertEqual(self.holder(),self.alex['id'])  # unchanged while pending
        self.assertEqual(self.store.assets(self.admin)[0]['pending_transfer_to'],'Bea Colleague')
        self.store.accept_transfer(self.bea,tid)
        self.assertEqual(self.holder(),self.bea['id'])
        self.assertEqual(self.store.assets(self.alex),[])
        self.assertEqual(len(self.store.assets(self.bea)),1)
        history=self.store.assignments(self.admin)
        self.assertEqual(len(history),2)
        new,old=history
        self.assertIsNone(new['returned_at']); self.assertEqual(new['transferred_from'],'Alex Worker')
        self.assertIsNotNone(old['returned_at']); self.assertEqual(old['transferred_to'],'Bea Colleague')
        kinds=[e['kind'] for e in self.store.events(self.admin)]
        self.assertEqual(kinds[:2],['transfer_accepted','transfer_requested'])
        self.assertEqual(self.store.transfers(self.admin)[0]['status'],'accepted')
        self.assertEqual(self.store.dashboard(self.admin)['assigned'],1)

    def test_reject_keeps_current_holder(self):
        tid=self.request()
        self.store.reject_transfer(self.bea,tid)
        self.assertEqual(self.holder(),self.alex['id'])
        self.assertEqual(len(self.store.assignments(self.admin)),1)
        self.assertEqual(self.store.events(self.admin)[0]['kind'],'transfer_rejected')
        self.request()  # a new transfer is allowed once the previous one is resolved

    def test_request_validation(self):
        other=self.store.create_asset(self.admin,{'code':'MON-1','name':'Monitor','category':'Display'})['id']
        self.assertProblem(409,self.store.request_transfer,self.manager,other,self.bea['id'],'x')  # unassigned
        self.assertProblem(409,self.request,self.alex)  # already the holder
        self.assertProblem(400,self.request,self.manager)  # not an employee
        self.assertProblem(400,self.request,None,None,'   ')  # reason required
        self.assertProblem(404,self.store.request_transfer,self.manager,999,self.bea['id'],'x')
        self.request()
        self.assertProblem(409,self.request,self.carl)  # already pending
        self.assertEqual(len(self.store.transfers(self.admin)),1)

    def test_only_admin_or_manager_can_request_or_cancel(self):
        self.assertProblem(403,self.request,None,self.alex)
        tid=self.request(actor=self.admin)
        self.assertProblem(403,self.store.cancel_transfer,self.bea,tid,'x')
        self.store.cancel_transfer(self.manager,tid,'Requested by mistake')
        self.assertEqual(self.store.transfers(self.admin)[0]['status'],'cancelled')
        self.assertEqual(self.holder(),self.alex['id'])

    def test_only_recipient_can_respond(self):
        tid=self.request()
        self.assertProblem(403,self.store.accept_transfer,self.alex,tid)    # current holder
        self.assertProblem(403,self.store.accept_transfer,self.manager,tid)
        self.assertProblem(403,self.store.reject_transfer,self.admin,tid)
        self.assertProblem(404,self.store.accept_transfer,self.carl,tid)    # unrelated employee
        self.assertProblem(404,self.store.accept_transfer,self.bea,tid+50)
        self.store.accept_transfer(self.bea,tid)
        self.assertProblem(409,self.store.accept_transfer,self.bea,tid)
        self.assertProblem(409,self.store.reject_transfer,self.bea,tid)
        self.assertProblem(409,self.store.cancel_transfer,self.admin,tid,'Too late')

    def test_visibility(self):
        self.request()
        self.assertEqual(len(self.store.transfers(self.admin)),1)
        self.assertEqual(len(self.store.transfers(self.manager)),1)
        self.assertEqual(len(self.store.transfers(self.alex)),1)
        self.assertEqual(len(self.store.transfers(self.bea)),1)
        self.assertEqual(self.store.transfers(self.carl),[])

    def snapshot(self):
        with self.store.db() as db:
            return [[tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY id')]
                    for t in ('assets','assignments','transfers','events')]

    def test_return_blocked_while_transfer_pending(self):
        tid=self.request()
        before=self.snapshot()
        for actor in (self.admin,self.manager):
            with self.assertRaises(Problem) as cm:
                self.store.return_asset(actor,self.aid,'damaged')
            self.assertEqual(cm.exception.status,409)
            self.assertIn('cancel the transfer',cm.exception.message)
        # Nothing changed: assignment, asset condition, transfer status, history.
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.holder(),self.alex['id'])
        self.assertEqual(self.store.transfers(self.admin)[0]['status'],'pending')
        # The pending transfer still works normally afterwards.
        self.store.accept_transfer(self.bea,tid)
        self.assertEqual(self.holder(),self.bea['id'])

    def test_return_allowed_after_cancelling_transfer(self):
        tid=self.request()
        self.store.cancel_transfer(self.manager,tid,'Asset needs repair')
        self.store.return_asset(self.manager,self.aid,'fair')
        self.assertEqual(self.store.dashboard(self.admin)['assigned'],0)
        self.assertEqual(self.store.assets(self.admin)[0]['condition'],'fair')
        self.assertEqual([e['kind'] for e in self.store.events(self.admin)][:2],['asset_returned','transfer_cancelled'])
        self.assertProblem(409,self.store.accept_transfer,self.bea,tid)
        self.store.update_asset(self.admin,self.aid,{'retired':True})

    def test_return_allowed_after_rejected_transfer(self):
        self.store.reject_transfer(self.bea,self.request())
        self.store.return_asset(self.admin,self.aid,'good')
        self.assertEqual(self.store.dashboard(self.admin)['assigned'],0)

    def test_concurrent_return_and_request_stay_consistent(self):
        for _ in range(5):
            results={}
            def ret():
                try: self.store.return_asset(self.admin,self.aid,'good'); results['return']='ok'
                except Problem as ex: results['return']=ex.status
            def req():
                try: self.request(); results['request']='ok'
                except Problem as ex: results['request']=ex.status
            threads=[threading.Thread(target=ret),threading.Thread(target=req)]
            for t in threads:t.start()
            for t in threads:t.join()
            self.assertEqual(sorted(map(str,results.values())),['409','ok'])
            pending=[t for t in self.store.transfers(self.admin) if t['status']=='pending']
            assigned=self.store.dashboard(self.admin)['assigned']
            # Either returned with no pending transfer, or still assigned with exactly one.
            self.assertIn((assigned,len(pending)),[(0,0),(1,1)])
            for t in pending: self.store.cancel_transfer(self.admin,t['id'],'Test reset')
            if not assigned: self.store.assign(self.admin,self.aid,self.alex['id'])

    def test_chained_transfers(self):
        self.store.accept_transfer(self.bea,self.request())
        self.store.accept_transfer(self.carl,self.request(self.carl))
        self.assertEqual(self.holder(),self.carl['id'])
        self.assertEqual(len(self.store.assignments(self.admin)),3)
        self.assertEqual(len(self.store.transfers(self.bea)),2)

    def test_concurrent_requests_only_one_pending(self):
        successes=[]
        def attempt(to):
            try: successes.append(self.request(to))
            except Problem as ex: self.assertEqual(ex.status,409)
        threads=[threading.Thread(target=attempt,args=(p,)) for p in (self.bea,self.carl)*3]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(successes),1)

    def test_concurrent_accept_and_cancel(self):
        tid=self.request()
        results=[]
        def run(fn,actor,*extra):
            try: fn(actor,tid,*extra); results.append('ok')
            except Problem as ex: results.append(ex.status)
        threads=[threading.Thread(target=run,args=(self.store.accept_transfer,self.bea)),
                 threading.Thread(target=run,args=(self.store.cancel_transfer,self.admin,'Changed plans'))]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sorted(results,key=str),[409,'ok'])
        self.assertEqual(self.store.dashboard(self.admin)['assigned'],1)

    def test_existing_database_is_upgraded_without_data_loss(self):
        # Simulate a database created before transfers existed.
        with self.store.db(write=True) as db:
            db.execute('DROP TABLE transfers')
        reopened=Store(self.store.path)
        self.assertEqual(reopened.assets(self.admin)[0]['assigned_to'],'Alex Worker')
        self.assertEqual(len(reopened.assignments(self.admin)),1)
        self.assertEqual(reopened.transfers(self.admin),[])
        tid=reopened.request_transfer(self.manager,self.aid,self.bea['id'],'Handover')['id']
        reopened.accept_transfer(self.bea,tid)
        self.assertEqual(reopened.assets(self.admin)[0]['assigned_to'],'Bea Colleague')

if __name__=='__main__': unittest.main()
