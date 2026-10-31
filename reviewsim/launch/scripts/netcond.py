#!/usr/bin/env python3
"""Network conditions for the device-launch lab, applied on the CI runner with sudo.

The iOS Simulator has no network stack of its own: the app's sockets are the runner's
sockets and its DNS goes through the runner's mDNSResponder. So a pf rule, a dummynet pipe
or an /etc/resolver file on the runner is exactly what the simulated device lives with.
Rules only ever name the engine hosts' IPs (or names), never "any", so the runner's own
link to GitHub is untouched.

Conditions (the lane brief numbers them 1-8; the last two are extra, for diagnosis):
  normal      1  nothing changed
  drop        2  every packet to the host silently dropped (pf "block drop")
  nxdomain    3  the host name answers NXDOMAIN (resolver stub)
  lat600      4  600 ms added to every round trip (dummynet delay on the way out)
  loss15      5  15 % of packets lost each way (dummynet plr, both directions)
  bw256       6  256 kbit/s each way (dummynet bw, both directions)
  drop25      7  like drop; the launch loop clears it 25 s after launch
  addify_nx   8  addify.app answers NXDOMAIN (today's real state, forced so it cannot drift)
  dnssilent   +  the host name's DNS query is never answered (resolver goes quiet)
  reset       +  TCP to the host is refused with RST, UDP with ICMP unreachable

The "guard" drops every packet to the LIVE server while a run targets the test box, so a
lab run can never reach live by any path (the app's directory lookup returns the live
address).

CLI:  netcond.py setup | apply <cond> <host>[,<host>...] [--guard ip,...] | clear [--guard ip,...]
      | probe <url> | teardown | show
"""
import json
import os
import re
import socket
import subprocess
import sys
import time

ANCHOR = "com.apple/addifysim"
STATE = os.environ.get("RS_NET_STATE", "/tmp/addifysim-net.json")
NX_PORT, SILENT_PORT = 5354, 5355
HERE = os.path.dirname(os.path.abspath(__file__))


def sh(args, check=False, input_text=None):
    p = subprocess.run(args, input=input_text, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} -> {p.returncode}: {p.stderr.strip()}")
    return p


def sudo(*args, input_text=None, check=False):
    return sh(["sudo", "-n", *args], check=check, input_text=input_text)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    with open(STATE, "w") as f:
        json.dump(st, f, indent=1)


def ip_from_sslip(host):
    m = re.match(r"^(\d+)-(\d+)-(\d+)-(\d+)\.sslip\.io$", host)
    return ".".join(m.groups()) if m else None


def resolve(host):
    """Real IPv4 addresses of a host, read before any condition is applied."""
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)})
    except socket.gaierror:
        return []


def host_ips(host, st):
    cache = st.setdefault("ips", {})
    if host not in cache:
        ips = resolve(host)
        enc = ip_from_sslip(host)
        if enc and enc not in ips:
            ips.append(enc)
        cache[host] = ips
        save_state(st)
    return cache[host]


def flush_dns():
    sudo("dscacheutil", "-flushcache")
    sudo("killall", "-HUP", "mDNSResponder")


def write_resolvers(names, port):
    for n in names:
        sudo("mkdir", "-p", "/etc/resolver")
        sudo("tee", f"/etc/resolver/{n}", input_text=f"nameserver 127.0.0.1\nport {port}\n")


def clear_resolvers(st):
    for n in st.get("resolver_files", []):
        sudo("rm", "-f", f"/etc/resolver/{n}")
    st["resolver_files"] = []


def setup():
    st = load_state()
    out = []
    pfconf = sh(["cat", "/etc/pf.conf"]).stdout
    out.append("pf.conf anchors: " + ", ".join(l.strip() for l in pfconf.splitlines() if "anchor" in l))
    r = sudo("pfctl", "-E")
    m = re.search(r"Token : (\d+)", r.stdout + r.stderr)
    st["pf_token"] = m.group(1) if m else None
    out.append(f"pfctl -E: rc={r.returncode} token={st['pf_token']} {r.stderr.strip()[-200:]}")
    logs = os.environ.get("RS_OUT", "/tmp")
    for mode, port in (("nx", NX_PORT), ("silent", SILENT_PORT)):
        f = open(os.path.join(logs, f"dnsstub-{mode}.log"), "a")
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "dnsstub.py"), mode, str(port)],
                             stdout=f, stderr=subprocess.STDOUT)
        st[f"stub_{mode}_pid"] = p.pid
        out.append(f"dns stub {mode} on 127.0.0.1:{port} pid {p.pid}")
    st["resolver_files"] = []
    save_state(st)
    time.sleep(0.5)
    print("\n".join(out))
    return st


