# SellerBottel

Telegram digital-product marketplace with an admin dashboard, per-product inventory, encrypted inventory records, deposits, discounts, and Telegram delivery.

## Current Fix: Product + Inventory Upload / Schema

This version fixes the inventory upload flow and documents the inventory contract.

### Root cause addressed

The upload button previously had a client-side state dependency that could prevent the upload handler from running. That was fixed in the frontend.

After the request reached the backend, the remaining HTTP 400 was caused by the inventory parser enforcing the product's existing schema with an opaque error. The backend now:

1. Accepts XLSX, CSV, and TXT inventory uploads.
2. Uses the first row of XLSX/CSV as the file header/schema.
3. If a digital product has no schema yet, a successful validation establishes that file header as the product schema.
4. If a product already has a schema, the uploaded file must contain the same field names.
5. Column order is allowed to differ.
6. Field comparison ignores surrounding whitespace and letter case.
7. Records are remapped to the canonical product schema before validation/import.
8. A schema mismatch returns a useful 400 message containing both the required schema and the received file headers.
9. The Products page displays the required inventory schema for every product.
10. The Manage Inventory page displays the same required schema before upload.
11. Product/service separation remains enforced: digital products use inventory; service products do not.

FastAPI file uploads use multipart/form-data with File/UploadFile. This project includes python-multipart in backend requirements.

---

## Product Types

### A. Produk Digital / Data

Digital products use per-product inventory.

- Inventory upload: enabled.
- Stock mode:
  - auto: stock follows available inventory.
  - manual: stock is capped by the configured manual stock and cannot exceed real available inventory.
- Inventory schema belongs to the product.
- Bulk upload is handled from the Product page and Manage Inventory page.
- Manual single-item input is handled from Manage Inventory.

### B. Produk Jasa

Service products do not use inventory.

- Inventory upload: disabled.
- Stock: Unlimited.
- Delivery can be:
  - Link
  - License
  - File

---

## Inventory Schema Contract

A product schema is a list of field names, for example:

```text
email · password · recovery_email · 2fa
```

An XLSX/CSV file should therefore have:

```text
email | password | recovery_email | 2fa
```

The actual separator depends on the file format.

### XLSX / CSV

- Row 1 is the header.
- Remaining rows are inventory records.
- Header names must match the product schema.
- Column order may differ.
- Case and surrounding whitespace are ignored.
- Different field names are not automatically guessed or translated.

Example:

Product schema:

```text
email · password · recovery_email · 2fa
```

This is accepted:

```text
2FA | EMAIL | Password | Recovery_Email
```

because field comparison is case-insensitive and order-independent.

This is rejected:

```text
email | kata sandi | recovery | 2fa
```

if the product schema is:

```text
email · password · recovery_email · 2fa
```

because kata sandi and recovery are different field names. The backend reports the expected and received headers in the 400 response.

### TXT

TXT supports pipe-separated fields:

```text
email|password|recovery_email|2fa
```

For a single-field schema, each non-empty line can be treated as one value.

---

## Schema Lifecycle

### New digital product

A newly created digital product may have no schema yet.

Flow:

1. Create the digital product.
2. Open Products → Input Data or Manage Inventory.
3. Upload an XLSX/CSV/TXT file.
4. Validate.
5. If the file is valid, its schema becomes the product's schema.
6. Import the validated inventory.
7. Future uploads must follow that schema.

The schema is shown on the Products page and Manage Inventory page.

### Existing digital product

If a product already has a schema:

1. Select the product.
2. Read the displayed required schema.
3. Prepare the file using those field names.
4. Validate.
5. Import.

Do not silently change field names between uploads.

---

## Admin Pages

### Products

The Products page manages ready-for-sale products.

Features:

- Product name and description.
- Product type.
- USD/IDR price.
- Active/inactive status.
- Stock mode for digital products.
- Unlimited status for services.
- Inventory schema column.
- Input Data / Inventory action for digital products.
- Excel product import.

The inventory schema is displayed directly in the product table.

### Manage Inventory

The Manage Inventory page is the dedicated inventory management area.

Features:

- Select an existing digital product.
- Show Available / Reserved / Sold counts.
- Show required product schema.
- Bulk XLSX/CSV/TXT upload.
- Validate upload before import.
- Import bulk inventory.
- Manual single-item input using the product schema.
- Filter inventory by status.
- Delete available inventory records.
- Sold inventory credentials are not exposed in the panel.

The page does not create products. Product selection always comes from the Products page data.

---

## Backend Inventory API

Base prefix:

```text
/api/admin
```

### List products

```http
GET /api/admin/products
```

Digital products include:

```json
{
  "product_kind": "digital",
  "inventory_enabled": true,
  "inventory_schema": ["email", "password", "recovery_email", "2fa"],
  "inventory_stock": 10,
  "stock_mode": "auto"
}
```

Service products include:

```json
{
  "product_kind": "service",
  "inventory_enabled": false,
  "stock": null,
  "stock_mode": "unlimited"
}
```

### Inventory list

```http
GET /api/admin/products/{pid}/inventory?status=available
```

