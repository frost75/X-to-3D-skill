---
name: x-to-3d
description: Convert local 2D images and videos into naked-eye or VR-style 3D output through the X-to-3D hosted service. Use when a user asks to turn a local jpg, jpeg, png, webp, mp4, mov, webm, mkv, or avi file into a 3D image or video, check conversion status or quota, cancel a queued conversion, or download a completed result.
---

# X to 3D

Use the bundled client to submit local media to the hosted X-to-3D service. Keep authentication credentials and signed result URLs out of the conversation.

## Requirements

- Require Python 3.9 or newer.
- Read the API base URL from `X_TO_3D_API_BASE_URL`.
- Treat the service as unavailable when that variable is missing. Explain that the service operator must provide its public API URL; do not invent one.
- Use `X_TO_3D_STATE_DIR` only when the user explicitly needs a non-default credential location.

## Workflow

1. Resolve the user's input to one local file path.
2. Check that the file exists and has a supported extension.
3. Select a compatible output format:
   - Images: `png`, `webp`, or `jpeg`.
   - Videos: `mp4`, `mkv`, or `avi`.
   - Default images to `png` and videos to `mp4` unless the user requests another supported format.
4. Run the client from this skill directory:

```powershell
python scripts/x_to_3d.py convert "C:\path\input.jpg" --output-format png
```

5. Let `convert` initialize the anonymous device session, submit the job, poll it, and download the result.
6. Report the final local output path. Do not print credential files, access tokens, refresh tokens, or full signed download URLs.

On macOS or Linux, invoke the same command with `python3` when `python` is unavailable.

## Commands

Initialize the anonymous device session explicitly:

```powershell
python scripts/x_to_3d.py init
```

Show quota and active-job information:

```powershell
python scripts/x_to_3d.py account
```

Submit and wait for a conversion:

```powershell
python scripts/x_to_3d.py convert "<input-path>" --output-format <format>
```

Use `--output "<path>"` to choose the downloaded result path. Use `--no-wait` only when the user explicitly wants asynchronous submission.

Check, cancel, or download a known job:

```powershell
python scripts/x_to_3d.py status <job-id>
python scripts/x_to_3d.py cancel <job-id>
python scripts/x_to_3d.py download <job-id> --output "<path>"
```

Remove this device's local session:

```powershell
python scripts/x_to_3d.py logout
```

## Output Handling

The client writes one JSON object to stdout. Parse these fields when present:

- `ok`: whether the command succeeded.
- `job_id`: hosted conversion job identifier.
- `status`: `queued`, `processing`, `done`, `failed`, `cancelled`, or `expired`.
- `output_path`: local downloaded result.
- `error.code` and `error.message`: stable failure details.

Do not expose internal fields omitted by the client. Summarize service errors in plain language. For `RATE_LIMITED`, honor `retry_after_seconds` when provided. For `ACTIVE_JOB_EXISTS`, report the existing job instead of creating another.

## Safety

- Ask before overwriting an existing local output file.
- Do not upload unsupported files or files larger than 50MB.
- Do not modify the source file.
- Do not inspect or display the session file.
- Do not add the session file to a project or Git repository.
- Do not retry failed submissions indefinitely.

Read [references/api.md](references/api.md) only when debugging service compatibility or updating the client.