def rules_for(cond, ips, guard, account=()):
    """pf anchor text and dnctl pipe configs for one condition.
    `account`: IPs that get an explicit "pass ... keep state" rule after every block rule, so
    `pfctl -ss` shows which engine hosts the app really opened connections to."""
    rules, pipes = [], []
    for g in guard:
        rules.append(f"block drop out quick inet from any to {g}")
    tgt = "{ " + ", ".join(ips) + " }" if ips else None
    if tgt and cond in ("drop", "drop25"):
        rules.append(f"block drop out quick inet from any to {tgt}")
    elif tgt and cond == "reset":
        rules.append(f"block return out quick inet proto tcp from any to {tgt}")
        rules.append(f"block return out quick inet proto udp from any to {tgt}")
    elif tgt and cond == "lat600":
        pipes.append(["1", "delay", "600"])
        rules.append(f"dummynet out quick inet from any to {tgt} pipe 1")
    elif tgt and cond == "loss15":
        pipes.append(["1", "plr", "0.15"])
        pipes.append(["2", "plr", "0.15"])
        rules.append(f"dummynet out quick inet from any to {tgt} pipe 1")
        rules.append(f"dummynet in quick inet from {tgt} to any pipe 2")
    elif tgt and cond == "bw256":
        pipes.append(["1", "bw", "256Kbit/s"])
        pipes.append(["2", "bw", "256Kbit/s"])
        rules.append(f"dummynet out quick inet from any to {tgt} pipe 1")
        rules.append(f"dummynet in quick inet from {tgt} to any pipe 2")
    acc = [a for a in account if a]
    if acc:
        rules.append("pass out quick inet from any to { " + ", ".join(acc) + " } keep state")
    # pf wants dummynet rules ahead of filter rules in one file
    rules.sort(key=lambda r: 0 if r.startswith("dummynet") else 1)
    return rules, pipes


def apply(cond, hosts, guard=(), account=()):
    """Set exactly one condition (plus the guard). Returns a dict describing what was set."""
    st = load_state()
    hosts = [h for h in hosts if h]
    ips = sorted({ip for h in hosts for ip in host_ips(h, st)})
    rules, pipes = rules_for(cond, ips, list(guard), list(account))
    sudo("dnctl", "-q", "flush")
    for p in pipes:
        sudo("dnctl", "pipe", p[0], "config", *p[1:])
    text = "\n".join(rules) + "\n"
    if rules:
        r = sudo("pfctl", "-a", ANCHOR, "-f", "-", input_text=text)
        if r.returncode != 0:
            raise RuntimeError(f"pfctl load failed for {cond}: {r.stderr.strip()}")
    else:
        sudo("pfctl", "-a", ANCHOR, "-F", "all")
    # Drop live connections to the hosts so a rule is not bypassed by an existing state.
    for ip in sorted(set(ips) | set(guard) | set(account)):
        sudo("pfctl", "-k", "0.0.0.0/0", "-k", ip)
    clear_resolvers(st)
    names = []
    if cond == "nxdomain":
        names = hosts
        write_resolvers(names, NX_PORT)
    elif cond == "dnssilent":
        names = hosts
        write_resolvers(names, SILENT_PORT)
    elif cond == "addify_nx":
        names = ["addify.app"]
        write_resolvers(names, NX_PORT)
    st["resolver_files"] = names
    st["cond"] = cond
    save_state(st)
    flush_dns()
    return {"cond": cond, "hosts": hosts, "ips": ips, "guard": list(guard), "pf": rules,
            "pipes": [" ".join(p) for p in pipes], "resolver": names}


def clear(guard=(), account=()):
    return apply("normal", [], guard, account)


def show():
    r1 = sudo("pfctl", "-a", ANCHOR, "-sr")
    r2 = sudo("pfctl", "-a", ANCHOR, "-sd")
    r3 = sudo("dnctl", "list")
    return f"filter:\n{r1.stdout}{r1.stderr}\ndummynet:\n{r2.stdout}{r2.stderr}\npipes:\n{r3.stdout[:800]}"


def probe(url, timeout=12):
    """One curl from the runner, timed. Same resolver and packet path as the simulator."""
    fmt = "%{http_code} dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} total=%{time_total} bytes=%{size_download}"
    t = time.time()
    p = sh(["curl", "-sS", "-o", "/dev/null", "-m", str(timeout), "-H", "Accept-Encoding: gzip", "-w", fmt, url])
    return {"url": url, "rc": p.returncode, "w": p.stdout.strip(), "err": p.stderr.strip()[-160:],
            "wall": round(time.time() - t, 2)}


def teardown():
    st = load_state()
    sudo("pfctl", "-a", ANCHOR, "-F", "all")
    sudo("dnctl", "-q", "flush")
    clear_resolvers(st)
    for k in ("stub_nx_pid", "stub_silent_pid"):
        if st.get(k):
            try:
                os.kill(int(st[k]), 15)
            except Exception:
                pass
    if st.get("pf_token"):
        sudo("pfctl", "-X", st["pf_token"])
    flush_dns()
    save_state(st)
    print("network conditions torn down")


def main(argv):
    cmd = argv[1]
    guard = []
    if "--guard" in argv:
        i = argv.index("--guard")
        guard = [g for g in argv[i + 1].split(",") if g]
        argv = argv[:i] + argv[i + 2:]
    if cmd == "setup":
        setup()
    elif cmd == "apply":
        print(json.dumps(apply(argv[2], argv[3].split(",") if len(argv) > 3 else [], guard)))
    elif cmd == "clear":
        print(json.dumps(clear(guard)))
    elif cmd == "probe":
        print(json.dumps(probe(argv[2])))
    elif cmd == "show":
        print(show())
    elif cmd == "teardown":
        teardown()
    else:
        sys.exit(f"unknown command {cmd}")


if __name__ == "__main__":
    main(sys.argv)
