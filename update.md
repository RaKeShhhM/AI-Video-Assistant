# S1 update: validate sources and restrict YouTube fetching

Date: 24 September 2026

## Scope

This update implements **S1 only** from the placement review: accept exactly one supported YouTube video URL or an uploaded file, validate the source independently in both services, and restrict YouTube download destinations.

It does not implement S2 secret enforcement, S3 upload-size/media-content quotas, S4 Multer upgrades, durable jobs, general validation of every API field, or the other review items.

## What was the issue earlier?

1. Express only checked that either `youtubeUrl` or `file` was truthy. It did not reject both together or validate the URL's type, scheme, hostname, or video path.
2. Python accepted that string and guessed its meaning: HTTP(S) meant download, anything else meant a local file. A caller bypassing the frontend could put a server-local path in `youtubeUrl`. If that media file existed and was readable, the pipeline could process it.
3. Arbitrary web URLs could reach the generic downloader, creating a server-side request forgery (SSRF) attack surface. Hostname lookalikes, URL credentials, private-network addresses, and redirects needed explicit protection.
4. Merely setting `noplaylist` was not an input-validation policy. Playlist URLs and ambiguous video-plus-playlist requests still needed rejection.

The earlier yt-dlp runtime/EJS fix addressed YouTube's download requirements. **This S1 update addresses which inputs and network destinations the application accepts.**

## What we fixed now

### 1. Exactly one source

- Express checks the source before creating a MongoDB job or forwarding anything to Python.
- FastAPI repeats the check before writing an upload or scheduling processing.
- Missing sources, URL plus file, and blank URL plus file are rejected with HTTP 400.
- FastAPI also rejects repeated URL/file form fields. Express rejects repeated URL values because they become a non-string input; its existing single-file upload parser rejects multiple files.

### 2. A small, explicit YouTube URL policy

Accepted HTTPS hosts:

```text
youtube.com
www.youtube.com
m.youtube.com
music.youtube.com
youtu.be
```

Accepted video paths:

```text
https://www.youtube.com/watch?v=VIDEO_ID
https://youtu.be/VIDEO_ID
https://www.youtube.com/shorts/VIDEO_ID
https://www.youtube.com/embed/VIDEO_ID
https://www.youtube.com/live/VIDEO_ID
```

The video ID must be exactly 11 letters, digits, underscores, or hyphens. This validates identifier shape, not whether the video exists or is accessible.

Both services reject HTTP/non-web schemes, local paths, unknown hosts, IP addresses, embedded credentials, nonstandard ports, encoded/dot-segment paths, malformed IDs, duplicate `v` parameters, channel/redirect/playlist pages, and any `list` query parameter, including an empty one.

All accepted inputs are normalized to:

```text
https://www.youtube.com/watch?v=VIDEO_ID
```

Share/tracking parameters, timestamps, and fragments are discarded. Processing still covers the whole video, not a selected time range. A video link copied from a playlist must have its `list` parameter removed before submission.

### 3. Explicit pipeline source types

The pipeline now receives `source_type="youtube"` or `source_type="upload"`; it no longer guesses from a string prefix. The YouTube function revalidates URLs even when called independently.

Upload paths are generated in FastAPI using a random UUID rather than the caller's job ID. Filename extensions are constrained to simple alphanumeric suffixes. Before conversion, the pipeline resolves the path and confirms it is an existing file inside the configured upload directory. User-provided local paths cannot enter through the YouTube field.

This is path isolation, not a full media-content validator. Actual file-content checks and processing quotas remain S3.

### 4. Restrictions on YouTube network requests

The downloader uses a dedicated transport instead of falling back to arbitrary yt-dlp network handlers:

