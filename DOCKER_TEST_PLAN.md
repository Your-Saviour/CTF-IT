# Docker Testing Plan - Service Credentials System

## Current Environment Analysis

✅ **Dockerfile**: Runtime image exists with uvicorn
✅ **Database**: `ctf.db` already exists in project root
✅ **API Routes**: New `api/routes/service_credentials.py` created
✅ **Templates**: New `frontend/templates/service_credentials.html` created
✅ **Models**: `ServiceCredential` model added to `api/models.py`
✅ **Environment**: `.env` file needs update with deploy variables

## Required Environment Variables

The current `.env` file lacks the deploy-specific variables needed for seeding:
- `DOMAIN` - for service URLs
- `CALDERA_ADMIN_PASSWORD` - default Caldera admin
- `SEMAPHORE_ADMIN_PASSWORD` - Semaphore admin
- `DOCKHAND_ADMIN_PASSWORD` - Dockhand credentials
- `TRAEFIK_DASHBOARD_AUTH` - Traefik dashboard
- `VULTR_API_KEY` - Vultr token
- `CLOUDFLARE_API_TOKEN` - Cloudflare token
- `AGENT_API_KEY` - AI agent token

## Testing Steps

### Phase 1: Environment Setup

#### 1.1 Update .env File
```bash
# Add deploy environment variables to /Users/jaketownsend/Desktop/Projectsz/CTF-IT/.env
DOMAIN=example.com
CALDERA_ADMIN_PASSWORD=admin
SEMAPHORE_ADMIN_PASSWORD=admin
DOCKHAND_ADMIN_PASSWORD=admin
TRAEFIK_DASHBOARD_AUTH=admin
VULTR_API_KEY=test_token
CLOUDFLARE_API_TOKEN=test_token
AGENT_API_KEY=test_agent_token
```

#### 1.2 Backup Existing Database
```bash
cp /Users/jaketownsend/Desktop/Projectsz/CTF-IT/ctf.db /Users/jaketownsend/Desktop/Projectsz/CTF-IT/ctf.db.backup
```

#### 1.3 Build API Container
```bash
cd /Users/jaketownsend/Desktop/Projectsz/CTF-IT
docker compose build api
```

### Phase 2: Database Migration Test

#### 2.1 Start API Container
```bash
docker compose up -d api
```

#### 2.2 Verify Table Creation
```bash
docker compose exec api sqlite3 /app/data/ctf.db ".schema service_credentials"
```

**Expected Output:**
```
CREATE TABLE service_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name VARCHAR(64) NOT NULL,
    credential_type VARCHAR(32) NOT NULL,
    username VARCHAR(256),
    password VARCHAR(256) NOT NULL,
    url VARCHAR(512),
    description TEXT,
    created_at DATETIME,
    updated_at DATETIME,
    created_by INTEGER
)
```

#### 2.3 Verify Seed Data
```bash
docker compose exec api sqlite3 /app/data/ctf.db "SELECT service_name, credential_type, username FROM service_credentials"
```

**Expected Output:**
```
Caldera Admin|admin|admin
Semaphore Admin|admin|admin
Dockhand|admin|admin
Traefik Dashboard|admin|admin
Vultr API|token|
Cloudflare|token|test_token
AI Agent|token|test_agent_token
```

### Phase 3: API Endpoint Testing

#### 3.1 Create Admin User
```bash
# First admin registration
curl -X POST http://localhost:8080/auth/register \
  -F "username=admin" \
  -F "password=admin123" \
  -F "event_id=1" \
  -F "admin_bootstrap_token=test-secret-key"
```

#### 3.2 Login and Get Session
```bash
# Login to get session cookie
curl -X POST http://localhost:8080/auth/login \
  -F "username=admin" \
  -F "password=admin123" \
  -c /tmp/cookies.txt \
  -L
```

#### 3.3 Test GET /admin/service-credentials
```bash
# List all credentials
curl -X GET http://localhost:8080/admin/service-credentials \
  -b /tmp/cookies.txt \
  -H "Accept: application/json"
```

**Expected Response:**
```json
[
  {
    "id": 1,
    "service_name": "Caldera Admin",
    "credential_type": "admin",
    "username": "admin",
    "password": "enc:v1:...",
    "url": "https://caldera.example.com",
    "description": "MITRE Caldera C2 admin credentials"
  },
  ...
]
```

#### 3.4 Test Create Credential
```bash
curl -X POST http://localhost:8080/admin/service-credentials \
  -b /tmp/cookies.txt \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "Test Service",
    "credential_type": "user",
    "username": "testuser",
    "password": "testpass123",
    "url": "https://test.example.com",
    "description": "Test credential"
  }'
```

#### 3.5 Test Update Credential
```bash
curl -X PUT http://localhost:8080/admin/service-credentials/1 \
  -b /tmp/cookies.txt \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "Caldera Admin (Updated)",
    "credential_type": "admin",
    "username": "admin",
    "password": "newpassword123",
    "url": "https://caldera.example.com",
    "description": "Updated description"
  }'
```

