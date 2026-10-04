# runway/tls.py is the one place that opens a URL itself.
import urllib.request

# ok: runway-raw-urlopen
urllib.request.urlopen(req)
# ok: runway-raw-urlopen
urllib.request.build_opener()
