"""One acquisition job in its own process: `python -m acquisition.job <job directory>`.

Started only by acquisition.service, which holds the profile owner and its exclusive session for the whole job and
passes the session's profile directory. A process per job because the SDK keeps process-wide state (browser,
browser-first mode, token) and the adapter installs once per process.

The job directory holds job.json (identity, recipe, provider, profile, limits) and inventory.json (sent by the
backend); this process runs its recipe (the pilot's or the group's, acquisition/recipe.py) and writes bundle.json (sealed, read-only) and report.json. The file `cancel` in that directory
requests a cooperative stop. The cache and the SDK's own data stay under the job directory; the transport's durable
budget and refusal blocks under the service's transport root, shared by all jobs.

Providers: 'sofascore' (the real site; ACQ_ENABLE_REAL=1 in the service) and 'fixture-site' (test images only: the
same SDK flow, browser included, against a local site that serves recorded responses).

Besides the adapter's own record (physical sends, page requests), it samples /proc/net of the container's network
namespace. That is a sample every 0.2 s, not a packet capture: it gives IP and port, never names; it can miss a TCP
connection that the remote side closes within one interval and UDP that is not connected. Connections to the
service's own port (the backend's control requests) are left out.
"""
from contextlib import closing
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

SECONDS = 300
MAX_SENDS = 8
SERVICE_PORT = 8002  # inbound control connections from the backend: not egress, not recorded
TCP_STATES = {'01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV', '04': 'FIN_WAIT1', '05': 'FIN_WAIT2',
              '06': 'TIME_WAIT', '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK', '0A': 'LISTEN', '0B': 'CLOSING'}


def now():
    return datetime.now(timezone.utc).isoformat()


def may_continue_group(*, error, descriptor, report, operation_state):
    """Only a successfully sealed batch with remaining headroom may start another batch."""
    from acquisition.group_plan import SEND_RESERVE
    return bool(error is None and descriptor is not None and report['stopped'] is None
                and not report['budget_exhausted'] and operation_state['remaining'] > SEND_RESERVE)


class StrictBreaker:
    """One attempt per route: any result other than ok opens the breaker and stops the whole job."""
    tripped = False

    def record(self, reason):
        if reason != 'ok':
            self.tripped = True


def address(text, family):
    host, port = text.split(':')
    raw = bytes.fromhex(host)
    # /proc/net stores each 32-bit word in host byte order (little-endian on amd64).
    raw = b''.join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    return socket.inet_ntop(family, raw), int(port, 16)


class Connections(threading.Thread):
    """Sockets of the container's network namespace, sampled every 0.2 s (see the module docstring for limits)."""
    FILES = (('tcp', socket.AF_INET), ('tcp6', socket.AF_INET6), ('udp', socket.AF_INET), ('udp6', socket.AF_INET6))

    def __init__(self, interval=.2):
        super().__init__(daemon=True)
        self.interval, self.halt, self.seen, self.samples = interval, threading.Event(), {}, 0

    def run(self):
        while not self.halt.is_set():
            self.sample()
            self.halt.wait(self.interval)

    def sample(self):
        at = now()
        for name, family in self.FILES:
            try:
                lines = Path('/proc/net', name).read_text().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                fields = line.split()
                ip, port = address(fields[2], family)
                if port == 0:
                    continue
                local = address(fields[1], family)[1]
                if local == SERVICE_PORT:
                    continue
                value = ipaddress.ip_address(ip)
                if getattr(value, 'ipv4_mapped', None):
                    ip = str(value.ipv4_mapped)
                proto = name.rstrip('6')
                entry = self.seen.setdefault((proto, ip, port, local), dict(proto=proto, remote=ip, port=port,
                                                                             local_port=local, first_seen=at, states=[]))
                entry['last_seen'] = at
                state = TCP_STATES.get(fields[3], fields[3]) if proto == 'tcp' else 'UDP'
                if state not in entry['states']:
                    entry['states'].append(state)
        self.samples += 1

    def stop(self):
        self.halt.set()
        self.join(5)
        self.sample()
        rows = sorted(self.seen.values(), key=lambda e: e['first_seen'])
        flows = {}
        for row in rows:
            key = f"{row['proto']} {row['remote']}:{row['port']}"
            flows[key] = flows.get(key, 0) + 1
        return dict(method='/proc/net sample', interval_s=self.interval, samples=self.samples, sockets=rows,
                    by_destination=flows, names=None)