#### 3.6 Test Delete Credential
```bash
curl -X DELETE http://localhost:8080/admin/service-credentials/999 \
  -b /tmp/cookies.txt
```

### Phase 4: Frontend Testing

#### 4.1 Access Admin Panel
Open browser to: `http://localhost:8080/admin`

#### 4.2 Navigate to Service Credentials
Click on "Service Credentials" section in the admin panel

#### 4.3 Verify UI Elements
- [ ] Credential table displays all services
- [ ] Passwords shown as masked (••••••••••••)
- [ ] "Copy" buttons appear on username and password fields
- [ ] "Open Service" buttons work for URLs
- [ ] "Edit" button functional
- [ ] "Delete" button functional

#### 4.4 Test Copy Functionality
Click copy buttons and verify:
- Username is copied to clipboard
- Password is copied to clipboard
- Toast notification appears: "Copied [field] to clipboard"

#### 4.5 Test Add Credential
1. Click "+ Add New Credential" button
2. Fill form with test data:
   - Service Name: "Test Service"
   - Credential Type: "user"
   - Username: "testuser"
   - Password: "testpass123"
   - URL: "https://test.example.com"
   - Description: "Test credential"
3. Click "Save"
4. Verify new credential appears in table

#### 4.6 Test Edit Credential
1. Click "Edit" on existing credential
2. Modify password to "newpassword123"
3. Click "Save"
4. Verify password is updated in database and UI

#### 4.7 Test Delete Credential
1. Click "Delete" on test credential
2. Confirm in dialog
3. Verify credential is removed from table

### Phase 5: Security Verification

#### 5.1 Verify Encryption in Database
```bash
docker compose exec api sqlite3 /app/data/ctf.db "SELECT service_name, password FROM service_credentials WHERE service_name = 'Caldera Admin'"
```

**Expected:** Password should start with `enc:v1:`

#### 5.2 Verify Decryption in API
```bash
curl -X GET http://localhost:8080/admin/service-credentials \
  -b /tmp/cookies.txt \
  -H "Accept: application/json" | jq '.[0].password' | grep -v '^enc:v1:'
```

**Expected:** Should show plain text password in JSON response

#### 5.3 Verify No Plain Text in Frontend
1. View page source or inspect network traffic
2. Verify password field shows only masked text (••••••••••••)
3. Verify no plain text credentials in HTML

### Phase 6: Seed Data Re-population Test

#### 6.1 Reset Database
```bash
docker compose down
rm -f /Users/jaketownsend/Desktop/Projectsz/CTF-IT/ctf.db
```

#### 6.2 Restart API Container
```bash
docker compose up -d api
```

#### 6.3 Verify Auto-Seeding
```bash
# Wait 5 seconds for startup
sleep 5

# Check credentials were created
docker compose exec api sqlite3 /app/data/ctf.db "SELECT COUNT(*) FROM service_credentials"
```

**Expected:** Should return 7 (default credentials)

### Phase 7: Cleanup

#### 7.1 Stop Containers
```bash
docker compose down
```

#### 7.2 Restore Database (optional)
```bash
cp /Users/jaketownsend/Desktop/Projectsz/CTF-IT/ctf.db.backup /Users/jaketownsend/Desktop/Projectsz/CTF-IT/ctf.db
```

## Expected Test Results

✅ **Database Migration**: `service_credentials` table created successfully
✅ **Seed Data**: 7 default credentials auto-populated from environment
✅ **API Endpoints**: All CRUD operations functional
✅ **Encryption**: Passwords encrypted in database, decrypted in API responses
✅ **Frontend UI**: Credentials displayed with masked passwords
✅ **Copy Functionality**: Username and password copy to clipboard
✅ **Edit/Delete**: Full CRUD operations work correctly
✅ **Auto-Seeding**: Credentials re-populate on fresh database

## Common Issues and Solutions

### Issue: Table not created
**Solution:** Check API logs: `docker compose logs api` and verify `init_db()` runs

### Issue: No seed data created
**Solution:** Verify environment variables in `.env` file are set

### Issue: API returns 403 Forbidden
**Solution:** Ensure admin user exists and session cookie is valid

### Issue: Frontend not loading
**Solution:** Check container health: `docker compose ps api` and verify database file exists

### Issue: Encryption error
**Solution:** Verify `DATA_ENCRYPTION_KEY` is set in `.env` file

## Success Criteria

All of the following must pass:
1. ✅ `service_credentials` table exists in database
2. ✅ 7 default credentials populated from environment
3. ✅ API endpoints return encrypted passwords correctly
4. ✅ Frontend displays credentials with masked passwords
5. ✅ Copy-to-clipboard buttons work for username and password
6. ✅ Edit and delete operations function correctly
7. ✅ Passwords remain encrypted in database
8. ✅ Seed data re-populates on fresh database creation
