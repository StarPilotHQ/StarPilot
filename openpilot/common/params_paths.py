"""Select persistent device Params independently of native build hardware."""

import os


def params_path(path=""):
  if path:
    return path
  configured = os.environ.get("PARAMS_ROOT")
  if configured:
    return configured
  return "/data/params" if os.path.isfile("/AGNOS") else path
