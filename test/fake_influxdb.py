# ==============================================================================
# NAME     : fake_influxdb.py
# PURPOSE  : Minimal InfluxDB 1.x HTTP /query emulator for stager tests.
#            Implements what nfit-stage-influxdb's performance acquisition and
#            the legacy single-query path use:
#              SELECT "f1"[, "f2"...] FROM "m" WHERE <entity filter>
#                AND time >= N AND time (<|<=) M GROUP BY "host"
#            - entity filter: OR-ed "serial_no" = 'x' or host = 'x' terms
#            - epoch=ns returns integer nanoseconds; otherwise RFC3339Nano
#              strings with trailing zeros trimmed (JSON) or extension type 5
#              timestamps (MessagePack), as InfluxDB does
#            - chunked=true streams one JSON object per HTTP chunk, at most
#              chunk_size rows per chunk; series.partial and result.partial
#              mark continuation exactly as InfluxDB 1.x does
#            - Accept negotiates JSON or MessagePack, including concatenated
#              MessagePack objects for a chunked response; JSON numbers follow
#              Go's encoder, so a whole-number float is written as an integer
#            - rows whose selected fields are all null are omitted
#            - GROUP BY * keeps every tag set as its own series
#            - SHOW FIELD KEYS / SHOW TAG KEYS FROM "m", SHOW RETENTION
#              POLICIES and /ping (X-Influxdb-Version), for discovery
#            Faults can be injected per query to test interrupted, truncated,
#            erroneous and failing responses.
# ==============================================================================
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

NS = 1_000_000_000


def rfc3339_nano(ns):
    sec, frac = divmod(ns, NS)
    base = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(sec))
    if frac:
        base += '.' + f'{frac:09d}'.rstrip('0')
    return base + 'Z'


