"""SQLite persistence and business rules for AssetTrack."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROLES = ('admin', 'manager', 'employee')
CONDITIONS = ('new', 'good', 'fair', 'damaged')

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)

def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 260_000)
    return 'pbkdf2_sha256$260000$' + salt.hex() + '$' + digest.hex()

def password_ok(password, saved):
    try:
        algo, rounds, salt, digest = saved.split('$')
        return algo == 'pbkdf2_sha256' and hmac.compare_digest(
            hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)),
            bytes.fromhex(digest))
    except (ValueError, AttributeError):
        return False

def clean(value, field, limit=120, required=True):
    if not isinstance(value, str):
        raise Problem(f'{field} must be text')
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise Problem(f'{field} must be {"1 to " if required else "at most "}{limit} characters')
    return value

def require_role(actor, *roles):
    if actor['role'] not in roles:
        raise Problem('Permission denied', 403)

class Store:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        with self.db(write=True) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    full_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','manager','employee')),
                    password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    name TEXT NOT NULL, category TEXT NOT NULL,
                    condition TEXT NOT NULL CHECK(condition IN ('new','good','fair','damaged')),
                    notes TEXT NOT NULL DEFAULT '', retired INTEGER NOT NULL DEFAULT 0 CHECK(retired IN (0,1)),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL REFERENCES assets(id),
                    employee_id INTEGER NOT NULL REFERENCES users(id),
                    assigned_at TEXT NOT NULL, assigned_by INTEGER NOT NULL REFERENCES users(id),
                    returned_at TEXT, returned_by INTEGER REFERENCES users(id), return_condition TEXT,
                    CHECK(returned_at IS NULL OR returned_by IS NOT NULL)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_assignment ON assignments(asset_id)
                    WHERE returned_at IS NULL;
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id),
                    asset_id INTEGER REFERENCES assets(id), kind TEXT NOT NULL,
                    detail TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_assignment_employee ON assignments(employee_id,returned_at);
                CREATE INDEX IF NOT EXISTS idx_events_asset ON events(asset_id,id);
                CREATE TABLE IF NOT EXISTS transfers (
                    id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL REFERENCES assets(id),
                    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
                    from_employee_id INTEGER NOT NULL REFERENCES users(id),
                    to_employee_id INTEGER NOT NULL REFERENCES users(id),
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','accepted','rejected','cancelled')),
                    requested_by INTEGER NOT NULL REFERENCES users(id), requested_at TEXT NOT NULL,
                    resolved_by INTEGER REFERENCES users(id), resolved_at TEXT,
                    new_assignment_id INTEGER REFERENCES assignments(id),
                    resolution_note TEXT,
                    CHECK(from_employee_id <> to_employee_id),
                    CHECK((status = 'pending') = (resolved_at IS NULL)),
                    CHECK(status <> 'pending' OR resolved_by IS NULL),
                    CHECK((status = 'accepted') = (new_assignment_id IS NOT NULL))
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_transfer ON transfers(asset_id)
                    WHERE status = 'pending';
                CREATE INDEX IF NOT EXISTS idx_transfers_to ON transfers(to_employee_id,status);
                CREATE INDEX IF NOT EXISTS idx_transfers_from ON transfers(from_employee_id);
                CREATE INDEX IF NOT EXISTS idx_transfers_asset ON transfers(asset_id,id);
            ''')
            # Databases created before cancellation notes existed get the column added; no data changes.
            if 'resolution_note' not in {r['name'] for r in db.execute('PRAGMA table_info(transfers)')}:
                try:
                    db.execute('ALTER TABLE transfers ADD COLUMN resolution_note TEXT')
                except sqlite3.OperationalError as ex:  # another process upgraded it first
                    if 'duplicate column' not in str(ex): raise

    def has_users(self):
        with self.db() as db:
            return db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None

    def bootstrap(self, password):
        if len(password) < 10:
            raise Problem('Password must be at least 10 characters')
        with self.db(write=True) as db:
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise Problem('Users already exist; refusing to create another bootstrap admin')
            db.execute('INSERT INTO users(username,full_name,role,password_hash,created_at) VALUES(?,?,?,?,?)',
                       ('admin','Administrator','admin',password_hash(password),now()))

    def reset_admin_password(self, username, password):
        """Local-console recovery for an existing administrator.

        This method must never be exposed through HTTP: local database access is
        the authorization boundary for recovery when the old password is lost.
        """
        username = clean(username, 'Admin username', 40)
        if not isinstance(password, str) or len(password) < 10:
            raise Problem('Password must be at least 10 characters')
        with self.db(write=True) as db:
            user = db.execute(
                "SELECT id FROM users WHERE username=? AND role='admin'", (username,)
            ).fetchone()
            if user is None:
                raise Problem('Administrator account not found', 404)
            db.execute(
                'UPDATE users SET password_hash=? WHERE id=?',
                (password_hash(password), user['id'])
            )
            db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))

    def authenticate(self, username, password):
        with self.db(write=True) as db:
            user = db.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone()
            if not user or not password_ok(password, user['password_hash']):
                raise Problem('Invalid credentials',401)
            token = secrets.token_urlsafe(32)
            expires = (datetime.now(timezone.utc)+timedelta(hours=12)).isoformat(timespec='seconds')
            db.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(),user['id'],expires))
            return token, self.public_user(user)

    def actor(self, token):
        if not token:
            raise Problem('Sign in required',401)
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.db() as db:
            user = db.execute('''SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.active=1''',(key,now())).fetchone()
            if not user:
                raise Problem('Session expired; sign in again',401)
            return self.public_user(user)

    def logout(self, token):
        if token:
            with self.db(write=True) as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))

    @staticmethod
    def public_user(row):
        return {key:row[key] for key in ('id','username','full_name','role','active')}

    def users(self, actor):
        require_role(actor,'admin','manager')
        with self.db() as db:
            return [self.public_user(row) for row in db.execute('SELECT * FROM users ORDER BY full_name')]

    def create_user(self, actor, data):
        require_role(actor,'admin')
        username = clean(data.get('username'), 'Username', 40)
        name = clean(data.get('full_name'), 'Full name')
        role = data.get('role')
        password = data.get('password')
        if role not in ROLES or not isinstance(password,str) or len(password)<10:
            raise Problem('Choose a valid role and a password of at least 10 characters')
        with self.db(write=True) as db:
            try:
                cur=db.execute('INSERT INTO users(username,full_name,role,password_hash,created_at) VALUES(?,?,?,?,?)',
                               (username,name,role,password_hash(password),now()))
            except sqlite3.IntegrityError:
                raise Problem('Username already exists',409) from None
            return {'id':cur.lastrowid,'username':username,'full_name':name,'role':role,'active':1}

    def set_user_active(self, actor, user_id, active):
        """Deactivate or reactivate an account. Deactivated users are signed out at once and can no
        longer sign in, receive assignments, or accept transfers. Their current assignments stay
        in place until an admin/manager returns or transfers the assets."""
        require_role(actor,'admin')
        if not isinstance(active,bool):
            raise Problem('Active must be true or false')
        with self.db(write=True) as db:
            user=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            if not user:
                raise Problem('User not found',404)
            if not active:
                if user['id']==actor['id']:
                    raise Problem('You cannot deactivate your own account',409)
                if user['role']=='admin' and not db.execute(
                        "SELECT 1 FROM users WHERE role='admin' AND active=1 AND id<>?",(user_id,)).fetchone():
                    raise Problem('At least one active administrator is required',409)
            if bool(user['active'])!=active:
                db.execute('UPDATE users SET active=? WHERE id=?',(int(active),user_id))
                if not active:
                    db.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
                self._log(db,actor,None,'user_activated' if active else 'user_deactivated',
                          f'{"Reactivated" if active else "Deactivated"} account {user["username"]} ({user["full_name"]})')
            pending=db.execute("SELECT COUNT(*) FROM transfers WHERE to_employee_id=? AND status='pending'",
                               (user_id,)).fetchone()[0]
            return {'id':user_id,'active':int(active),'pending_transfers':pending}

    def events(self, actor, limit=100, asset_id=None):
        with self.db() as db:
            query='''SELECT e.id,e.asset_id,e.kind,e.detail,e.created_at,u.full_name AS actor_name,
                a.code AS asset_code FROM events e LEFT JOIN users u ON u.id=e.actor_id
                LEFT JOIN assets a ON a.id=e.asset_id WHERE 1=1'''
            params=[]
            if actor['role']=='employee':
                query+=' AND e.asset_id IN (SELECT asset_id FROM assignments WHERE employee_id=?)'
                params.append(actor['id'])
            if asset_id is not None:
                query+=' AND e.asset_id=?'
                params.append(asset_id)
            query+=' ORDER BY e.id DESC LIMIT ?'
            params.append(min(200,max(1,int(limit))))
            return [dict(row) for row in db.execute(query,params)]

    def _log(self,db,actor,asset_id,kind,detail):
        db.execute('INSERT INTO events(actor_id,asset_id,kind,detail,created_at) VALUES(?,?,?,?,?)',
                   (actor['id'],asset_id,kind,detail,now()))

    def assets(self,actor):
        with self.db() as db:
            return self._assets(db,holder_id=actor['id'] if actor['role']=='employee' else None)

    def _assets(self,db,holder_id=None,asset_id=None):
        query='''SELECT a.*,s.id AS assignment_id,s.employee_id,u.full_name AS assigned_to,
            t.id AS pending_transfer_id,tu.full_name AS pending_transfer_to
            FROM assets a LEFT JOIN assignments s ON s.asset_id=a.id AND s.returned_at IS NULL
            LEFT JOIN users u ON u.id=s.employee_id
            LEFT JOIN transfers t ON t.asset_id=a.id AND t.status='pending'
            LEFT JOIN users tu ON tu.id=t.to_employee_id WHERE 1=1'''
        args=[]
        if holder_id is not None:
            query+=' AND s.employee_id=?'
            args.append(holder_id)
        if asset_id is not None:
            query+=' AND a.id=?'
            args.append(asset_id)
        query+=' ORDER BY a.code'
        return [self._asset(row) for row in db.execute(query,args)]

    @staticmethod
    def _asset(row):
        result=dict(row)
        result['status']='retired' if row['retired'] else ('assigned' if row['assignment_id'] else 'available')
        return result

    def create_asset(self,actor,data):
        require_role(actor,'admin','manager')
        code=clean(data.get('code'),'Asset code',40).upper()
        name=clean(data.get('name'),'Asset name')
        category=clean(data.get('category'),'Category',80)
        condition=data.get('condition','good')
        if condition not in CONDITIONS:
            raise Problem('Invalid condition')
        notes=clean(data.get('notes',''),'Notes',500,False)
        with self.db(write=True) as db:
            try:
                cur=db.execute('''INSERT INTO assets(code,name,category,condition,notes,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?)''',(code,name,category,condition,notes,now(),now()))
            except sqlite3.IntegrityError:
                raise Problem('Asset code already exists',409) from None
            self._log(db,actor,cur.lastrowid,'asset_created',f'Asset {code} registered')
            return {'id':cur.lastrowid}

    def update_asset(self,actor,asset_id,data):
        require_role(actor,'admin','manager')
        with self.db(write=True) as db:
            existing=db.execute('SELECT * FROM assets WHERE id=?',(asset_id,)).fetchone()
            if not existing:
                raise Problem('Asset not found',404)
            name=clean(data.get('name',existing['name']),'Asset name')
            category=clean(data.get('category',existing['category']),'Category',80)
            condition=data.get('condition',existing['condition'])
            if condition not in CONDITIONS:
                raise Problem('Invalid condition')
            notes=clean(data.get('notes',existing['notes']),'Notes',500,False)
            retired=data.get('retired',bool(existing['retired']))
            if not isinstance(retired,bool):
                raise Problem('Retired must be true or false')
            if retired and db.execute('SELECT 1 FROM assignments WHERE asset_id=? AND returned_at IS NULL',(asset_id,)).fetchone():
                raise Problem('Return this asset before retiring it',409)
            db.execute('''UPDATE assets SET name=?,category=?,condition=?,notes=?,retired=?,updated_at=? WHERE id=?''',
                       (name,category,condition,notes,int(retired),now(),asset_id))
            self._log(db,actor,asset_id,'asset_updated',f'Updated {existing["code"]}')
            return {'id':asset_id}

    def assign(self,actor,asset_id,employee_id):
        require_role(actor,'admin','manager')
        if isinstance(employee_id,bool) or not isinstance(employee_id,int):
            raise Problem('Choose an employee')
        with self.db(write=True) as db:
            asset=db.execute('SELECT * FROM assets WHERE id=?',(asset_id,)).fetchone()
            if not asset:
                raise Problem('Asset not found',404)
            if asset['retired']:
                raise Problem('Retired assets cannot be assigned',409)
            if db.execute('SELECT 1 FROM assignments WHERE asset_id=? AND returned_at IS NULL',(asset_id,)).fetchone():
                raise Problem('Asset is already assigned',409)
            employee=db.execute('SELECT * FROM users WHERE id=? AND active=1 AND role="employee"',(employee_id,)).fetchone()
            if not employee:
                raise Problem('Choose an active employee',400)
            try:
                db.execute('''INSERT INTO assignments(asset_id,employee_id,assigned_at,assigned_by)
                    VALUES(?,?,?,?)''',(asset_id,employee_id,now(),actor['id']))
            except sqlite3.IntegrityError:
                raise Problem('Asset is already assigned',409) from None
            self._log(db,actor,asset_id,'asset_assigned',f'Assigned to {employee["full_name"]}')
            return {'ok':True}

    def return_asset(self,actor,asset_id,condition):
        require_role(actor,'admin','manager')
        if condition not in CONDITIONS:
            raise Problem('Choose a valid return condition')
        with self.db(write=True) as db:
            assignment=db.execute('SELECT id FROM assignments WHERE asset_id=? AND returned_at IS NULL',(asset_id,)).fetchone()
            if not assignment:
                raise Problem('Asset is not assigned',409)
            # Returning and transferring are separate processes: a pending transfer must be
            # cancelled explicitly first. Checked before any write, inside the same transaction.
            pending=db.execute('''SELECT u.full_name AS to_name FROM transfers t
                JOIN users u ON u.id=t.to_employee_id WHERE t.asset_id=? AND t.status='pending' ''',(asset_id,)).fetchone()
            if pending:
                raise Problem(f'A transfer to {pending["to_name"]} is pending for this asset; '
                              'cancel the transfer before returning the asset',409)
            db.execute('''UPDATE assignments SET returned_at=?,returned_by=?,return_condition=? WHERE id=?''',
                       (now(),actor['id'],condition,assignment['id']))
            db.execute('UPDATE assets SET condition=?,updated_at=? WHERE id=?',(condition,now(),asset_id))
            self._log(db,actor,asset_id,'asset_returned',f'Returned in {condition} condition')
            return {'ok':True}

    def assignments(self,actor,asset_id=None):
        with self.db() as db:
            query='''SELECT s.id,s.asset_id,a.code AS asset_code,a.name AS asset_name,
              s.employee_id,u.full_name AS employee_name,s.assigned_at,s.returned_at,
              s.return_condition,uto.full_name AS transferred_to,ufrom.full_name AS transferred_from,
              CASE WHEN s.returned_at IS NULL THEN 'active' WHEN tout.id IS NOT NULL THEN 'transferred'
                   ELSE 'returned' END AS status,
              upend.full_name AS pending_transfer_to
              FROM assignments s JOIN assets a ON a.id=s.asset_id
              JOIN users u ON u.id=s.employee_id
              LEFT JOIN transfers tout ON tout.assignment_id=s.id AND tout.status='accepted'
              LEFT JOIN users uto ON uto.id=tout.to_employee_id
              LEFT JOIN transfers tpend ON tpend.assignment_id=s.id AND tpend.status='pending'
              LEFT JOIN users upend ON upend.id=tpend.to_employee_id
              LEFT JOIN transfers tin ON tin.new_assignment_id=s.id
              LEFT JOIN users ufrom ON ufrom.id=tin.from_employee_id WHERE 1=1'''
            args=[]
            if actor['role']=='employee':
                query+=' AND s.employee_id=?'
                args.append(actor['id'])
            if asset_id is not None:
                query+=' AND s.asset_id=?'
                args.append(asset_id)
            query+=' ORDER BY s.id DESC LIMIT 300'
            return [dict(row) for row in db.execute(query,args)]

    TRANSFER_SELECT='''SELECT t.id,t.asset_id,a.code AS asset_code,a.name AS asset_name,
        t.from_employee_id,uf.full_name AS from_name,uf.active AS from_active,
        t.to_employee_id,ut.full_name AS to_name,ut.active AS to_active,
        t.reason,t.status,t.requested_by,t.requested_at,ur.full_name AS requested_by_name,
        ur.role AS requested_by_role,t.resolved_by,t.resolved_at,ux.full_name AS resolved_by_name,
        ux.role AS resolved_by_role,t.resolution_note
        FROM transfers t JOIN assets a ON a.id=t.asset_id
        JOIN users uf ON uf.id=t.from_employee_id JOIN users ut ON ut.id=t.to_employee_id
        JOIN users ur ON ur.id=t.requested_by LEFT JOIN users ux ON ux.id=t.resolved_by'''

    def transfers(self,actor,asset_id=None):
        """Admins/managers see every transfer; employees only those they send or receive."""
        with self.db() as db:
            query,args=self.TRANSFER_SELECT+' WHERE 1=1',[]
            if actor['role']=='employee':
                query+=' AND (t.from_employee_id=? OR t.to_employee_id=?)'
                args+=[actor['id'],actor['id']]
            if asset_id is not None:
                query+=' AND t.asset_id=?'
                args.append(asset_id)
            query+=" ORDER BY t.status='pending' DESC, t.id DESC LIMIT 300"
            return [dict(row) for row in db.execute(query,args)]

    def request_transfer(self,actor,asset_id,employee_id,reason):
        require_role(actor,'admin','manager')
        if isinstance(employee_id,bool) or not isinstance(employee_id,int):
            raise Problem('Choose an employee')
        reason=clean(reason,'Reason',500)
        with self.db(write=True) as db:
            asset=db.execute('SELECT * FROM assets WHERE id=?',(asset_id,)).fetchone()
            if not asset:
                raise Problem('Asset not found',404)
            current=db.execute('''SELECT s.id,s.employee_id,u.full_name FROM assignments s
                JOIN users u ON u.id=s.employee_id WHERE s.asset_id=? AND s.returned_at IS NULL''',(asset_id,)).fetchone()
            if not current:
                raise Problem('Only assigned assets can be transferred',409)
            if current['employee_id']==employee_id:
                raise Problem(f'{current["full_name"]} already has this asset',409)
            if db.execute("SELECT 1 FROM transfers WHERE asset_id=? AND status='pending'",(asset_id,)).fetchone():
                raise Problem('A transfer is already pending for this asset',409)
            target=db.execute("SELECT * FROM users WHERE id=? AND active=1 AND role='employee'",(employee_id,)).fetchone()
            if not target:
                raise Problem('Choose an active employee',400)
            try:
                cur=db.execute('''INSERT INTO transfers(asset_id,assignment_id,from_employee_id,to_employee_id,
                    reason,requested_by,requested_at) VALUES(?,?,?,?,?,?,?)''',
                    (asset_id,current['id'],current['employee_id'],employee_id,reason,actor['id'],now()))
            except sqlite3.IntegrityError:
                raise Problem('A transfer is already pending for this asset',409) from None
            self._log(db,actor,asset_id,'transfer_requested',
                      f'Transfer from {current["full_name"]} to {target["full_name"]} requested: {reason}')
            return {'id':cur.lastrowid}

    def _pending_transfer(self,db,actor,transfer_id):
        transfer=db.execute('''SELECT t.*,a.code,uf.full_name AS from_name,ut.full_name AS to_name
            FROM transfers t JOIN assets a ON a.id=t.asset_id JOIN users uf ON uf.id=t.from_employee_id
            JOIN users ut ON ut.id=t.to_employee_id WHERE t.id=?''',(transfer_id,)).fetchone()
        # Employees may not learn about transfers they are not part of.
        if not transfer or (actor['role']=='employee' and actor['id'] not in
                            (transfer['from_employee_id'],transfer['to_employee_id'])):
            raise Problem('Transfer not found',404)
        if transfer['status']!='pending':
            raise Problem(f'This transfer was already {transfer["status"]}',409)
        return transfer

    def _require_recipient(self,db,actor,transfer):
        if actor['id']!=transfer['to_employee_id']:
            raise Problem('Only the receiving employee can respond to this transfer',403)
        # Re-check the account itself, not just the session, so a deactivated recipient can never respond.
        if not db.execute("SELECT 1 FROM users WHERE id=? AND active=1 AND role='employee'",(actor['id'],)).fetchone():
            raise Problem('Inactive accounts cannot respond to transfers',403)

    def accept_transfer(self,actor,transfer_id):
        with self.db(write=True) as db:
            transfer=self._pending_transfer(db,actor,transfer_id)
            self._require_recipient(db,actor,transfer)
            stamp=now()
            closed=db.execute('''UPDATE assignments SET returned_at=?,returned_by=?
                WHERE id=? AND returned_at IS NULL''',(stamp,transfer['requested_by'],transfer['assignment_id']))
            if closed.rowcount!=1:
                raise Problem('The asset is no longer assigned to the original employee',409)
            cur=db.execute('''INSERT INTO assignments(asset_id,employee_id,assigned_at,assigned_by)
                VALUES(?,?,?,?)''',(transfer['asset_id'],actor['id'],stamp,transfer['requested_by']))
            db.execute('''UPDATE transfers SET status='accepted',resolved_by=?,resolved_at=?,new_assignment_id=?
                WHERE id=?''',(actor['id'],stamp,cur.lastrowid,transfer_id))
            self._log(db,actor,transfer['asset_id'],'transfer_accepted',
                      f'Transferred from {transfer["from_name"]} to {transfer["to_name"]}')
            return {'ok':True}

    def reject_transfer(self,actor,transfer_id):
        with self.db(write=True) as db:
            transfer=self._pending_transfer(db,actor,transfer_id)
            self._require_recipient(db,actor,transfer)
            db.execute('''UPDATE transfers SET status='rejected',resolved_by=?,resolved_at=? WHERE id=?''',
                       (actor['id'],now(),transfer_id))
            self._log(db,actor,transfer['asset_id'],'transfer_rejected',
                      f'{transfer["to_name"]} rejected transfer from {transfer["from_name"]}; asset stays with {transfer["from_name"]}')
            return {'ok':True}

    def cancel_transfer(self,actor,transfer_id,reason):
        require_role(actor,'admin','manager')
        # A reason is mandatory so every cancellation in the transfer history explains itself.
        if reason is None or (isinstance(reason,str) and not reason.strip()):
            raise Problem('Enter a reason for cancelling this transfer')
        reason=clean(reason,'Cancellation reason',500)
        with self.db(write=True) as db:
            transfer=self._pending_transfer(db,actor,transfer_id)
            db.execute('''UPDATE transfers SET status='cancelled',resolved_by=?,resolved_at=?,resolution_note=?
                WHERE id=?''',(actor['id'],now(),reason,transfer_id))
            self._log(db,actor,transfer['asset_id'],'transfer_cancelled',
                      f'Transfer from {transfer["from_name"]} to {transfer["to_name"]} cancelled: {reason}')
            return {'ok':True}

    def asset_detail(self,actor,asset_id):
        """One asset with its transfers, assignments and activity. Employees may open assets they
        currently hold or have a pending incoming transfer for; each section keeps its usual filter."""
        with self.db() as db:
            found=self._assets(db,asset_id=asset_id)
            if not found:
                raise Problem('Asset not found',404)
            if actor['role']=='employee' and not db.execute('''SELECT 1 FROM assignments
                    WHERE asset_id=? AND employee_id=? AND returned_at IS NULL
                UNION SELECT 1 FROM transfers WHERE asset_id=? AND to_employee_id=? AND status='pending' ''',
                    (asset_id,actor['id'],asset_id,actor['id'])).fetchone():
                raise Problem('Asset not found',404)
        return {'asset':found[0],'transfers':self.transfers(actor,asset_id),
                'assignments':self.assignments(actor,asset_id),'events':self.events(actor,200,asset_id)}

    def dashboard(self,actor):
        assets=self.assets(actor)
        return {'total':len(assets),'available':sum(a['status']=='available' for a in assets),
                'assigned':sum(a['status']=='assigned' for a in assets),
                'retired':sum(a['status']=='retired' for a in assets),
                'damaged':sum(a['condition']=='damaged' for a in assets)}
