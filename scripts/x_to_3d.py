#!/usr/bin/env python3
"""Dependency-free client for the hosted X-to-3D Agent API."""

from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import os
import secrets
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


MAX_INPUT_BYTES = 50 * 1024 * 1024
IMAGE_INPUTS = {"jpg", "jpeg", "png", "webp"}
VIDEO_INPUTS = {"mp4", "mov", "webm", "mkv", "avi"}
IMAGE_OUTPUTS = {"png", "webp", "jpeg"}
VIDEO_OUTPUTS = {"mp4", "mkv", "avi"}
TERMINAL_STATUSES = {"done", "failed", "cancelled", "expired"}
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 6 * 60 * 60
DEFAULT_API_BASE_URL = "https://ntpmkvxomxmsdamwzccl.supabase.co/functions/v1/x-to-3d-api"


class ClientError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: Optional[int] = None,
        retry_after_seconds: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after_seconds = retry_after_seconds


def state_dir() -> Path:
    configured = os.environ.get("X_TO_3D_STATE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".x-to-3d"


def session_path() -> Path:
    return state_dir() / "session.json"


def api_base_url() -> str:
    value = os.environ.get("X_TO_3D_API_BASE_URL", DEFAULT_API_BASE_URL).strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ClientError("INVALID_CONFIG", "X_TO_3D_API_BASE_URL must be an absolute HTTP(S) URL.")
    return value


def read_session() -> Optional[Dict[str, Any]]:
    path = session_path()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClientError("SESSION_INVALID", "The local session is unreadable. Run logout and initialize again.") from exc
    if not isinstance(value, dict):
        raise ClientError("SESSION_INVALID", "The local session has an invalid format.")
    return value


def write_session(value: Dict[str, Any]) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass
    path = session_path()
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    try:
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    temporary.replace(path)


def remove_session() -> bool:
    path = session_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def decode_json(raw: bytes) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientError("INVALID_RESPONSE", "The service returned an invalid response.") from exc
    if not isinstance(value, dict):
        raise ClientError("INVALID_RESPONSE", "The service returned an unexpected response shape.")
    return value


def api_error(status: int, payload: Dict[str, Any]) -> ClientError:
    error = payload.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "HTTP_ERROR")
        message = str(error.get("message") or f"The service returned HTTP {status}.")
        retry = error.get("retry_after_seconds")
        return ClientError(code, message, status=status, retry_after_seconds=retry if isinstance(retry, int) else None)
    return ClientError("HTTP_ERROR", f"The service returned HTTP {status}.", status=status)


def request(
    method: str,
    route: str,
    *,
    token: Optional[str] = None,
    body: Optional[bytes] = None,
    content_type: Optional[str] = "application/json",
    timeout: float = 60.0,
) -> Dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "x-to-3d-skill/0.1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None and content_type:
        headers["Content-Type"] = content_type
    last_error: Optional[BaseException] = None
    for attempt in range(3):
        req = urllib.request.Request(api_base_url() + route, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return decode_json(response.read())
        except urllib.error.HTTPError as exc:
            payload = decode_json(exc.read())
            raise api_error(exc.code, payload) from exc
        except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.75 * (2 ** attempt))
    raise ClientError("SERVICE_UNREACHABLE", "Could not reach the X-to-3D service.") from last_error


def validate_session_payload(payload: Dict[str, Any], installation_id: str) -> Dict[str, Any]:
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        raise ClientError("INVALID_RESPONSE", "Authentication response did not contain an access token.")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise ClientError("INVALID_RESPONSE", "Authentication response did not contain a refresh token.")
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, (int, float)):
        expires_in = payload.get("expires_in")
        if not isinstance(expires_in, (int, float)):
            expires_in = 3600
        expires_at = int(time.time() + expires_in)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(expires_at),
        "installation_id": installation_id,
    }


def initialize_session() -> Dict[str, Any]:
    existing = read_session()
    if existing:
        return existing
    installation_id = str(uuid.uuid4())
    body = json.dumps({"installation_id": installation_id}).encode("utf-8")
    payload = request("POST", "/auth/anonymous", body=body)
    session = validate_session_payload(payload, installation_id)
    write_session(session)
    return session


def refresh_session(session: Dict[str, Any]) -> Dict[str, Any]:
    refresh_token = session.get("refresh_token")
    installation_id = session.get("installation_id")
    if not isinstance(refresh_token, str) or not isinstance(installation_id, str):
        raise ClientError("SESSION_INVALID", "The local session is missing required fields.")
    body = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
    payload = request("POST", "/auth/refresh", body=body)
    updated = validate_session_payload(payload, installation_id)
    write_session(updated)
    return updated