### Validate upload

```http
POST /api/admin/products/{pid}/inventory/validate
Content-Type: multipart/form-data
```

Form fields:

- content: optional text input.
- file: XLSX, CSV, or TXT.

Successful response:

```json
{
  "schema": ["email", "password", "recovery_email", "2fa"],
  "valid_count": 10,
  "duplicate_count": 2,
  "preview": []
}
```

### Import upload

```http
POST /api/admin/products/{pid}/inventory/import
Content-Type: multipart/form-data
```

### Manual inventory

```http
POST /api/admin/products/{pid}/inventory/manual
Content-Type: application/json
```

Body:

```json
{
  "data": {
    "email": "example@example.com",
    "password": "example",
    "recovery_email": "recovery@example.com",
    "2fa": "123456"
  }
}
```

### Delete available inventory

```http
DELETE /api/admin/products/{pid}/inventory/{item_id}
```

Only available inventory can be deleted.

---

## Inventory Encryption

Inventory records are encrypted before being stored.

Implementation:

- backend/inventory.py
- Fernet encryption.
- SHA-256 fingerprint for duplicate detection.
- MongoDB stores encrypted inventory in inventory_items.

The encryption key is controlled by:

```text
INVENTORY_ENCRYPTION_KEY
```

Never replace this key after inventory data has been stored.

Generate a new key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Project Structure

```text
sellerbottel/
├── backend/
│   ├── .env
│   ├── .env.example
│   ├── server.py
│   ├── admin_routes.py
│   ├── admin_user_routes.py
│   ├── inventory.py
│   ├── auth.py
│   ├── db.py
│   ├── error_handlers.py
│   ├── bot.py
│   ├── join_gate.py
│   ├── tgapi.py
│   ├── services.py
│   ├── storage.py
│   ├── requirements.txt
│   └── ...
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Products.jsx
│   │   │   ├── Inventory.jsx
│   │   │   └── Users.jsx
│   │   ├── lib/
│   │   │   └── api.js
│   │   └── ...
│   ├── public/
│   ├── build/
│   ├── package.json
│   └── craco.config.js
│
└── README.md
```

---

## Important Inventory Code Responsibilities

### backend/admin_routes.py

Responsible for:

- Product CRUD.
- Product type normalization.
- Product stock mode.
- Product list data.
- Inventory upload endpoints.
- XLSX/CSV/TXT parsing.
- Inventory schema comparison.
- Inventory schema establishment.
- Admin inventory listing.

### backend/inventory.py

Responsible for:

- Inventory normalization.
- Fingerprinting.
- Duplicate detection.
- Encryption.
- Database insertion.
- Available stock count.
- Reservation/release/sold transitions.
- Decryption for admin display where allowed.

### backend/error_handlers.py

Provides JSON responses for inventory errors:

```json
{
  "detail": "..."
}
```

This prevents the frontend from hiding the actual inventory error behind a generic message.

### frontend/src/pages/Products.jsx

Responsible for:

- Product management UI.
- Product type selection.
- Product stock UI.
- Product schema display.
- Product-level inventory upload.

### frontend/src/pages/Inventory.jsx

Responsible for:

- Product selection.
- Required schema display.
- Bulk inventory upload.
- Validation/import actions.
- Manual inventory entry.
- Inventory table.

### frontend/src/lib/api.js

Creates the Axios API client:

```text
\${REACT_APP_BACKEND_URL}/api
```

and formats backend error responses.

---

## Required Backend Environment

Create:

```text
/opt/sellerbottel/backend/.env
```

Minimum required configuration:

```env
TELEGRAM_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
WEBHOOK_SECRET=
PUBLIC_BASE_URL=https://your-domain.example
CORS_ORIGINS=https://your-domain.example
COOKIE_SAMESITE=lax
TRUST_PROXY=true

JWT_SECRET=
ADMIN_EMAIL=
ADMIN_PASSWORD=
ADMIN_TELEGRAM_ID=

MONGO_URL=mongodb://localhost:27017
DB_NAME=sellerbottel

INVENTORY_ENCRYPTION_KEY=

LOCAL_STORAGE_DIR=/opt/sellerbottel/backend/storage_data
```

### Optional GoPay / QRIS

Only required when the GoPay/QRIS integration is enabled:

```env
GOPAY_ENABLED=false
GOPAY_LOGIN_METHOD=email_otp
GOPAY_EMAIL=
GOPAY_PASSWORD=
GOPAY_PHONE=
GOPAY_QRIS_STRING=
GOPAY_POLL_INTERVAL=15
```

Do not commit the real .env file to Git.

---

## Frontend Environment

The production frontend reads its backend URL at build time.

Recommended:

```env
REACT_APP_BACKEND_URL=https://your-domain.example
```

For a same-domain deployment, the API is exposed under:

```text
https://your-domain.example/api
```

After changing frontend environment variables, rebuild the frontend.

---

## Production Deployment

### Backend

The current deployment uses a Python virtual environment:

```text
/opt/sellerbottel/backend/venv
```

Backend service:

