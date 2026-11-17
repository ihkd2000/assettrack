import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from assettrack.store import Store
from assettrack.server import AppServer

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.store=Store(Path(cls.tmp.name)/'web.sqlite3')
        cls.store.bootstrap('safe-password-123')
        cls.server=AppServer(('127.0.0.1',0),cls.store)
        cls.port=cls.server.server_address[1]
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.server.testing = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=3)
        cls.tmp.cleanup()

    def request(self,method,path,data=None,cookie='',origin=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        headers={'Cookie':cookie}
        body=None
        if data is not None:
            headers['Content-Type']='application/json'
            body=json.dumps(data)
        if origin:headers['Origin']=origin
        conn.request(method,path,body,headers)
        res=conn.getresponse();headers_out=dict(res.getheaders());body=res.read()
        conn.close()
        return res.status,headers_out,json.loads(body) if path.startswith('/api/') else body

    def login(self):
        status,headers,_=self.request('POST','/api/login',{'username':'admin','password':'safe-password-123'})
        self.assertEqual(status,200)
        return headers['Set-Cookie'].split(';')[0]

    def test_unauthorized(self):
        status,_,_=self.request('GET','/api/assets')
        self.assertEqual(status,401)

    def test_login_and_asset_endpoints(self):
        cookie=self.login()
        status,_,result=self.request('POST','/api/assets',{'code':'WEB-001','name':'Printer','category':'Office'},cookie)
        self.assertEqual(status,200)
        status,_,result=self.request('GET','/api/assets',cookie=cookie)
        self.assertEqual(status,200)
        self.assertTrue(any(a['code']=='WEB-001' for a in result))

    def test_origin_protection(self):
        cookie=self.login()
        status,_,_=self.request('POST','/api/assets',{'code':'BAD','name':'Printer','category':'Office'},cookie,origin='https://evil.example')
        self.assertEqual(status,403)

    def login_as(self,username,password):
        status,headers,_=self.request('POST','/api/login',{'username':username,'password':password})
        self.assertEqual(status,200)
        return headers['Set-Cookie'].split(';')[0]

    def test_transfer_endpoints(self):
        admin=self.login()
        for name in ('tx_from','tx_to','tx_other'):
            self.request('POST','/api/users',{'username':name,'full_name':name,'role':'employee','password':'long-password-123'},admin)
        users={u['username']:u['id'] for u in self.request('GET','/api/users',cookie=admin)[2]}
        _,_,asset=self.request('POST','/api/assets',{'code':'WEB-TX','name':'Laptop','category':'IT'},admin)
        path=f"/api/assets/{asset['id']}"
        status,_,body=self.request('POST',path+'/transfer',{'employee_id':users['tx_to'],'reason':'Handover'},admin)
        self.assertEqual((status,body['error']),(409,'Only assigned assets can be transferred'))
        self.request('POST',path+'/assign',{'employee_id':users['tx_from']},admin)
        status,_,created=self.request('POST',path+'/transfer',{'employee_id':users['tx_to'],'reason':'Handover'},admin)
        self.assertEqual(status,200)
        tid=created['id']
        sender=self.login_as('tx_from','long-password-123')
        receiver=self.login_as('tx_to','long-password-123')
        other=self.login_as('tx_other','long-password-123')
        self.assertEqual(self.request('POST',path+'/transfer',{'employee_id':users['tx_other'],'reason':'x'},sender)[0],403)
        self.assertEqual(self.request('GET','/api/transfers',cookie=other)[2],[])
        self.assertEqual(len(self.request('GET','/api/transfers',cookie=sender)[2]),1)
        status,_,body=self.request('POST',path+'/return',{'condition':'good'},admin)
        self.assertEqual(status,409)
        self.assertIn('cancel the transfer',body['error'])
        self.assertEqual(self.request('GET','/api/transfers',cookie=admin)[2][0]['status'],'pending')
        self.assertEqual(self.request('POST',f'/api/transfers/{tid}/accept',{},sender)[0],403)
        self.assertEqual(self.request('POST',f'/api/transfers/{tid}/accept',{},other)[0],404)
        self.assertEqual(self.request('POST',f'/api/transfers/{tid}/bogus',{},receiver)[0],404)
        self.assertEqual(self.request('POST',f'/api/transfers/{tid}/accept',{},receiver)[0],200)
        mine=self.request('GET','/api/assets',cookie=receiver)[2]
        self.assertEqual([a['code'] for a in mine],['WEB-TX'])
        self.assertEqual(self.request('GET','/api/assets',cookie=sender)[2],[])
        self.assertEqual(self.request('POST',f'/api/transfers/{tid}/reject',{},receiver)[0],409)

    def test_account_status_endpoint(self):
        admin=self.login()
        self.request('POST','/api/users',{'username':'leaver','full_name':'Lee Leaver','role':'employee','password':'long-password-123'},admin)
        uid=next(u['id'] for u in self.request('GET','/api/users',cookie=admin)[2] if u['username']=='leaver')
        leaver=self.login_as('leaver','long-password-123')
        self.assertEqual(self.request('POST',f'/api/users/{uid}/status',{'active':False},leaver)[0],403)
        self.assertEqual(self.request('POST',f'/api/users/{uid}/delete',{'active':False},admin)[0],404)
        status,_,body=self.request('POST',f'/api/users/{uid}/status',{'active':False},admin)
        self.assertEqual((status,body['active']),(200,0))
        self.assertEqual(self.request('GET','/api/me',cookie=leaver)[0],401)   # signed out immediately
        self.assertEqual(self.request('POST','/api/login',{'username':'leaver','password':'long-password-123'})[0],401)
        self.assertEqual(self.request('POST',f'/api/users/{uid}/status',{'active':True},admin)[2]['active'],1)
        self.login_as('leaver','long-password-123')

    def test_asset_detail_and_cancel_reason_endpoints(self):
        admin=self.login()
        for name in ('hd_from','hd_to','hd_other'):
            self.request('POST','/api/users',{'username':name,'full_name':name,'role':'employee','password':'long-password-123'},admin)
        users={u['username']:u['id'] for u in self.request('GET','/api/users',cookie=admin)[2]}
        _,_,asset=self.request('POST','/api/assets',{'code':'WEB-HD','name':'Tablet','category':'IT'},admin)
        path=f"/api/assets/{asset['id']}"
        self.request('POST',path+'/assign',{'employee_id':users['hd_from']},admin)
        tid=self.request('POST',path+'/transfer',{'employee_id':users['hd_to'],'reason':'Desk move'},admin)[2]['id']
        status,_,detail=self.request('GET',path,cookie=admin)
        self.assertEqual(status,200)
        self.assertEqual((detail['asset']['code'],detail['transfers'][0]['status']),('WEB-HD','pending'))
        other=self.login_as('hd_other','long-password-123')
        self.assertEqual(self.request('GET',path,cookie=other)[0],404)
        self.assertEqual(self.request('GET',path,cookie=self.login_as('hd_to','long-password-123'))[0],200)
        self.assertEqual(self.request('GET','/api/assets/abc',cookie=admin)[0],404)
        self.assertEqual(self.request('GET',path+'/extra',cookie=admin)[0],404)
        self.assertEqual(self.request('POST',f'/api/transfers/{tid}/cancel',{'reason':['bad']},admin)[0],400)
        status,_,body=self.request('POST',f'/api/transfers/{tid}/cancel',{},admin)
        self.assertEqual((status,body['error']),(400,'Enter a reason for cancelling this transfer'))
        self.assertEqual(self.request('POST',f'/api/transfers/{tid}/cancel',{'reason':'   '},admin)[0],400)
        self.assertEqual(self.request('GET',path,cookie=admin)[2]['transfers'][0]['status'],'pending')
        self.assertEqual(self.request('POST',f'/api/transfers/{tid}/cancel',{'reason':'Wrong employee'},admin)[0],200)
        t=self.request('GET',path,cookie=admin)[2]['transfers'][0]
        self.assertEqual((t['status'],t['resolution_note'],t['resolved_by_name']),('cancelled','Wrong employee','Administrator'))

    def test_frontend_is_available(self):
        status,_,body=self.request('GET','/')
        self.assertEqual(status,200)
        self.assertIn(b'AssetTrack',body)

if __name__=='__main__': unittest.main()