def go_json_numbers(obj):
    """InfluxDB is written in Go, whose JSON encoder writes a whole-number
    float64 without a fractional part (2.0 -> 2). Python's json keeps 2.0, so
    emulate Go to expose any float/int ambiguity of the JSON transport."""
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, dict):
        return {k: go_json_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [go_json_numbers(v) for v in obj]
    return obj


class MsgpackTime:
    """A timestamp the MessagePack encoder writes as InfluxDB extension type 5
    (big-endian unsigned seconds, then nanoseconds), as the server does for a
    query without epoch=."""

    def __init__(self, ns):
        self.ns = ns


def msgpack_default(obj):
    import msgpack  # installed with the real influxdb client
    import struct
    if isinstance(obj, MsgpackTime):
        return msgpack.ExtType(5, struct.pack('>QI', obj.ns // 1_000_000_000, obj.ns % 1_000_000_000))
    raise TypeError(f'cannot pack {type(obj).__name__}')


class Fault:
    """A fault applied to queries whose text contains `match` (a string, or a
    tuple of strings that must all be present).
    mode: 'interrupt'   - send `after` chunks, then drop the connection
          'partial_end' - send `after` (partial) chunks, then end cleanly
          'error'       - send one chunk carrying a query error
          'http500'     - respond with HTTP 500
    times: how many matching requests are affected (None = all)."""

    def __init__(self, match, mode, after=1, times=1, message=None):
        self.match, self.mode, self.after, self.times = match, mode, after, times
        self.message = message      # error text for mode "error"
        self.hits = 0

    def applies(self, query):
        parts = self.match if isinstance(self.match, tuple) else (self.match,)
        if not all(part in query for part in parts):
            return False
        if self.times is not None and self.hits >= self.times:
            return False
        self.hits += 1
        return True


class FakeInfluxDB:
    def __init__(self, points):
        """points: iterable of dicts with keys measurement, time_ns, tags (must
        include host and serial_no), fields."""
        self.points = list(points)
        self.field_types = {}       # measurement -> {field: [types]}; else inferred
        self.retention_policies = [{'name': 'autogen', 'duration': '0s', 'shardGroupDuration': '168h0m0s',
                                    'replicaN': 1, 'default': True}]
        self.version = '1.8.10'
        self.faults = []
        self.queries = []
        self.accept_headers = []
        self._server = None

    # -- query evaluation ------------------------------------------------------
    @staticmethod
    def _influx_type(v):
        if isinstance(v, bool):
            return 'boolean'
        if isinstance(v, int):
            return 'integer'
        if isinstance(v, float):
            return 'float'
        return 'string'

    def evaluate_show(self, q):
        """Series for a discovery statement, or None if q is not one."""
        m = re.match(r'SHOW (FIELD|TAG) KEYS FROM "([^"]+)"', q)
        if m:
            kind, measurement = m.groups()
            pts = [p for p in self.points if p['measurement'] == measurement]
            if kind == 'FIELD':
                types = self.field_types.get(measurement)
                if types is None:
                    types = {}
                    for p in pts:
                        for f, v in p['fields'].items():
                            if v is not None and self._influx_type(v) not in types.setdefault(f, []):
                                types[f].append(self._influx_type(v))
                rows = [[f, t] for f in sorted(types) for t in types[f]]
                columns = ['fieldKey', 'fieldType']
            else:
                rows = [[k] for k in sorted({k for p in pts for k in p['tags']})]
                columns = ['tagKey']
            return [{'name': measurement, 'tags': None, 'columns': columns, 'values': rows}] if rows else []
        if q.startswith('SHOW RETENTION POLICIES'):
            cols = ['name', 'duration', 'shardGroupDuration', 'replicaN', 'default']
            return [{'columns': cols, 'values': [[rp[c] for c in cols] for rp in self.retention_policies]}]
        return None

    def evaluate(self, q):
        m = re.search(r'SELECT\s+(.*?)\s+FROM\s+"([^"]+)"', q)
        fields = re.findall(r'"([^"]+)"', m.group(1))
        measurement = m.group(2)
        t = re.search(r'time >= (\d+) AND time (<=|<) (\d+)', q)
        lo, op, hi = int(t.group(1)), t.group(2), int(t.group(3))
        serials = set(re.findall(r'"serial_no" = \'([^\']+)\'', q))
        hosts = set(re.findall(r'host = \'([^\']+)\'', q))

        def selected(p):
            if p['measurement'] != measurement:
                return False
            if serials and p['tags'].get('serial_no') not in serials:
                return False
            if hosts and p['tags'].get('host') not in hosts:
                return False
            ts = p['time_ns']
            return lo <= ts and (ts <= hi if op == '<=' else ts < hi)

        group_all = 'GROUP BY *' in q
        by_group = {}
        for p in self.points:
            if not selected(p):
                continue
            row_values = [p['fields'].get(f) for f in fields]
            if all(v is None for v in row_values):
                continue
            key = tuple(sorted(p['tags'].items())) if group_all else (('host', p['tags'].get('host', '')),)
            by_group.setdefault(key, []).append((p['time_ns'], row_values))
        series = []
        for key in sorted(by_group):
            rows = sorted(by_group[key], key=lambda r: r[0])   # stable for equal times
            series.append({'name': measurement, 'tags': dict(key),
                           'columns': ['time'] + fields, 'rows': rows})
        return series

    @staticmethod
    def render_rows(rows, epoch_ns, ext_time=False):
        if epoch_ns:
            return [[ts] + vals for ts, vals in rows]
        if ext_time:
            return [[MsgpackTime(ts)] + vals for ts, vals in rows]
        return [[rfc3339_nano(ts)] + vals for ts, vals in rows]

    def chunks(self, series, epoch_ns, chunk_size, ext_time=False):
        out = []
        for s in series:
            rows = s['rows']
            for i in range(0, len(rows), chunk_size):
                part = rows[i:i + chunk_size]
                ser = {'name': s['name'], 'tags': s['tags'], 'columns': s['columns'],
                       'values': self.render_rows(part, epoch_ns, ext_time)}
                if i + chunk_size < len(rows):
                    ser['partial'] = True
                out.append({'results': [{'statement_id': 0, 'series': [ser]}]})
        if not out:
            out.append({'results': [{'statement_id': 0}]})
        for obj in out[:-1]:
            obj['results'][0]['partial'] = True
        return out

    # -- server ----------------------------------------------------------------
    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def do_GET(self):
                if urlparse(self.path).path == '/ping':
                    self.send_response(204)
                    self.send_header('X-Influxdb-Version', fake.version)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                qs = parse_qs(urlparse(self.path).query)
                q = qs.get('q', [''])[0]
                fake.queries.append(q)
                accept = self.headers.get('Accept', '')
                fake.accept_headers.append(accept)
                use_msgpack = 'application/x-msgpack' in accept
                content_type = 'application/x-msgpack' if use_msgpack else 'application/json'

                def encode(obj):
                    if use_msgpack:
                        import msgpack  # installed with the real influxdb client
                        return msgpack.packb(obj, use_bin_type=True, default=msgpack_default)
                    return (json.dumps(go_json_numbers(obj)) + '\n').encode('utf-8')

                epoch_ns = qs.get('epoch', [''])[0] == 'ns'
                chunked = qs.get('chunked', ['false'])[0] == 'true'
                chunk_size = int(qs.get('chunk_size', ['10000'])[0])
                fault = next((f for f in fake.faults if f.applies(q)), None)

                if fault and fault.mode == 'http500':
                    body = b'{"error":"internal error"}'
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                show = fake.evaluate_show(q)
                if show is not None:
                    result = {'statement_id': 0}
                    if show:
                        result['series'] = show
                    body = encode({'results': [result]})
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                series = fake.evaluate(q)
                if not chunked:
                    result = {'statement_id': 0}
                    if series:
                        result['series'] = [{'name': s['name'], 'tags': s['tags'], 'columns': s['columns'],
                                             'values': fake.render_rows(s['rows'], epoch_ns, use_msgpack)} for s in series]
                    body = encode({'results': [result]})
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                objs = fake.chunks(series, epoch_ns, chunk_size, use_msgpack)
                if fault and fault.mode == 'error':
                    objs = [{'results': [{'statement_id': 0, 'error': fault.message or 'query timeout'}]}]
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                for n, obj in enumerate(objs):
                    if fault and fault.mode in ('interrupt', 'partial_end') and n >= fault.after:
                        break
                    data = encode(obj)
                    self.wfile.write(f'{len(data):x}\r\n'.encode() + data + b'\r\n')
                    self.wfile.flush()
                if fault and fault.mode == 'interrupt':
                    # Drop the connection without the terminating zero-length chunk.
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                self.wfile.write(b'0\r\n\r\n')

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self._server.server_address[1]

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