def authenticated_session() -> Dict[str, Any]:
    session = initialize_session()
    expires_at = session.get("expires_at", 0)
    if not isinstance(expires_at, (int, float)) or expires_at <= time.time() + 60:
        session = refresh_session(session)
    return session


def authenticated_request(
    method: str,
    route: str,
    *,
    body: Optional[bytes] = None,
    content_type: Optional[str] = "application/json",
    timeout: float = 60.0,
) -> Dict[str, Any]:
    session = authenticated_session()
    try:
        return request(
            method,
            route,
            token=str(session["access_token"]),
            body=body,
            content_type=content_type,
            timeout=timeout,
        )
    except ClientError as exc:
        if exc.status != 401:
            raise
    session = refresh_session(session)
    return request(
        method,
        route,
        token=str(session["access_token"]),
        body=body,
        content_type=content_type,
        timeout=timeout,
    )


def validate_input(path_text: str, output_format: Optional[str]) -> Tuple[Path, str]:
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        raise ClientError("INPUT_NOT_FOUND", f"Input file does not exist: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise ClientError("EMPTY_INPUT", "Input file is empty.")
    if size > MAX_INPUT_BYTES:
        raise ClientError("FILE_TOO_LARGE", "Input files must be 50MB or smaller.")
    extension = path.suffix.lower().lstrip(".")
    if extension in IMAGE_INPUTS:
        allowed_outputs = IMAGE_OUTPUTS
        selected = (output_format or "png").lower()
    elif extension in VIDEO_INPUTS:
        allowed_outputs = VIDEO_OUTPUTS
        selected = (output_format or "mp4").lower()
    else:
        raise ClientError("UNSUPPORTED_INPUT_FORMAT", f"Unsupported input extension: {extension or '(none)'}")
    if selected == "jpg":
        selected = "jpeg"
    if selected not in allowed_outputs:
        raise ClientError("UNSUPPORTED_OUTPUT_FORMAT", f"Output format {selected} is not compatible with this input.")
    return path, selected


def upload_input(path: Path, output_format: str) -> Dict[str, Any]:
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    size = path.stat().st_size
    body = json.dumps({
        "file_name": path.name,
        "content_type": content_type,
        "bytes": size,
    }).encode("utf-8")
    upload = authenticated_request("POST", "/uploads", body=body)
    upload_url = upload.get("upload_url")
    object_key = upload.get("object_key")
    if not isinstance(upload_url, str) or not upload_url:
        raise ClientError("INVALID_RESPONSE", "Upload response did not contain an upload URL.")
    if not isinstance(object_key, str) or not object_key:
        raise ClientError("INVALID_RESPONSE", "Upload response did not contain an object key.")
    request_headers = {
        "Content-Type": content_type,
        "Content-Length": str(size),
        "cache-control": "max-age=3600",
        "x-upsert": "false",
        "User-Agent": "x-to-3d-skill/0.2.0",
    }
    upload_request = urllib.request.Request(
        upload_url,
        data=path.read_bytes(),
        headers=request_headers,
        method="PUT",
    )
    try:
        with urllib.request.urlopen(upload_request, timeout=300.0) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        exc.read()
        raise ClientError("UPLOAD_FAILED", f"Supabase Storage rejected the upload with HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ClientError("UPLOAD_FAILED", "Could not upload the input to Supabase Storage.") from exc
    return {
        "object_key": object_key,
        "file_name": path.name,
        "content_type": content_type,
        "bytes": size,
        "output_format": output_format,
    }


def create_job(path: Path, output_format: str) -> Dict[str, Any]:
    upload = upload_input(path, output_format)
    body = json.dumps(upload).encode("utf-8")
    payload = authenticated_request("POST", "/jobs", body=body, timeout=180.0)
    if not isinstance(payload.get("job_id"), str):
        raise ClientError("INVALID_RESPONSE", "Job response did not contain a job identifier.")
    return payload


def get_job(job_id: str) -> Dict[str, Any]:
    return authenticated_request("GET", f"/jobs/{urllib.parse.quote(job_id, safe='')}", content_type=None)


def wait_for_job(job_id: str, poll_seconds: float, timeout_seconds: float) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = get_job(job_id)
        status = str(last.get("status", ""))
        if status in TERMINAL_STATUSES:
            return last
        time.sleep(poll_seconds)
    raise ClientError("POLL_TIMEOUT", f"Timed out while waiting for job {job_id}.")


def default_output_path(input_path: Optional[Path], result: Dict[str, Any], job_id: str) -> Path:
    file_name = result.get("file_name")
    if not isinstance(file_name, str) or not file_name:
        file_name = f"x-to-3d-{job_id}.bin"
    directory = input_path.parent if input_path else Path.cwd()
    return directory / file_name


def download_job(job_id: str, output_text: Optional[str], input_path: Optional[Path] = None) -> Path:
    route = f"/jobs/{urllib.parse.quote(job_id, safe='')}/result"
    result = authenticated_request("GET", route, content_type=None)
    download_url = result.get("download_url")
    if not isinstance(download_url, str) or not download_url:
        raise ClientError("INVALID_RESPONSE", "Result response did not contain a download URL.")
    output = Path(output_text).expanduser().resolve() if output_text else default_output_path(input_path, result, job_id)
    if output.exists():
        raise ClientError("OUTPUT_EXISTS", f"Refusing to overwrite existing file: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{secrets.token_hex(6)}.part")
    try:
        with urllib.request.urlopen(download_url, timeout=300.0) as response, temporary.open("wb") as target:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                target.write(block)
        temporary.replace(output)
    except (OSError, urllib.error.URLError) as exc:
        if temporary.exists():
            temporary.unlink()
        raise ClientError("DOWNLOAD_FAILED", "The generated output could not be downloaded.") from exc
    return output


def command_init(_: argparse.Namespace) -> Dict[str, Any]:
    initialize_session()
    return {"ok": True, "authenticated": True, "message": "Anonymous device session initialized."}


def command_account(_: argparse.Namespace) -> Dict[str, Any]:
    payload = authenticated_request("GET", "/account", content_type=None)
    return {"ok": True, **payload}


def command_status(args: argparse.Namespace) -> Dict[str, Any]:
    return {"ok": True, **get_job(args.job_id)}


def command_cancel(args: argparse.Namespace) -> Dict[str, Any]:
    route = f"/jobs/{urllib.parse.quote(args.job_id, safe='')}/cancel"
    payload = authenticated_request("POST", route, body=b"{}")
    return {"ok": True, **payload}


def command_download(args: argparse.Namespace) -> Dict[str, Any]:
    output = download_job(args.job_id, args.output)
    return {"ok": True, "job_id": args.job_id, "status": "done", "output_path": str(output)}


def command_convert(args: argparse.Namespace) -> Dict[str, Any]:
    path, output_format = validate_input(args.input, args.output_format)
    created = create_job(path, output_format)
    job_id = str(created["job_id"])
    if args.no_wait:
        return {"ok": True, **created}
    job = wait_for_job(job_id, args.poll_seconds, args.timeout)
    status = str(job.get("status", ""))
    if status != "done":
        message = str(job.get("error_message") or job.get("error") or f"Job ended with status {status}.")
        raise ClientError("JOB_NOT_COMPLETED", message)
    output = download_job(job_id, args.output, path)
    return {"ok": True, **job, "job_id": job_id, "status": "done", "output_path": str(output)}


def command_logout(_: argparse.Namespace) -> Dict[str, Any]:
    removed = remove_session()
    return {"ok": True, "authenticated": False, "removed": removed}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Client for the hosted X-to-3D conversion service.")
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Initialize an anonymous device session.")
    init.set_defaults(handler=command_init)
    account = sub.add_parser("account", help="Show quota and active-job information.")
    account.set_defaults(handler=command_account)
    convert = sub.add_parser("convert", help="Submit a file and optionally wait for its result.")
    convert.add_argument("input")
    convert.add_argument("--output-format")
    convert.add_argument("--output")
    convert.add_argument("--no-wait", action="store_true")
    convert.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    convert.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    convert.set_defaults(handler=command_convert)
    status = sub.add_parser("status", help="Read a job.")
    status.add_argument("job_id")
    status.set_defaults(handler=command_status)
    cancel = sub.add_parser("cancel", help="Cancel a queued job.")
    cancel.add_argument("job_id")
    cancel.set_defaults(handler=command_cancel)
    download = sub.add_parser("download", help="Download a completed job.")
    download.add_argument("job_id")
    download.add_argument("--output")
    download.set_defaults(handler=command_download)
    logout = sub.add_parser("logout", help="Remove this device's local session.")
    logout.set_defaults(handler=command_logout)
    return root


def public_error(exc: ClientError) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.retry_after_seconds is not None:
        error["retry_after_seconds"] = exc.retry_after_seconds
    return {"ok": False, "error": error}


def main() -> int:
    args = parser().parse_args()
    try:
        result = args.handler(args)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except ClientError as exc:
        print(json.dumps(public_error(exc), ensure_ascii=True))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": {"code": "INTERRUPTED", "message": "Operation interrupted."}}))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
