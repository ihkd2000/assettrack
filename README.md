# AssetTrack

AssetTrack is a lightweight internal equipment management application for small teams. Track laptops, monitors, printers, and other assets; assign them to employees; record returns and condition changes; and review an activity trail. It is a functional baseline intended for real use and future extensions, not a prebuilt multi-step evaluation exercise.

## Requirements

- Python **3.10 or later**
- No external dependencies. Uses `http.server`, `sqlite3`, `hashlib`, `unittest`, and browser-native HTML/CSS/JavaScript.

## First-time setup

From the repository root:

```bash
python -m assettrack --init-admin
```

Enter and confirm a password of at least 12 characters. The initial username is **admin**. No default password is shipped. This creates `assettrack.sqlite3` in the current directory.

Start the application:

```bash
python -m assettrack
```

Visit **http://127.0.0.1:8000** and log in as `admin` using the password you chose. If the database already contains users, skip `--init-admin` and start normally.

To store data elsewhere, use `--db path/to/assets.sqlite3` for both setup and startup. Use `--port 8080` to change the port.

### Recover an existing administrator password

If you have access to the machine holding the AssetTrack SQLite database but have forgotten the administrator password, **stop the app**, back up the database, and run:

```bash
python -m assettrack --reset-admin-password admin
```

The command prompts securely for a new password (10+ characters) and confirmation. It resets **only an existing administrator**; it does not create a new account or delete asset data. That administrator's existing sessions are invalidated. If the admin account is inactive, it stays inactive; reset does not override deactivation.

For another admin username use `--reset-admin-password USERNAME`. With a custom database path, include `--db path/to/assets.sqlite3`. This is **local-console recovery for someone authorized to access the database** and is intentionally not exposed through the web interface.


## Basic workflow

1. Log in as admin and select **People** → **Add user**. Create an employee and optionally a manager.
2. Select **Assets** → **Register asset**, and enter its unique code, name, category, condition, and optional notes.
3. On an available asset, select **Assign** and choose an active employee.
4. The employee can sign in and see their currently assigned equipment and assignment history.
5. An admin or manager selects **Return** to record the condition on return. The asset becomes available again.
6. To hand an assigned asset directly to a colleague, an admin or manager selects **Transfer**, chooses the receiving employee, and enters a reason. The receiving employee accepts or rejects it under **Transfers**.
7. Select an asset code anywhere in the app to open its **details page**: current holder, transfer history with each transfer's lifecycle, assignment history, and activity.
8. Use **History** to review who registered, edited, assigned, returned, or transferred equipment.

## Roles and rules

| Role | Capabilities |
| --- | --- |
| Admin | Register/edit/retire assets, assign/return, request/cancel transfers, create and deactivate/reactivate users, view all transfers and history |
| Manager | Register/edit/retire assets, assign/return, request/cancel transfers, view user list, all transfers and history |
| Employee | View currently assigned assets, own assignment records, related history, and transfers to or from them; accept/reject transfers addressed to them |

- Each asset code is unique (case insensitive).
- An asset has at most **one active assignment**. The application checks this inside a write transaction and enforces it with a partial unique SQLite index.
- Only active employee accounts can receive assignments or transfers.
- Admins can deactivate and reactivate accounts from **People**. A deactivated user is signed out immediately and cannot sign in, receive assignments, or respond to transfers. Their current assignments stay in place until an admin or manager returns or transfers the assets. Admins cannot deactivate themselves or the last active administrator. Account changes are recorded in history.
- Assigned assets must be returned before retirement.
- Employee and manager permissions are enforced on the server, not just hidden in the UI.
- Asset changes and assignment actions are recorded in persistent activity history.

### Transfers

- Only assets that are currently assigned can be transferred, and only to a different active employee.
- An asset can have at most **one pending transfer**, checked in a write transaction and enforced by a partial unique index.
- The asset stays assigned to its current holder while the transfer is pending. Only the receiving employee can accept or reject it; admins and managers can cancel it.
- On acceptance, the current assignment is closed and a new one opened for the receiver in a single transaction. The assignment history shows "Transferred from/to", and the request, acceptance, rejection, or cancellation is recorded in the asset's activity history.
- Returning and transferring are separate processes: an asset with a pending transfer **cannot be returned**. An admin or manager must cancel the transfer first (or the receiver must accept or reject it). A blocked return changes nothing: the assignment, asset condition, and transfer status stay as they were.
- If the receiving employee's account is deactivated while a transfer is pending, the transfer stays pending and the asset stays with its current holder. The inactive recipient cannot accept or reject it; admins and managers see it flagged on **Transfers** and can cancel it. Reactivating the account lets the employee respond again.
- Cancelling a transfer **requires a reason** (up to 500 characters). The server rejects a blank or missing reason without changing the transfer. The reason appears in the transfer history and the activity entry. Cancellations made before reasons were required are labelled as such.
- Upgrading: the `transfers` table (and the cancellation-reason column for databases created earlier) is added automatically on startup; existing data is untouched.

### Reviewing transfers

Each transfer's lifecycle is read straight from its transfer record, so there is no second log that could get out of sync. For every transfer you can see the employees involved, who initiated it and why, its current status, and who accepted, rejected, or cancelled it, when, and why it was cancelled.

- **Transfers** page: filter by status, search by asset, employee, or reason, and select **History** on a row for the full lifecycle.
- **Asset details** page: every transfer of that asset with its lifecycle, alongside the asset's assignment history and activity.
- **Assignment history** (Assignments page and asset details) has a **Notes** column showing each assignment's status (`active`, `returned`, or `transferred`) and how it started and ended: for example "Received by transfer from Alex Worker", "Transfer to Bea Colleague pending", "Transferred to Dana Lee on …", or "Returned to inventory". The **Returned** column is filled only for actual returns.
- Employees see the same views limited to transfers they were part of. They can open details for assets they hold or are being offered.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests use isolated temporary SQLite databases. No production database is modified.

## Project structure

- `assettrack/__main__.py`: CLI and server entry point
- `assettrack/server.py`: HTTP endpoints, sessions and static frontend delivery
- `assettrack/store.py`: SQLite schema, role checks and business operations
- `assettrack/static/`: browser UI (HTML, CSS, vanilla JS)
- `tests/`: persistence, concurrency, authorization and HTTP tests

## Security and deployment notes

Passwords are stored using salted PBKDF2-HMAC-SHA256 hashes; sessions use random tokens whose hashes are persisted. Session cookies are HttpOnly with SameSite=Strict. The server defaults to the loopback interface for local or trusted-machine use.

**This baseline is designed for local/internal evaluation**, not direct public internet exposure. Before deploying for shared organizational use, add HTTPS through a reverse proxy, automated backups, secure secret management, user lifecycle/password reset workflows, and appropriate operational monitoring. Do not bind to a publicly accessible interface without these protections.

## Current limitations

There is no external identity provider, file attachment handling, email notification system, bulk import, or automated backups. Employee accounts are created and deactivated by administrators; there is no self-service password reset. Asset codes are immutable after registration; asset details can be updated. This version intentionally keeps the scope limited to equipment registration, assignment, return, employee-to-employee transfer, and audit history.
