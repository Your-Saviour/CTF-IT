# Service Credentials Implementation - Testing Plan

## Overview
Testing the service credentials system in the Docker container environment.

## Implementation Status
✅ Database model added to `api/models.py`
✅ Encryption utilities in `api/services/secrets.py`
✅ Database migration script in `api/main.py`
✅ CRUD endpoints in `api/routes/service_credentials.py`
✅ Frontend template in `frontend/templates/service_credentials.html`
✅ Navigation link in `frontend/templates/admin.html`
✅ Seed data logic in `api/main.py`

## Testing Steps

### 1. Build and Start API Container
```bash
cd /Users/jaketownsend/Desktop/Projectsz/CTF-IT
docker compose build api
docker compose up -d api
```

### 2. Verify Database Migration
Check that `service_credentials` table was created:
```bash
docker compose exec api sqlite3 /app/data/ctf.db ".schema service_credentials"
```

Expected output:
- Table should exist with columns: id, service_name, credential_type, username, password, url, description, created_at, updated_at, created_by

### 3. Verify Seed Data
Check that default credentials were created:
```bash
docker compose exec api sqlite3 /app/data/ctf.db "SELECT service_name, credential_type, username FROM service_credentials"
```

Expected output:
- Caldera Admin (admin)
- Semaphore Admin (admin)
- Dockhand (admin)
- Traefik Dashboard (admin)
- Vultr API (token)
- Cloudflare (token)
- AI Agent (token)

### 4. Test API Endpoints
Create a test admin user first (if none exists):

```bash
# Start API
docker compose up -d api

# Create admin user with bootstrap token
curl -X POST http://localhost:8080/auth/register \
  -F "username=admin" \
  -F "password=admin123" \
  -F "event_id=1" \
  -F "admin_bootstrap_token=test-secret-key"

# Login and get session cookie
curl -X POST http://localhost:8080/auth/login \
  -F "username=admin" \
  -F "password=admin123"

# Test credential listing endpoint
curl -X GET http://localhost:8080/admin/service-credentials \
  -H "Cookie: session=<session_cookie>"
```

Expected response: JSON array with credential data

### 5. Test Frontend Access
1. Open browser to `http://localhost:8080/admin`
2. Navigate to "Service Credentials" section
3. Click "Manage Credentials" button
4. Verify:
   - Credential table displays all services
   - Passwords show as masked (••••••••••••)
   - Copy buttons appear for username and password
   - "Open Service" buttons work for URLs
   - "Edit" and "Delete" buttons are functional

### 6. Test CRUD Operations

#### Create Credential
- Click "+ Add New Credential"
- Fill form with test data
- Save
- Verify new credential appears in table

#### Edit Credential
- Click "Edit" on existing credential
- Modify password
- Save
- Verify changes persist

#### Delete Credential
- Click "Delete" on existing credential
- Confirm deletion
- Verify credential is removed

### 7. Security Verification
1. Check that passwords are encrypted in database:
```bash
docker compose exec api sqlite3 /app/data/ctf.db "SELECT service_name, password FROM service_credentials LIMIT 1"
```
- Should show encrypted value starting with `enc:v1:`

2. Verify decryption works in API:
```bash
curl -X GET http://localhost:8080/admin/service-credentials \
  -H "Cookie: session=<session_cookie>" \
  -H "Accept: application/json" | jq '.[0].password' | grep -v '^enc:v1:'
```
- Should show unencrypted password in JSON response

### 8. Test Seed Data Re-population
1. Reset database:
```bash
docker compose down
rm -f /Users/jaketownsend/Desktop/Projectsz/CTF-IT/api/data/ctf.db
docker compose up -d api
```

2. Verify seed data is created automatically:
```bash
docker compose exec api sqlite3 /app/data/ctf.db "SELECT COUNT(*) FROM service_credentials"
```
- Should return 7 (default credentials)

## Expected Outcomes

✅ Database table created successfully
✅ Default credentials auto-populated from environment variables
✅ API endpoints return encrypted passwords correctly
✅ Frontend displays credentials with masked passwords
✅ Copy-to-clipboard buttons work for username and password
✅ Edit and delete operations function correctly
✅ Passwords remain encrypted in database
✅ Seed data re-populates on fresh database creation

## Troubleshooting

If table doesn't exist:
```bash
docker compose exec api python3 -c "from api.database import init_db; init_db()"
```

If seed data not created:
- Verify environment variables in `.env` file
- Check that `DATA_ENCRYPTION_KEY` is set
- Ensure admin user exists before seeding

If frontend not accessible:
- Verify API container is healthy: `docker compose ps api`
- Check logs: `docker compose logs api`
- Verify database file exists and has correct permissions
