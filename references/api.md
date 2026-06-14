# X-to-3D API Contract

The client reads the versioned API root from `X_TO_3D_API_BASE_URL`. A configured value should resemble `https://service.example/api/v1`, without a trailing slash.

## Authentication

### Create anonymous session

```http
POST /auth/anonymous
Content-Type: application/json
```

Request:

```json
{
  "installation_id": "client-generated UUID"
}
```

Response:

```json
{
  "access_token": "secret",
  "refresh_token": "secret",
  "expires_at": 1781400000
}
```

### Refresh session

```http
POST /auth/refresh
Content-Type: application/json
```

Request:

```json
{
  "refresh_token": "secret"
}
```

Return the same session shape as anonymous authentication. Token values are process-only secrets and must never appear in Agent-facing output.

All remaining endpoints require:

```http
Authorization: Bearer <access_token>
```

## Account

```http
GET /account
```

Response fields may include `remaining_bytes` and `active_job`.

## Create Job

```http
POST /jobs
Content-Type: multipart/form-data
```

Parts:

- `file`: binary input.
- `output_format`: requested extension.

Response:

```json
{
  "job_id": "uuid",
  "status": "queued",
  "requested_bytes": 1234,
  "remaining_bytes": 52427566
}
```

## Read Job

```http
GET /jobs/{job_id}
```

Expected status values are `queued`, `processing`, `done`, `failed`, `cancelled`, and `expired`.

## Cancel Job

```http
POST /jobs/{job_id}/cancel
Content-Type: application/json
```

Only queued jobs are cancellable in the first version.

## Get Result

```http
GET /jobs/{job_id}/result
```

Response:

```json
{
  "file_name": "output.png",
  "content_type": "image/png",
  "size_bytes": 4567,
  "download_url": "short-lived private URL",
  "expires_at": "2026-06-14T12:00:00Z"
}
```

The client consumes `download_url` internally and omits it from stdout.

## Errors

Non-success responses should use:

```json
{
  "error": {
    "code": "STABLE_CODE",
    "message": "Safe user-facing message",
    "retry_after_seconds": 30
  }
}
```

Stable codes include `AUTH_REQUIRED`, `SESSION_EXPIRED`, `ACCOUNT_DISABLED`, `RATE_LIMITED`, `UNSUPPORTED_INPUT_FORMAT`, `UNSUPPORTED_OUTPUT_FORMAT`, `FILE_TOO_LARGE`, `INSUFFICIENT_QUOTA`, `ACTIVE_JOB_EXISTS`, `JOB_NOT_FOUND`, `JOB_NOT_CANCELLABLE`, `RESULT_NOT_READY`, `RESULT_EXPIRED`, `QUEUE_UNAVAILABLE`, and `INTERNAL_ERROR`.
