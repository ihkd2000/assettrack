import tempfile
import unittest
from pathlib import Path
from assettrack.store import Store, Problem

class AccountStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'test.sqlite3')
        self.store.bootstrap('safe-password-123')
        self.admin=self.store.authenticate('admin','safe-password-123')[1]
        mk=lambda u,n,r:self.store.create_user(self.admin,{'username':u,'full_name':n,'role':r,'password':'long-password-123'})
        self.alex=mk('alex','Alex Worker','employee')
        self.bea=mk('bea','Bea Colleague','employee')
        self.manager=mk('manager','Sam Manager','manager')
        self.aid=self.store.create_asset(self.admin,{'code':'LAP-001','name':'Laptop','category':'Computer'})['id']
        self.store.assign(self.admin,self.aid,self.alex['id'])

    def assertProblem(self,status,fn,*args):
        with self.assertRaises(Problem) as cm: fn(*args)
        self.assertEqual(cm.exception.status,status,cm.exception.message)

    def status(self,uid):
        return next(u for u in self.store.users(self.admin) if u['id']==uid)['active']

    def test_deactivation_signs_out_and_blocks_login(self):
        token,_=self.store.authenticate('bea','long-password-123')
        result=self.store.set_user_active(self.admin,self.bea['id'],False)
        self.assertEqual(result,{'id':self.bea['id'],'active':0,'pending_transfers':0})
        self.assertEqual(self.status(self.bea['id']),0)
        self.assertProblem(401,self.store.actor,token)
        self.assertProblem(401,self.store.authenticate,'bea','long-password-123')
        self.assertEqual(self.store.events(self.admin)[0]['kind'],'user_deactivated')
        self.assertProblem(400,self.store.assign,self.admin,
                           self.store.create_asset(self.admin,{'code':'M-1','name':'Monitor','category':'Display'})['id'],self.bea['id'])
        self.assertProblem(400,self.store.request_transfer,self.manager,self.aid,self.bea['id'],'x')
        self.assertNotIn('user_deactivated',[e['kind'] for e in self.store.events(self.alex)])
        # Reactivation restores everything.
        self.store.set_user_active(self.admin,self.bea['id'],True)
        self.assertEqual(self.status(self.bea['id']),1)
        self.store.authenticate('bea','long-password-123')
        self.assertEqual(self.store.events(self.admin)[0]['kind'],'user_activated')

    def test_safeguards(self):
        self.assertProblem(403,self.store.set_user_active,self.manager,self.bea['id'],False)
        self.assertProblem(403,self.store.set_user_active,self.alex,self.bea['id'],False)
        self.assertProblem(409,self.store.set_user_active,self.admin,self.admin['id'],False)  # self
        self.assertProblem(409,self.store.set_user_active,self.admin,self.admin['id'],False)  # last admin
        self.assertProblem(404,self.store.set_user_active,self.admin,999,False)
        self.assertProblem(400,self.store.set_user_active,self.admin,self.bea['id'],'no')
        admin2=self.store.create_user(self.admin,{'username':'admin2','full_name':'Second Admin','role':'admin','password':'long-password-123'})
        self.store.set_user_active(self.admin,admin2['id'],False)   # fine: another active admin remains
        # Even with a stale identity, the last active admin can never be deactivated.
        self.assertProblem(409,self.store.set_user_active,admin2,self.admin['id'],False)
        self.assertEqual(self.status(self.admin['id']),1)

    def test_deactivated_holder_keeps_assignment(self):
        self.store.set_user_active(self.admin,self.alex['id'],False)
        asset=self.store.assets(self.admin)[0]
        self.assertEqual((asset['status'],asset['employee_id']),('assigned',self.alex['id']))
        # Admin/manager can still move the asset on via transfer or return.
        tid=self.store.request_transfer(self.manager,self.aid,self.bea['id'],'Alex left the company')['id']
        self.store.accept_transfer(self.bea,tid)
        self.assertEqual(self.store.assets(self.admin)[0]['employee_id'],self.bea['id'])

    def test_inactive_recipient_cannot_respond_but_manager_can_review_and_cancel(self):
        tid=self.store.request_transfer(self.manager,self.aid,self.bea['id'],'Handover')['id']
        token,_=self.store.authenticate('bea','long-password-123')
        result=self.store.set_user_active(self.admin,self.bea['id'],False)
        self.assertEqual(result['pending_transfers'],1)
        # Session is gone, and even a direct call with the stale identity is refused.
        self.assertProblem(401,self.store.actor,token)
        self.assertProblem(403,self.store.accept_transfer,self.bea,tid)
        self.assertProblem(403,self.store.reject_transfer,self.bea,tid)
        # Nothing moved: still assigned to Alex, transfer still pending and visible to managers.
        self.assertEqual(self.store.assets(self.admin)[0]['employee_id'],self.alex['id'])
        listed=self.store.transfers(self.manager)[0]
        self.assertEqual((listed['status'],listed['to_active'],listed['from_active']),('pending',0,1))
        self.assertEqual(len(self.store.assignments(self.admin)),1)
        self.store.cancel_transfer(self.manager,tid,'Recipient left the company')
        self.assertEqual(self.store.transfers(self.admin)[0]['status'],'cancelled')
        self.store.return_asset(self.manager,self.aid,'good')
        self.assertEqual(self.store.dashboard(self.admin)['assigned'],0)

    def test_reactivated_recipient_can_accept(self):
        tid=self.store.request_transfer(self.manager,self.aid,self.bea['id'],'Handover')['id']
        self.store.set_user_active(self.admin,self.bea['id'],False)
        self.store.set_user_active(self.admin,self.bea['id'],True)
        self.store.accept_transfer(self.bea,tid)
        self.assertEqual(self.store.assets(self.admin)[0]['employee_id'],self.bea['id'])

if __name__=='__main__': unittest.main()
