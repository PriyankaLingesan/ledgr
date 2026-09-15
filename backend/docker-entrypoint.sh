#!/bin/sh
# Render's Docker "start command" override is a single scalar value that has
# to survive whatever tokenization Render's control plane applies to it
# before it reaches the container. A multi-word shell one-liner (quotes,
# `&&`, `$PORT`) did not survive that trip intact - Render's deploy log
# showed the whole "python -m alembic ... && python -m uvicorn ..." line
# glued together and looked up as a single, nonexistent command name.
#
# A path to this one file has nothing in it for a naive tokenizer to
# mis-split: no spaces, no quotes, no shell operators. Render's dockerCommand
# is just this script's absolute path; everything shell-shaped lives here,
# inside the image, where a real `sh` (via the shebang above) always
# interprets it correctly.
#
# /opt/venv/bin/python is the exact interpreter backend/Dockerfile installs
# every dependency into (`python -m venv /opt/venv` in the builder stage,
# copied into the runtime stage unchanged) - verified present in the built
# image via `which python` / `sys.executable` before this fix was written.
set -e
/opt/venv/bin/python -m alembic upgrade head
exec /opt/venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
