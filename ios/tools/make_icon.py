#!/usr/bin/env python3
"""Placeholder app icon: a solid #5B4BE8 1024x1024 PNG written with the stdlib only.

actool is not on the build Mac, so nothing here compiles the asset catalog; this just
gives Xcode a valid file to pick up. Replace it with a rasterised /icon.svg (server.py
serves the real purple wave) before any TestFlight build:
    rsvg-convert -w 1024 -h 1024 icon.svg > ios/Addify/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png
App Store icons must be opaque (no alpha), which is why this writes RGB, not RGBA."""
import struct, sys, zlib

def png(path, w, h, rgb):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    row = b"\x00" + bytes(rgb) * w
    raw = row * h
    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(raw, 9))
    out += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(out)

if __name__ == "__main__":
    dest = sys.argv[1] if len(sys.argv) > 1 else "AppIcon-1024.png"
    png(dest, 1024, 1024, (0x5B, 0x4B, 0xE8))
    print("wrote", dest)
