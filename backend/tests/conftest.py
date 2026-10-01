import os


# Configuration is validated while application modules are imported. Tests use
# an explicit non-production key rather than relying on an application default.
os.environ.setdefault(
    "SECRET_KEY",
    "test-only-session-signing-key-32-characters-minimum",
)
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("USER_USERNAME", "test-user")
os.environ.setdefault("USER_PASSWORD", "test-user-password")