- Only HTTPS on port 443 is allowed.
- Destination hosts are restricted to the listed YouTube API/page hosts and subdomains of `googlevideo.com` and `ytimg.com` used for media/assets.
- Every redirect destination is checked before sending its request; redirects are capped at five.
- DNS results containing private, loopback, link-local, reserved, or multicast addresses are rejected before a socket connection.
- The connection uses the exact numeric address that was checked, avoiding a second DNS lookup between validation and connection. TLS still verifies the original hostname.
- Downloader proxies are disabled so they cannot resolve or route destinations outside these checks.
- Only direct HTTPS media formats are selected. Remote HLS/DASH manifests and external network downloaders are not used, so FFmpeg is used for local conversion rather than an unchecked remote fetch.

**Tradeoff:** videos offering only unsupported streaming formats may fail with a format error. This change does not guarantee that every YouTube video is downloadable. Normal YouTube access restrictions and occasional provider-side 403 errors still apply.

These restrictions cover YouTube acquisition. They are not an application-wide firewall; internal callbacks and other AI provider requests are separate paths. Network-level deployment restrictions remain useful defense in depth. The design follows the allowlisting and redirect-validation principles in the [OWASP SSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).

### 5. Regression tests and dependency compatibility

Both languages use the same URL fixture file to keep their policies aligned. Tests also exercise the Express controller, real FastAPI multipart parsing, pipeline source dispatch, redirects, DNS results, and connection address selection.

The yt-dlp version is pinned to `2026.8.19` because the restricted transport integrates with its networking classes. Upgrade it deliberately and rerun these tests. The Node/EJS runtime fix is retained.

## Files changed for S1

| File | Purpose |
|---|---|
| `server/src/utils/videoSource.js` | URL normalization and exactly-one-source validation. |
| `server/src/controllers/videoController.js` | Validate before saving/forwarding; save normalized source. |
| `server/package.json` | Add `npm test` using Node's built-in test runner. |
| `ai-service/utils/video_source.py` | Independent Python source validation. |
| `ai-service/main.py` | Validate multipart inputs, generate upload paths, pass source type. |
| `ai-service/pipeline.py` | Pass explicit source type into audio processing. |
| `ai-service/utils/audio_processor.py` | Revalidate YouTube URLs, constrain upload paths and downloader use. |
| `ai-service/utils/youtube_network.py` | Guard initial requests, redirects, DNS results, and connections. |
| `ai-service/requirements.txt` | Pin the downloader version used by the guarded transport. |
| `tests/fixtures/video_sources.json` | Shared accepted/rejected URL cases. |
| `server/test/videoSource.test.js` | Express validation/controller regression checks. |
| `ai-service/tests/` | Python source, endpoint, transport, and downloader regression checks. |
| `ai-service/YOUTUBE_SETUP.md` | Align installation instructions with the tested dependency pin. |

## How to verify manually

### Step 1: restart both backends

Stop the running Express and FastAPI processes with Ctrl+C in their own terminals, then restart them so changes are loaded.

Express, from `server`:

```sh
npm run dev
```

Python, from `ai-service` with its virtual environment activated:

```sh
python -m uvicorn main:app --reload --port 8000
```

Keep the frontend running and log in. If your dependencies differ from the tested version, use `python -m pip install -r requirements.txt` in the AI virtual environment.

### Step 2: verify the normal UI paths

1. Submit a normal HTTPS YouTube video you can access. Expected: a job is created and begins processing. Its stored source is a canonical `https://www.youtube.com/watch?v=...` URL.
2. Submit the same video's `youtu.be` share URL. Expected: the same normalized source format, without share parameters.
3. Upload a small valid audio/video file. Expected: the upload path still works and processing starts.
4. Submit a YouTube URL with `&list=...`. Expected: a playlist-specific error, no new job, and no Python download attempt.
5. Submit `https://example.com/video.mp4` in the YouTube input. Expected: an HTTPS YouTube URL validation error, no new job, and no download attempt.

A 201 job response or Python's 200 accepted response means validation/acceptance succeeded; check subsequent job completion separately.

### Step 3: bypass the browser's URL validation to test the API

Open browser DevTools → Console on the logged-in app. The following helper uses your existing session without printing its token. Change the API origin if you configured a different backend port.

