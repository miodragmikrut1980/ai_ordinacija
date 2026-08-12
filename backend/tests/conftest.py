"""Test-session configuration.

Must set env vars *before* `app.state`/`app.main` are imported anywhere,
since `RATE_LIMIT_PER_MINUTE` is read once at import time.

The production default (`CLINIC_RATE_LIMIT_PER_MINUTE=240`) is a real
per-IP security control (see state.py) and must stay a low, deliberately
throttling number for a real deployment. The test suite runs from a single
TestClient "IP" and legitimately issues far more than 240 requests inside
a one-minute window (e.g. tenant-isolation tests spin up whole extra
organizations), so without this override, adding more tests eventually
makes unrelated later tests fail with 429s that have nothing to do with
what they're testing. The lockout/rate-limit behavior itself is still
exercised directly in test_api.py::test_login_rate_limit_and_lockout_still_enforced,
which triggers the (separate) login-lockout mechanism against a disposable
account rather than relying on this global IP limiter.
"""
import os
import tempfile
import atexit
import shutil

os.environ.setdefault("CLINIC_RATE_LIMIT_PER_MINUTE", "100000")
os.environ.setdefault("CLINIC_ENV", "demo")

# Isolate the test run from the real clinic database. Without this, the app
# under test writes to backend/../data (the same directory `./start.sh` uses
# for a real clinic), so persistent state like login-attempt history and
# lockouts survives between separate `pytest` invocations and can eventually
# lock out the real 'doctor' demo account outside of any test's control.
_tmp_data_dir = tempfile.mkdtemp(prefix="clinic-test-data-")
os.environ.setdefault("CLINIC_DATA_DIR", _tmp_data_dir)
atexit.register(lambda: shutil.rmtree(_tmp_data_dir, ignore_errors=True))
