#!/usr/bin/env python3
"""Keep fuzz output private on a public repo's CI.

Between jobs of one run: a per-run Fernet key (kept in the Actions cache, never in an artifact
or a log). For the final bundle: a fresh Fernet key wrapped with the lane's RSA public key
(fuzz-pub.pem), so only the private key holder can open it.

  seal.py newkey KEYFILE
  seal.py enc KEYFILE SRC_DIR OUT.bin        tar.gz + Fernet
  seal.py dec KEYFILE IN.bin OUT_DIR
  seal.py seal PUB.pem SRC_DIR OUT_DIR       writes OUT_DIR/bundle.bin + OUT_DIR/bundle.key
  seal.py unseal PRIV.pem IN_DIR OUT_DIR
  seal.py wrap PUB.pem KEYFILE OUT.key       wrap the run key so the owner can open the per-job blobs too
"""
import base64, io, os, sys, tarfile
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def tar_bytes(src):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        t.add(src, arcname=".")
    return buf.getvalue()


def untar(data, out):
    Path(out).mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        for m in t.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts or m.issym() or m.islnk():
                raise SystemExit("unsafe member " + m.name)
        t.extractall(out)


def main():
    cmd = sys.argv[1]
    if cmd == "newkey":
        Path(sys.argv[2]).write_bytes(Fernet.generate_key())
    elif cmd == "enc":
        k = Path(sys.argv[2]).read_bytes().strip()
        Path(sys.argv[4]).write_bytes(Fernet(k).encrypt(tar_bytes(sys.argv[3])))
    elif cmd == "dec":
        k = Path(sys.argv[2]).read_bytes().strip()
        untar(Fernet(k).decrypt(Path(sys.argv[3]).read_bytes()), sys.argv[4])
    elif cmd == "seal":
        pub = serialization.load_pem_public_key(Path(sys.argv[2]).read_bytes())
        k = Fernet.generate_key()
        out = Path(sys.argv[4])
        out.mkdir(parents=True, exist_ok=True)
        (out / "bundle.bin").write_bytes(Fernet(k).encrypt(tar_bytes(sys.argv[3])))
        (out / "bundle.key").write_bytes(base64.b64encode(pub.encrypt(k, OAEP)))
    elif cmd == "unseal":
        priv = serialization.load_pem_private_key(Path(sys.argv[2]).read_bytes(), password=None)
        d = Path(sys.argv[3])
        k = priv.decrypt(base64.b64decode((d / "bundle.key").read_bytes()), OAEP)
        untar(Fernet(k).decrypt((d / "bundle.bin").read_bytes()), sys.argv[4])
    elif cmd == "wrap":
        pub = serialization.load_pem_public_key(Path(sys.argv[2]).read_bytes())
        Path(sys.argv[4]).write_bytes(base64.b64encode(pub.encrypt(Path(sys.argv[3]).read_bytes().strip(), OAEP)))
    else:
        raise SystemExit("unknown command")


if __name__ == "__main__":
    main()