```javascript
async function checkSource(youtubeUrl) {
  const response = await fetch("http://localhost:5000/api/videos", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${localStorage.getItem("token")}`,
    },
    body: JSON.stringify({ youtubeUrl }),
  });
  console.log(response.status, await response.json());
}
```

Run each of these individually. **Every request below should return HTTP 400**, create no new job, and produce no Python download/conversion log:

```javascript
await checkSource("C:\\private\\recording.mp4");
await checkSource("/private/recording.wav");
await checkSource("file:///etc/passwd");
await checkSource("https://127.0.0.1:8000/health");
await checkSource("http://169.254.169.254/latest/meta-data/");
await checkSource("https://youtube.com.evil.example/watch?v=BaW_jenozKc");
await checkSource("https://evil.example@youtube.com/watch?v=BaW_jenozKc");
await checkSource("https://www.youtube.com/redirect?q=https://127.0.0.1");
await checkSource("https://www.youtube.com/watch?v=BaW_jenozKc&list=PLtest");
await checkSource("https://www.youtube.com/watch?v=BaW_jenozKc&v=abcdefghijk");
await checkSource({ unexpected: "object" });
await checkSource(123);
await checkSource("");
```

These strings are deliberately rejected before any outbound request or file read. If you get 401, log in again before evaluating source-validation behavior.

### Step 4: verify URL plus upload is rejected

Run this in the same browser console:

```javascript
const form = new FormData();
form.append("youtubeUrl", "https://www.youtube.com/watch?v=BaW_jenozKc");
form.append("file", new Blob(["not processed"], { type: "audio/wav" }), "test.wav");
const response = await fetch("http://localhost:5000/api/videos", {
  method: "POST",
  headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
  body: form,
});
console.log(response.status, await response.json());
```

Expected: **400**, exactly-one-source message, no new MongoDB job, and no AI request. Repeat with an empty `youtubeUrl` field: still 400. An empty request with neither field should also return 400.

### Step 5: verify Python does not depend on Express validation

Use Postman to send `POST http://localhost:8000/process` as multipart form-data. Add:

| Field | Value |
|---|---|
| `job_id` | `s1-manual-invalid` |
| `language` | `english` |
| `callback_url` | `http://localhost:5000/api/internal/jobs/progress` |
| `callback_secret` | Your existing internal shared secret, entered locally. |
| `service_secret` | The same configured shared secret, entered locally. |
| `youtube_url` | `/private/recording.wav` |

Expected: **400** with a URL validation error; no job is scheduled. Repeat with an unsupported HTTPS host or a playlist URL. Then add a file alongside a valid YouTube URL: expected 400 again. Do not publish screenshots containing your secret.

Missing/incorrect credentials return 401 when a service secret is configured. Fail-closed behavior for an unset secret is **S2 and is not part of this update**.

### Step 6: verify redirects and private DNS safely

Do not modify your hosts file or send requests to real internal services. Run the deterministic network tests instead; they simulate those conditions and assert that the forbidden request/socket is never sent:

```sh
# From ai-service, with the virtual environment active:
python -m unittest discover -s tests -p test_youtube_network.py -v
```

## Verification performed during implementation

- `npm test` in `server`: **4 tests passed**, including the shared URL cases and rejection before database access.
- `python -m unittest discover -s tests -v` in `ai-service`: **18 tests passed**, including real multipart endpoint parsing with the worker mocked.
- The restricted transport successfully fetched YouTube's public `robots.txt`: **HTTP 200**, reading only 64 bytes. The sandbox initially denied outbound sockets; the approved network check succeeded.
- No paid AI calls or real database mutations were needed for these tests. The full upload/transcription/summary flow and a full media download remain the manual happy-path checks above.
- The installed Starlette test client emitted a dependency deprecation warning; the endpoint tests still passed. Changing that dependency is outside S1.

To rerun the full checks:

```sh
# Terminal 1: from server
npm test

# Terminal 2: from ai-service, virtual environment active
python -m unittest discover -s tests -v
```


