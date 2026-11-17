"""AssetTrack command-line entrypoint."""
import argparse
import getpass
import os
import secrets
import ipaddress
from pathlib import Path

from .server import AppServer
from .store import Problem, Store

def main():
    parser=argparse.ArgumentParser(description='AssetTrack equipment management')
    parser.add_argument('--db',default=os.environ.get('ASSETTRACK_DB','assettrack.sqlite3'),help='SQLite file')
    parser.add_argument('--host',default=str(ipaddress.IPv4Address('127.0.0.1')),help='Bind address (default localhost)')
    parser.add_argument('--port',type=int,default=8000)
    actions=parser.add_mutually_exclusive_group()
    actions.add_argument('--init-admin',action='store_true',help='Create initial administrator')
    actions.add_argument('--reset-admin-password',metavar='USERNAME',help='Reset an existing administrator password from the local console')
    args=parser.parse_args()
    store=Store(Path(args.db))
    if args.init_admin:
        if store.has_users():
            parser.error('Database already contains users')
        password=getpass.getpass('Choose an admin password (10+ characters): ')
        repeat=getpass.getpass('Repeat password: ')
        if password!=repeat:
            parser.error('Passwords do not match')
        try: store.bootstrap(password)
        except Problem as ex: parser.error(ex.message)
        print('Created administrator: admin')
        return
    if args.reset_admin_password:
        print('Local administrator recovery: existing sessions for this account will be signed out.')
        password=getpass.getpass('New admin password (10+ characters): ')
        repeat=getpass.getpass('Repeat new password: ')
        if password != repeat:
            parser.error('Passwords do not match')
        try:
            store.reset_admin_password(args.reset_admin_password, password)
        except Problem as ex:
            parser.error(ex.message)
        print(f'Password reset for administrator: {args.reset_admin_password}')
        return
    if not store.has_users():
        print('No users yet. Run: python -m assettrack --init-admin')
        return
    with AppServer((args.host,args.port),store) as server:
        print(f'AssetTrack running at http://{args.host}:{args.port} (database: {args.db})')
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            print('\nShutting down')

if __name__=='__main__':
    main()
