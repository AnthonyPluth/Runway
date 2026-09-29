import os

# Tests never touch a real key file: secrets are encrypted with this throwaway key (see runway/secretbox.py).
os.environ.setdefault("RUNWAY_SECRET_KEY", "test-only-key-0123456789abcdefghijklmnop")
