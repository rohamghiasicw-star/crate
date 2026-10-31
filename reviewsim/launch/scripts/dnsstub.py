#!/usr/bin/env python3
"""Tiny DNS stub for the device-launch lab. Runs on the CI runner only.

  dnsstub.py nx <port>       answer every query with NXDOMAIN (RCODE 3, no answers)
  dnsstub.py silent <port>   read every query and never answer (a resolver that went quiet)

macOS sends queries for one name to this stub when /etc/resolver/<name> says
"nameserver 127.0.0.1" and "port <port>" (netcond.py writes that file). Nothing else on
the runner is affected. Every query is logged with the time, so the job log proves which
names the app asked for and when.
"""
import socket
import struct
import sys
import time


def qname(pkt, off=12):
    labels = []
    while off < len(pkt):
        n = pkt[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0:  # compression never appears in a question, but do not loop on it
            off += 2
            break
        labels.append(pkt[off + 1: off + 1 + n].decode("ascii", "replace"))
        off += 1 + n
    qtype = struct.unpack("!H", pkt[off: off + 2])[0] if off + 2 <= len(pkt) else 0
    return ".".join(labels), qtype, off + 4


def main():
    mode, port = sys.argv[1], int(sys.argv[2])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    print(f"dnsstub {mode} listening on 127.0.0.1:{port}", flush=True)
    while True:
        pkt, addr = s.recvfrom(4096)
        if len(pkt) < 12:
            continue
        name, qtype, qend = qname(pkt)
        print(f"{time.time():.3f} {mode} q={name} type={qtype} from={addr[1]}", flush=True)
        if mode != "nx":
            continue
        tid, flags = struct.unpack("!HH", pkt[:4])
        rd = flags & 0x0100
        # QR=1, opcode copied (0), AA=1, RD copied, RA=1, RCODE=3 (NXDOMAIN)
        rflags = 0x8000 | 0x0400 | rd | 0x0080 | 0x0003
        header = struct.pack("!HHHHHH", tid, rflags, 1, 0, 0, 0)
        s.sendto(header + pkt[12:qend], addr)


if __name__ == "__main__":
    main()
