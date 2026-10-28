import os
from pathlib import Path

Import("env")

# Portable outside-repo secrets directory resolution:
# 1. $ROBO_SECRETS_DIR environment variable (if set)
# 2. ~/.robo_secrets (works across any Windows username, macOS, or Linux)
secrets_dir = Path(os.environ.get("ROBO_SECRETS_DIR", Path.home() / ".robo_secrets"))
if secrets_dir.is_dir():
    env.Append(CPPPATH=[str(secrets_dir.resolve())])
