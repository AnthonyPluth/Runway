"""Stand-ins for the outside services, served on the loopback address: `make verify` and tools/control run them beside
each demo server (runway/verify.py's `serve`, with RUNWAY_VERIFY_FAKES=1), and the tests run them in-process. Made-up
data only, to match the demo's (runway/domain/demo.py). Under tests/, so never in the image (.dockerignore)."""
