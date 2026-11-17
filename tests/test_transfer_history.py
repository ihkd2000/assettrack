import tempfile
import unittest
from pathlib import Path
from assettrack.store import Store, Problem

class TransferHistoryTests(unittest.TestCase):
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

    def request(self,to,reason='Moving to Sales'):
        return self.store.request_transfer(self.manager,self.aid,to['id'],reason)['id']

    def assertProblem(self,status,fn,*args):
        with self.assertRaises(Problem) as cm: fn(*args)
        self.assertEqual(cm.exception.status,status,cm.exception.message)

    def test_lifecycle_fields_for_every_outcome(self):
        self.store.reject_transfer(self.bea,self.request(self.bea,'First try'))
        self.store.cancel_transfer(self.admin,self.request(self.carl,'Second try'),'  Carl is on leave  ')
        self.store.accept_transfer(self.bea,self.request(self.bea,'Third try'))
        rows={t['reason']:t for t in self.store.transfers(self.manager)}
        expected={'First try':('rejected','Bea Colleague','employee',None),
                  'Second try':('cancelled','Administrator','admin','Carl is on leave'),
                  'Third try':('accepted','Bea Colleague','employee',None)}
        for reason,(status,by,role,note) in expected.items():
            t=rows[reason]
            self.assertEqual((t['status'],t['resolved_by_name'],t['resolved_by_role'],t['resolution_note']),(status,by,role,note))
            self.assertEqual((t['requested_by_name'],t['requested_by_role'],t['from_name']),('Sam Manager','manager','Alex Worker'))
            self.assertIsNotNone(t['requested_at']); self.assertIsNotNone(t['resolved_at'])
        # Audit history is unchanged in shape: one event per action, cancellation now says why.
        kinds=[e['kind'] for e in self.store.events(self.admin) if e['kind'].startswith('transfer_')]
        self.assertEqual(kinds,['transfer_accepted','transfer_requested','transfer_cancelled','transfer_requested','transfer_rejected','transfer_requested'])
        self.assertTrue(any(e['detail'].endswith(': Carl is on leave') for e in self.store.events(self.admin)))

    def test_cancel_reason_is_required_and_validated(self):
        tid=self.request(self.bea)
        before=self.store.transfers(self.admin)[0]
        for bad in (None,'','   ','\n\t',42,['x'],'x'*501):
            self.assertProblem(400,self.store.cancel_transfer,self.manager,tid,bad)
        self.assertProblem(400,self.store.cancel_transfer,self.admin,tid,None)
        # Refused cancellations change nothing: transfer still pending, no history entry.
        self.assertEqual(self.store.transfers(self.admin)[0],before)
        self.assertEqual(self.store.events(self.admin)[0]['kind'],'transfer_requested')
        # Permission is still checked first, so employees get 403 whatever they send.
        self.assertProblem(403,self.store.cancel_transfer,self.alex,tid,None)
        self.store.cancel_transfer(self.manager,tid,'  Bea is leaving the team  ')
        t=self.store.transfers(self.admin)[0]
        self.assertEqual((t['status'],t['resolution_note']),('cancelled','Bea is leaving the team'))
        self.assertEqual(self.store.events(self.admin)[0]['detail'],
                         'Transfer from Alex Worker to Bea Colleague cancelled: Bea is leaving the team')

    def test_assignment_status_and_notes_fields(self):
        first=self.store.assignments(self.admin)[0]
        self.assertEqual((first['status'],first['pending_transfer_to']),('active',None))
        self.request(self.bea)
        self.assertEqual(self.store.assignments(self.admin)[0]['pending_transfer_to'],'Bea Colleague')
        self.store.accept_transfer(self.bea,self.store.transfers(self.admin)[0]['id'])
        self.store.return_asset(self.manager,self.aid,'fair')
        self.store.assign(self.admin,self.aid,self.carl['id'])
        rows=self.store.assignments(self.admin)   # newest first
        got=[(r['employee_name'],r['status'],r['transferred_from'],r['transferred_to'],r['return_condition'],r['pending_transfer_to']) for r in rows]
        self.assertEqual(got,[('Carl Outsider','active',None,None,None,None),
                              ('Bea Colleague','returned','Alex Worker',None,'fair',None),
                              ('Alex Worker','transferred',None,'Bea Colleague',None,None)])
        # Rejected/cancelled transfers never mark an assignment as transferred.
        self.store.reject_transfer(self.bea,self.request(self.bea))
        self.store.cancel_transfer(self.manager,self.request(self.bea),'Changed plans')
        self.assertEqual(self.store.assignments(self.admin)[0]['status'],'active')
        self.assertEqual(len(self.store.assignments(self.admin)),3)

    def test_asset_detail_for_managers(self):
        self.store.reject_transfer(self.bea,self.request(self.bea))
        tid=self.request(self.carl)
        other=self.store.create_asset(self.admin,{'code':'MON-1','name':'Monitor','category':'Display'})['id']
        self.store.request_transfer(self.manager,other,self.bea['id'],'x') if False else None
        d=self.store.asset_detail(self.manager,self.aid)
        self.assertEqual((d['asset']['code'],d['asset']['assigned_to'],d['asset']['pending_transfer_id']),('LAP-001','Alex Worker',tid))
        self.assertEqual([t['status'] for t in d['transfers']],['pending','rejected'])
        self.assertEqual(len(d['assignments']),1)
        self.assertTrue(all(e['asset_id']==self.aid for e in d['events']))
        self.assertEqual(self.store.asset_detail(self.admin,other)['transfers'],[])
        self.assertProblem(404,self.store.asset_detail,self.admin,999)

    def test_asset_detail_access_for_employees(self):
        tid=self.request(self.bea)
        # Holder and pending recipient may open it; an unrelated employee may not.
        holder=self.store.asset_detail(self.alex,self.aid)
        self.assertEqual(len(holder['transfers']),1)
        incoming=self.store.asset_detail(self.bea,self.aid)
        self.assertEqual(incoming['assignments'],[])   # Bea never held it: Alex's records stay hidden
        self.assertEqual(incoming['events'],[])         # same rule as the History page
        self.assertProblem(404,self.store.asset_detail,self.carl,self.aid)
        self.store.reject_transfer(self.bea,tid)
        self.assertProblem(404,self.store.asset_detail,self.bea,self.aid)   # offer gone, access gone
        # Employees only see transfers they were part of, even on an asset they hold.
        self.store.cancel_transfer(self.manager,self.request(self.carl),'Wrong person')
        self.assertEqual(len(self.store.asset_detail(self.alex,self.aid)['transfers']),2)
        self.store.accept_transfer(self.bea,self.request(self.bea))
        mine=self.store.asset_detail(self.bea,self.aid)
        self.assertEqual([t['to_name'] for t in mine['transfers']],['Bea Colleague','Bea Colleague'])
        self.assertEqual([a['employee_name'] for a in mine['assignments']],['Bea Colleague'])
        self.assertProblem(404,self.store.asset_detail,self.alex,self.aid)  # handed over

    def test_upgrade_adds_cancel_note_column_without_touching_transfers(self):
        tid=self.request(self.bea,'Before upgrade')
        with self.store.db(write=True) as db:
            db.execute('DROP INDEX IF EXISTS idx_transfers_asset')
            db.execute('ALTER TABLE transfers DROP COLUMN resolution_note')   # pre-upgrade schema
        upgraded=Store(self.store.path)
        t=upgraded.transfers(self.admin)[0]
        self.assertEqual((t['id'],t['status'],t['reason'],t['resolution_note']),(tid,'pending','Before upgrade',None))
        upgraded.cancel_transfer(self.manager,tid,'Done after upgrade')
        self.assertEqual(upgraded.transfers(self.admin)[0]['resolution_note'],'Done after upgrade')
        Store(self.store.path)   # re-running the upgrade is harmless

if __name__=='__main__': unittest.main()