```text
sellerbottel.service
```

Typical service commands:

```bash
systemctl restart sellerbottel
systemctl status sellerbottel --no-pager
```

Logs:

```bash
journalctl -u sellerbottel -f --no-pager
```

### Frontend

Build:

```bash
cd /opt/sellerbottel/frontend
npm run build
```

The production build is:

```text
frontend/build/
```

Nginx serves that directory.

Reload:

```bash
nginx -t && systemctl reload nginx
```

---

## Nginx Production Layout

Expected:

```text
server_name your-domain.example;
root /opt/sellerbottel/frontend/build;

location /api/ {
    proxy_pass http://127.0.0.1:8000;
}

location / {
    try_files $uri /index.html;
}
```

Do not expose backend/.env through Nginx.

---

## Verification Checklist

### 1. Backend

```bash
systemctl is-active sellerbottel
```

Expected:

```text
active
```

### 2. Frontend build

```bash
cd /opt/sellerbottel/frontend
npm run build
```

Expected:

```text
Compiled successfully
```

### 3. Nginx

```bash
nginx -t
```

Expected:

```text
syntax is ok
test is successful
```

### 4. Login

Open the admin dashboard and verify:

- GET /api/auth/me
- GET /api/admin/products

return 200.

### 5. Product schema

For every digital product:

- Schema column is visible.
- Existing schema is displayed.
- Empty schema explicitly says it is not yet established.

For service products:

- Inventory is disabled.
- Stock is Unlimited.

### 6. Inventory validation

Select a digital product and upload a valid file.

Expected backend log:

```text
POST /api/admin/products/<pid>/inventory/validate ... 200 OK
```

### 7. Inventory import

After successful validation:

```text
POST /api/admin/products/<pid>/inventory/import ... 200 OK
```

### 8. Stock

After import:

- Available inventory increases.
- Product stock in auto mode follows available inventory.
- Manual mode never exceeds real available inventory.

---

## Troubleshooting

### HTTP 400: schema mismatch

The backend now returns:

```text
Schema wajib: [...]
Header file: [...]
```

Make the file headers match the displayed product schema.

Column order does not matter.

### HTTP 400: invalid XLSX

The parser first uses openpyxl.

If that fails because an XLSX contains malformed/unsupported style metadata, the backend uses a direct XLSX XML fallback.

If both fail, the file itself is not a readable XLSX.

### HTTP 503: encryption configuration

Check that the variable exists without printing its secret value:

```bash
grep '^INVENTORY_ENCRYPTION_KEY=' /opt/sellerbottel/backend/.env
```

Then:

```bash
systemctl restart sellerbottel
journalctl -u sellerbottel -n 100 --no-pager
```

### Upload button produces no POST

Check:

```text
Browser → Network → inventory/validate
```

The request must reach:

```text
POST /api/admin/products/<pid>/inventory/validate
```

If there is no POST, inspect the frontend build/cache/event layer.

If a POST exists but returns 400, inspect the JSON detail; the backend now reports the expected and received schema.

### Backend only shows GET requests

The browser did not submit the upload request. This is a frontend/client issue, not an inventory parser issue.

---

## Data Safety

Inventory contains potentially sensitive account credentials.

Rules:

1. Never commit .env.
2. Never expose INVENTORY_ENCRYPTION_KEY.
3. Never paste raw inventory credentials into public logs.
4. Do not replace the encryption key while encrypted inventory exists.
5. Sold inventory is intentionally hidden from the admin table.
6. Use HTTPS for the admin dashboard.
7. Keep MongoDB inaccessible from the public internet unless explicitly secured.

---

## Change Summary

This fix changes the inventory contract without changing the product/business model:

- Digital products = inventory-backed.
- Service products = unlimited.
- Product schema is persistent.
- First successful inventory validation can establish an empty schema.
- Existing schema is enforced.
- Header order is flexible.
- Header case/whitespace is flexible.
- Schema mismatch errors are explicit.
- Products page shows schema.
- Manage Inventory shows schema.
- Bulk inventory remains available.
- Manual inventory remains available only through Manage Inventory.
- XLSX fallback remains supported.
- Inventory remains encrypted.

## Final Acceptance Criteria

The inventory feature is considered operational when all of these are true:

- [ ] Every digital product displays its inventory schema.
- [ ] Every service product clearly shows that inventory is not applicable.
- [ ] New digital products can establish a schema through their first valid inventory file.
- [ ] Existing products reject incompatible field names with a readable error.
- [ ] Existing products accept the same schema in a different column order.
- [ ] XLSX upload validates successfully.
- [ ] CSV upload validates successfully.
- [ ] TXT upload validates according to the product schema.
- [ ] Bulk import writes encrypted inventory records.
- [ ] Duplicate records are skipped.
- [ ] Stock follows available inventory in auto mode.
- [ ] Manual stock cannot exceed available inventory.
- [ ] Product page and Manage Inventory use the same schema contract.
- [ ] Backend logs show 200 for successful validation/import.
- [ ] INVENTORY_ENCRYPTION_KEY is configured and stable.
