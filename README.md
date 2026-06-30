# Invoice AI — Backend

AI-powered invoice processing backend. Upload a PDF or photo of an invoice — the system extracts every field (vendor, amounts, line items, dates), categorizes the expense, detects duplicates, flags anomalies, and generates a monthly AI narrative summary.

Built with FastAPI, Celery, PostgreSQL, Redis, and OpenAI GPT-4o (with Vision for images).

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Setup & Run (5 Steps)](#setup--run-5-steps)
5. [Environment Variables](#environment-variables)
6. [Testing with Postman](#testing-with-postman)
7. [API Reference](#api-reference)
8. [Project Structure](#project-structure)
9. [Frontend Integration Guide](#frontend-integration-guide)
10. [Development](#development)
11. [Deployment Checklist](#deployment-checklist)
12. [Troubleshooting](#troubleshooting)

---

## What It Does

| Feature | How |
|---|---|
| **Invoice Extraction** | Upload PDF or image → OCR + GPT-4o Vision reads every field (vendor, invoice #, dates, subtotal, tax, total, all line items) |
| **Expense Categorization** | AI assigns one of 7 categories: utilities, marketing, travel, office supplies, equipment, software, other |
| **Duplicate Detection** | Composite similarity scoring on invoice number, vendor name, and amount |
| **Anomaly Detection** | Z-score + AI analysis flags invoices that are statistical outliers |
| **Monthly Summary** | AI-generated narrative with spending insights and recommendations |
| **Async Processing** | Upload returns instantly (202); Celery worker processes in background |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Next.js Frontend  (separate repo)                           │
└────────────────────────┬─────────────────────────────────────┘
                         │  REST  (X-User-Id header)
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  FastAPI  /api/v1/*          (port 8000)                     │
│                                                              │
│  POST /invoices/upload  ──► Celery task dispatched (Redis)   │
│  GET  /invoices/{id}    ──► DB read                          │
│  GET  /invoices/summary/monthly ──► AI narrative             │
└──────┬───────────────────────────────────────────────────────┘
       │                          │
       ▼                          ▼
┌─────────────┐          ┌─────────────────────────────────────┐
│  PostgreSQL │          │  Celery Worker                      │
│  (port 5432)│◄─────────│  OCR → GPT-4o Vision → Categorize  │
└─────────────┘          │  → Duplicate check → Anomaly check  │
                         └────────────────┬────────────────────┘
┌─────────────┐                          │
│  Redis      │◄─────── task queue ───────┘
│  (port 6379)│
└─────────────┘
```

**5 Docker services:**

| Service | Port | Purpose |
|---|---|---|
| `api` | 8000 | FastAPI application |
| `worker` | — | Celery invoice processing worker |
| `flower` | 5555 | Celery monitoring dashboard |
| `postgres` | 5432 | Primary database |
| `redis` | 6379 | Task queue + result backend |

---

## Prerequisites

Install these before starting:

| Tool | Version | Download |
|---|---|---|
| **Docker Desktop** | Latest | https://www.docker.com/products/docker-desktop |
| **Git** | Any | https://git-scm.com/downloads |
| **Postman** | Any | https://www.postman.com/downloads (for testing) |

> **OpenAI API Key required.** Get one at https://platform.openai.com/api-keys
> The account needs access to `gpt-4o` (Vision enabled).

---

## Setup & Run (5 Steps)

### Step 1 — Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/invoice-ai-backend.git
cd invoice-ai-backend
```

### Step 2 — Create your environment file

```bash
cp docker/.env.example docker/.env
```

Open `docker/.env` in any text editor and fill in:

```env
OPENAI_API_KEY=your_openai_api_key
SECRET_KEY=any-random-string-at-least-32-characters-long
```

> **Important:** Never commit `docker/.env` — it is already in `.gitignore`.

### Step 3 — Build and start all services

```bash
cd docker
docker-compose up --build -d
```

First run downloads ~1 GB of images and installs dependencies. Takes 3–5 minutes.
Subsequent starts take ~10 seconds.

### Step 4 — Run database migrations

```bash
docker-compose exec api alembic upgrade head
```

You should see:
```
INFO  [alembic.runtime.migration] Running upgrade ... -> ..., initial schema
```

### Step 5 — Verify everything is running

```bash
docker-compose ps
```

Expected output:
```
NAME              STATUS
docker-api-1      Up (healthy)
docker-worker-1   Up (healthy)
docker-postgres-1 Up (healthy)
docker-redis-1    Up (healthy)
docker-flower-1   Up (restarting is normal — monitoring only)
```

Test the health endpoint:

```
GET http://localhost:8000/api/v1/health
→ { "status": "ok" }

GET http://localhost:8000/api/v1/health/ready
→ { "status": "ready" }
```

**API is live at:** `http://localhost:8000`
**Swagger UI at:** `http://localhost:8000/docs`
**Celery monitor at:** `http://localhost:5555`

---

## Environment Variables

All variables go in `docker/.env`. Copy from `docker/.env.example`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | **Yes** | — | OpenAI secret key (needs gpt-4o access) |
| `SECRET_KEY` | **Yes** | — | JWT signing secret (min 32 chars) |
| `DATABASE_URL` | No | postgres in docker | PostgreSQL async DSN |
| `REDIS_URL` | No | redis in docker | Redis connection URL |
| `OPENAI_MODEL` | No | `gpt-4o` | Model to use for extraction |
| `OCR_PROVIDER` | No | `tesseract` | OCR backend (`tesseract` only for now) |
| `MAX_UPLOAD_SIZE_MB` | No | `20` | Max file size in MB |
| `APP_ENV` | No | `development` | `development` or `production` |

---

## Testing with Postman

All requests require this header:

```
X-User-Id: 550e8400-e29b-41d4-a716-446655440000
```

> This is a UUID representing the user. Use any valid UUID — the backend auto-creates the user row if it doesn't exist.

---

### 1. Upload an Invoice

**Request:**
```
POST http://localhost:8000/api/v1/invoices/upload
Header: X-User-Id: 550e8400-e29b-41d4-a716-446655440000
Body: form-data → key: file, value: [select a JPEG, PNG, or PDF invoice file]
```

**Response (202 Accepted):**
```json
{
  "invoice_id": "fa437e93-8954-4b51-b51d-385214eae241",
  "filename": "invoice.jpeg",
  "status": "pending"
}
```

Save the `invoice_id` — you will use it in all follow-up requests.

Processing takes 10–30 seconds. The worker does: OCR → GPT-4o Vision → categorize → duplicate check → anomaly check.

---

### 2. Check Processing Status (poll this until `status = "processed"`)

**Request:**
```
GET http://localhost:8000/api/v1/invoices/fa437e93-8954-4b51-b51d-385214eae241
Header: X-User-Id: 550e8400-e29b-41d4-a716-446655440000
```

**Response when processed:**
```json
{
  "id": "fa437e93-8954-4b51-b51d-385214eae241",
  "filename": "invoice.jpeg",
  "status": "processed",
  "vendor_name": "X Construction",
  "invoice_number": "#CON-2028-001",
  "invoice_date": "2028-09-21",
  "due_date": null,
  "currency": "USD",
  "subtotal": "26000.0000",
  "tax_amount": "2600.0000",
  "total_amount": "28600.0000",
  "category": "equipment",
  "confidence": 0.95,
  "is_anomaly": false,
  "anomaly_reason": null,
  "duplicate_of": null,
  "line_items": [
    { "description": "Foundation Work", "quantity": 10.0, "unit_price": "100", "total": "1000" },
    { "description": "Steel Structure Installation", "quantity": 5.0, "unit_price": "2000", "total": "10000" },
    { "description": "Concrete Material", "quantity": 200.0, "unit_price": "50", "total": "10000" },
    { "description": "Structural Steel Material", "quantity": 10.0, "unit_price": "500", "total": "5000" }
  ],
  "created_at": "2026-06-17T12:52:30.351452Z",
  "updated_at": "2026-06-17T12:52:33.663893Z"
}
```

**Possible status values:**

| Status | Meaning |
|---|---|
| `pending` | Queued, worker hasn't started yet |
| `processing` | OCR + AI extraction in progress |
| `processed` | Done — all fields populated |
| `failed` | Processing error — check worker logs |
| `duplicate` | Duplicate of another invoice |

---

### 3. List All Invoices (paginated)

**Request:**
```
GET http://localhost:8000/api/v1/invoices
Header: X-User-Id: 550e8400-e29b-41d4-a716-446655440000
```

**With filters:**
```
GET http://localhost:8000/api/v1/invoices?status=processed&category=equipment&limit=10&offset=0
```

**Response:**
```json
{
  "invoices": [ ... ],
  "meta": {
    "total": 42,
    "limit": 10,
    "offset": 0,
    "has_more": true
  }
}
```

**Query parameters:**

| Param | Values | Description |
|---|---|---|
| `status` | `pending`, `processing`, `processed`, `failed`, `duplicate` | Filter by status |
| `category` | `utilities`, `marketing`, `travel`, `office_supplies`, `equipment`, `software`, `other` | Filter by category |
| `limit` | 1–200 (default 50) | Page size |
| `offset` | 0+ (default 0) | Skip N records |

---

### 4. Duplicate Detection

Upload the same invoice file twice, then call:

**Request:**
```
GET http://localhost:8000/api/v1/invoices/SECOND_INVOICE_ID/duplicates
Header: X-User-Id: 550e8400-e29b-41d4-a716-446655440000
```

**Response:**
```json
{
  "invoice_id": "...",
  "has_duplicates": true,
  "matches": [
    {
      "candidate_id": "FIRST_INVOICE_ID",
      "invoice_number_match": true,
      "vendor_match": true,
      "amount_match": true,
      "similarity_score": 1.0,
      "is_duplicate": true
    }
  ]
}
```

---

### 5. Anomaly Detection

**Request:**
```
GET http://localhost:8000/api/v1/invoices/INVOICE_ID/anomaly
Header: X-User-Id: 550e8400-e29b-41d4-a716-446655440000
```

**Response:**
```json
{
  "invoice_id": "...",
  "is_anomaly": false,
  "zscore": null,
  "reason": null
}
```

---

### 6. Monthly AI Summary

**Request:**
```
GET http://localhost:8000/api/v1/invoices/summary/monthly?year=2028&month=9
Header: X-User-Id: 550e8400-e29b-41d4-a716-446655440000
```

**Response:**
```json
{
  "year": 2028,
  "month": 9,
  "total_spending": "28600.0000",
  "currency": "USD",
  "invoice_count": 1,
  "category_breakdown": [
    {
      "category": "equipment",
      "amount": "28600.0000",
      "percentage": 100.0,
      "count": 1
    }
  ],
  "top_vendors": [
    { "vendor": "X Construction", "total": "28600.0000", "count": 1 }
  ],
  "ai_narrative": "In September 2028, total spending reached $28,600 driven entirely by equipment expenses from X Construction..."
}
```

---

## API Reference

Base URL: `http://localhost:8000/api/v1`

Interactive docs: `http://localhost:8000/docs`

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/health/ready` | Readiness probe (pings DB) |
| `POST` | `/invoices/upload` | Upload invoice → 202 Accepted |
| `POST` | `/invoices/{id}/process` | Force-process synchronously (dev only) |
| `GET` | `/invoices/{id}` | Get single invoice |
| `GET` | `/invoices` | List invoices (paginated, filterable) |
| `GET` | `/invoices/{id}/duplicates` | Duplicate detection result |
| `GET` | `/invoices/{id}/anomaly` | Anomaly report |
| `GET` | `/invoices/summary/monthly` | Monthly AI summary (`?year=&month=`) |

### Error Response Format

All errors return the same envelope:

```json
{
  "success": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "Invoice abc123 not found",
    "detail": null
  }
}
```

| Status | Code | When |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Bad request body or query params |
| 401 | `UNAUTHORIZED` | Missing or invalid `X-User-Id` header |
| 404 | `NOT_FOUND` | Invoice doesn't exist for this user |
| 413 | `FILE_TOO_LARGE` | Upload exceeds 20 MB |
| 415 | `UNSUPPORTED_FILE_TYPE` | Only PDF, JPEG, PNG, TIFF allowed |
| 422 | `EXTRACTION_FAILED` | AI/OCR could not extract data |
| 500 | `INTERNAL_ERROR` | Unexpected server error |

### Amounts are decimal strings

All monetary amounts are returned as strings to avoid JavaScript float precision loss:

```js
// ✅ Correct
import Decimal from 'decimal.js'
const total = new Decimal(invoice.total_amount)

// ❌ Wrong — loses precision on large amounts
const total = parseFloat(invoice.total_amount)
```

---

## Project Structure

```
invoice-ai/
├── app/
│   ├── api/
│   │   └── v1/
│   │       └── endpoints/
│   │           ├── health.py       # Health + readiness probes
│   │           └── invoices.py     # All invoice endpoints
│   ├── core/
│   │   ├── dependencies.py         # FastAPI DI: DB session, current user, service
│   │   ├── settings.py             # All config via pydantic-settings + .env
│   │   ├── middleware.py           # Request ID, CORS
│   │   └── exception_handlers.py  # Typed error → HTTP response mapping
│   ├── domain/
│   │   ├── entities/               # Pure Python dataclasses (no ORM/Pydantic)
│   │   ├── enums/                  # InvoiceStatus, ExpenseCategory, Currency
│   │   └── exceptions/            # Typed domain exceptions
│   ├── infrastructure/
│   │   ├── ai/                     # OpenAI client + prompts (GPT-4o Vision)
│   │   ├── ocr/                    # Tesseract OCR with image preprocessing
│   │   └── persistence/
│   │       ├── models/             # SQLAlchemy ORM models
│   │       └── repositories/      # All DB queries (no SQL in services)
│   ├── schemas/                    # Pydantic request/response models (API contract)
│   └── services/
│       ├── invoice_service.py      # Business logic orchestration
│       └── celery_tasks.py         # Async Celery task definitions
├── alembic/                        # Database migrations
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── .env                        # Your secrets (gitignored, never committed)
│   └── .env.example                # Template — copy this to .env
├── tests/                          # pytest test suite
├── pyproject.toml                  # Dependencies + tool config
└── README.md
```

---

## Frontend Integration Guide

### Authentication flow

Every request to this backend **must** include:

```http
X-User-Id: <user-uuid>
```

**This header must be set server-side**, never from the browser:

```
Browser → Next.js API route (validates JWT, extracts user UUID, adds header) → This backend
```

```typescript
// Next.js API route — server/app/api/invoices/route.ts
const response = await fetch(`${process.env.BACKEND_URL}/api/v1/invoices/upload`, {
  method: 'POST',
  headers: {
    'X-User-Id': session.user.id,  // from your auth (NextAuth, Clerk, etc.)
  },
  body: formData,
})
```

### Upload + polling pattern

```typescript
// 1. Upload
const uploadRes = await fetch('/api/v1/invoices/upload', {
  method: 'POST',
  headers: { 'X-User-Id': userId },
  body: formData,
})
const { invoice_id } = await uploadRes.json()  // status: "pending"

// 2. Poll until done
let invoice
do {
  await new Promise(r => setTimeout(r, 2000))  // wait 2 seconds
  const res = await fetch(`/api/v1/invoices/${invoice_id}`, {
    headers: { 'X-User-Id': userId },
  })
  invoice = await res.json()
} while (invoice.status === 'pending' || invoice.status === 'processing')

// 3. invoice.status is now "processed" or "failed"
```

### Accepted file types

```
image/jpeg  image/png  image/tiff  application/pdf
Max size: 20 MB
```

---

## Development

### Running tests

```bash
# Inside the api container
docker-compose exec api poetry run pytest

# With HTML coverage report
docker-compose exec api poetry run pytest --cov=app --cov-report=html
```

### Rebuilding after code changes

```bash
cd docker
docker-compose build --no-cache api worker
docker-compose stop api worker
docker-compose up -d api worker
```

### Viewing logs

```bash
# All services
docker-compose logs -f

# Worker only (most useful for debugging processing)
docker-compose logs -f worker

# API only
docker-compose logs -f api
```

### Running a new migration

```bash
# Generate migration from model changes
docker-compose exec api alembic revision --autogenerate -m "add_some_column"

# Apply
docker-compose exec api alembic upgrade head
```

### Adding a new OCR provider

1. Create `class MyProvider(AbstractOCRProvider)` in `app/infrastructure/ocr/__init__.py`
2. Implement `async def extract_text(self, file_path: Path) -> str`
3. Add a `case` in `OCRProviderFactory.get_provider()`
4. Set `OCR_PROVIDER=my_provider` in `docker/.env`

No other changes needed — the service layer depends only on the abstract interface.

---

## Deployment Checklist

Before going to production:

- [ ] Set `APP_ENV=production` in environment (disables Swagger UI)
- [ ] Use a secret manager for `OPENAI_API_KEY` and `SECRET_KEY` (never .env in prod)
- [ ] Set `DATABASE_URL` to a connection pooler (PgBouncer or RDS Proxy)
- [ ] Replace local file storage with S3 (`STORAGE_BACKEND=s3`)
- [ ] Switch OCR to AWS Textract for better accuracy on noisy/handwritten invoices
- [ ] Change Celery pool from `--pool=threads` to `--pool=gevent` for better I/O scaling
- [ ] Set up Grafana + Prometheus for monitoring (or use Flower in managed mode)
- [ ] Add JWT signature verification in `app/core/dependencies.py` `get_current_user_id()`
- [ ] Set up database backups (RDS snapshots or `pg_dump` cron)
- [ ] Configure `ALLOWED_ORIGINS` to your production domain only

---

## Troubleshooting

### Containers won't start

```bash
docker-compose logs postgres   # check DB startup
docker-compose logs api        # check API startup errors
```

### Worker stays "pending" — invoices never process

```bash
docker-compose logs worker
```

Common causes:
- `OPENAI_API_KEY` wrong or expired → Check key at platform.openai.com
- Worker not connected to Redis → Check `CELERY_BROKER_URL` in `.env`

### 500 on upload

Usually a missing DB migration:
```bash
docker-compose exec api alembic upgrade head
```

### OpenAI API key not picked up

If you have `OPENAI_API_KEY` set as a Windows/Mac system environment variable,
it may override your `docker/.env` file. Remove the system variable:

**Windows PowerShell:**
```powershell
[System.Environment]::SetEnvironmentVariable('OPENAI_API_KEY', $null, 'User')
[System.Environment]::SetEnvironmentVariable('OPENAI_API_KEY', $null, 'Machine')
```

**Mac/Linux:**
```bash
unset OPENAI_API_KEY
# and remove it from ~/.bashrc or ~/.zshrc
```

### Reset everything (nuclear option)

```bash
cd docker
docker-compose down -v   # stops containers AND deletes all data volumes
docker-compose up --build -d
docker-compose exec api alembic upgrade head
```

---

## License

MIT