def run(folder):
    folder = Path(folder)
    spec = json.loads((folder / 'job.json').read_text(encoding='utf8'))
    provider = spec['provider']
    if provider not in ('sofascore', 'fixture-site'):
        raise SystemExit('Proveedor desconocido')
    monitor = Connections()
    monitor.start()

    from acquisition.sdk_adapter import SdkAdapter, configure_environment
    configure_environment(folder / 'sdk')  # before importing the SDK
    from acquisition import group_plan
    from acquisition.recipe import GroupScope, collect, collect_group, seed_cache
    from acquisition.transport import SdkTransport
    from persistencia.bundle import GROUP_RECIPE, PATHS, encode, parse
    from sofascore_scraper.store import Store

    baseline = parse((folder / 'inventory.json').read_bytes())
    if baseline['sha256'] != spec['inventory_sha256']:
        raise SystemExit('Inventario distinto del registrado para este trabajo')
    group = spec['recipe_id'] == GROUP_RECIPE
    # Simulated time exists only for the local provider: the plan's "now" and the transport's clock move together.
    offset = (spec['test_now'] - time.time()) if provider == 'fixture-site' and spec.get('test_now') else 0.0
    clock = lambda: time.time() + offset  # noqa: E731
    if group:
        from acquisition.operation_budget import OperationBudget
        operation_budget = OperationBudget(Path(spec['transport_root']).parent / 'operations',
                                           spec['operation_id'], spec['max_total_sends'])
        total_state = operation_budget.snapshot()
        if total_state['remaining'] <= group_plan.SEND_RESERVE:
            raise SystemExit('Manual operation has no safe send allowance for another batch')
        started = clock()  # one "now" for the rounds of the allowlist and the plan
        listed_at = group_plan.round_listings(spec['transport_root'])
        scope = GroupScope(sorted(set().union(*group_plan.rounds_to_list(baseline, started, listed_at=listed_at))))
        # A new batch can use only the operation's persisted remainder. Attempts are lowered further to leave
        # headroom for retries and browser challenge traffic; the adapter enforces the hard physical cap.
        sends = min(group_plan.MAX_SENDS, spec.get('max_sends') or group_plan.MAX_SENDS, total_state['remaining'])
        attempts_cap = min(group_plan.MAX_ATTEMPTS, spec.get('max_attempts') or group_plan.MAX_ATTEMPTS,
                           max(1, sends - group_plan.SEND_RESERVE))
        paths, seconds = scope.permits, group_plan.SECONDS
        limits = dict(allowed=scope.permits, limits='group', max_attempts=attempts_cap, seconds=seconds,
                      send_reserve=group_plan.SEND_RESERVE)
    else:
        operation_budget = None
        paths, sends, seconds = PATHS, MAX_SENDS, SECONDS
        limits = dict(max_attempts=len(PATHS), seconds=SECONDS, breaker=StrictBreaker())
    cancel = folder / 'cancel'
    profile = Path(spec['profile'])
    error, attempts, report, descriptor = None, [], None, None
    with closing(Store(folder / 'cache')) as store:
        seed_cache(store, baseline)
        site = None
        if provider == 'fixture-site':
            from sdk_fixture import FixtureSite, routes_from_file  # test images only
            site = FixtureSite(routes_from_file(spec['fixture_routes']), scenario=spec.get('fixture_scenario', 'challenge'))
            site.__enter__()
            adapter = SdkAdapter(profile, paths, api=site.api, site=site.origin, max_sends=sends,
                                 operation_budget=operation_budget, job_id=spec['job_id'],
                                 require_sandbox=True, local_test=True)
            urls = dict(loopback_url=site.api)
        else:
            adapter = SdkAdapter(profile, paths, max_sends=sends, operation_budget=operation_budget,
                                 job_id=spec['job_id'], require_sandbox=True)
            urls = dict(enable_real=True)
        adapter.install()
        try:
            with SdkTransport(Path(spec['transport_root']), adapter=adapter, job_id=spec['job_id'],
                              request_id=spec['request_id'], inventory_sha256=baseline['sha256'],
                              cancelled=cancel.exists, clock=clock, **limits, **urls) as transport:
                try:
                    output = folder / 'bundle.json'
                    if group:
                        budget = spec.get('test_planned_attempts') if provider == 'fixture-site' else None
                        descriptor = collect_group(store, transport, scope, baseline, output, now=started,
                                                   cancelled=cancel.exists, budget=budget, listed_at=listed_at,
                                                   limits=dict(attempts=attempts_cap, sends=sends),
                                                   detail_rounds=None, recent_round_count=spec['recent_round_count'],
                                                   max_total_sends=spec['max_total_sends'], operation_id=spec['operation_id'],
                                                   operation_budget=operation_budget)
                        again = lambda: collect_group(store, transport, scope, baseline, output, now=clock())  # noqa: E731
                    else:
                        descriptor = collect(store, transport, baseline, output, cancelled=cancel.exists)
                        again = lambda: collect(store, transport, baseline, output)  # noqa: E731
                    sends_done = len(adapter.sends)
                    assert again() == descriptor
                    assert len(adapter.sends) == sends_done  # the sealed bundle is re-verified without network
                except BaseException as e:
                    error = f'{type(e).__name__}: {e}'[:400]
                attempts = transport.state['attempts']
        finally:
            adapter.close()
            report = adapter.report()
            if site is not None:
                site.__exit__(None, None, None)
    connections = monitor.stop()
    operation_state = operation_budget.snapshot() if operation_budget is not None else None
    summary = dict(provider=provider, started_at=spec['started_at'], finished_at=now(), error=error,
                   cancelled=cancel.exists(), stopped=report['stopped'], descriptor=descriptor, attempts=attempts,
                   recipe=spec['recipe_id'],
                   comments_stop_after_sport=bool(group and transport.comments_stop_after_sport),
                   limits=dict(sends=sends, job_s=seconds, attempts=limits['max_attempts'],
                               detail_rounds=spec.get('detail_rounds') if group else None),
                   sends=report['sends'], requests=report['requests'], sandbox=report['sandbox'],
                   sessions=report['sessions'], launches=report['launches'], browser=report['browser'],
                   events=report['events'], connections=connections, operation_budget=operation_state,
                   budget_exhausted=bool(report['budget_exhausted']),
                   continuation_allowed=bool(group and may_continue_group(error=error, descriptor=descriptor,
                                                                          report=report,
                                                                          operation_state=operation_state)))
    with (folder / 'report.json').open('wb') as f:
        f.write(encode(summary))
        f.flush()
        os.fsync(f.fileno())
    safe_comments_stop=(group and descriptor is not None and error is None and transport.comments_stop_after_sport
                        and report['stopped']==transport.comments_stop_reason
                        and not report.get('processes_remaining'))
    if error is not None or (report['stopped'] is not None and not safe_comments_stop) or descriptor is None:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(run(sys.argv[1]))
