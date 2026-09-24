import threading

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.formparsers import MultiPartException

from utils.media_limits import MAX_BYTES, MAX_AI_JOBS


class ProcessLimitsMiddleware:
    def __init__(self, app, max_bytes=MAX_BYTES + 65536, max_jobs=MAX_AI_JOBS):
        self.app = app
        self.max_bytes = max_bytes
        self.slots = threading.BoundedSemaphore(max_jobs)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        processing = scope["path"].rstrip("/") == "/process"
        asking = scope["path"].rstrip("/") == "/ask"
        if not processing and not asking:
            return await self.app(scope, receive, send)
        cap = self.max_bytes if processing else 16384
        try:
            lengths = [int(value) for key, value in scope.get("headers", []) if key == b"content-length"]
            if len(lengths) > 1 or any(value < 0 for value in lengths):
                raise ValueError()
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
        if lengths and lengths[0] > cap:
            return await JSONResponse({"detail": "Request exceeds the upload limit."}, status_code=413)(scope, receive, send)
        if processing and not self.slots.acquire(blocking=False):
            return await JSONResponse({"detail": "AI processing capacity is full. Try again later."},
                                      status_code=429, headers={"Retry-After": "60"})(scope, receive, send)
        total = 0
        exceeded = False

        async def bounded_receive():
            nonlocal total, exceeded
            message = await receive()
            if processing and message["type"] == "http.disconnect":
                # Use the parser's cleanup path for unfinished upload spools.
                raise MultiPartException("Upload interrupted.")
            total += len(message.get("body", b""))
            if total > cap:
                exceeded = True
                if processing:
                    # Starlette closes partially spooled files on this error.
                    raise MultiPartException("Request exceeds the upload limit.")
                raise HTTPException(413, "Request exceeds the upload limit.")
            return message

        async def bounded_send(message):
            if exceeded and message["type"] == "http.response.start":
                message = {**message, "status": 413}
            await send(message)

        try:
            # Await includes Starlette's background task: the slot stays held
            # after the 200 response until processing actually finishes.
            await self.app(scope, bounded_receive, bounded_send)
        finally:
            if processing:
                self.slots.release()
