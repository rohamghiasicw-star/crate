"""Offline tests for SPEED FIX 2's studio check (origref.py), review 2026-10-07:
  D5  a check killed at its timeout leaves no audio on disk (the child, its ffmpeg / fpcalc and
      verify's own temp decodes all lived in a work dir that is removed on every path).
Synthetic audio only (numpy), made in a temp dir removed at exit; no network, no Shazam.
The D5 test needs ffmpeg and fpcalc on PATH and is skipped without them.
run: /usr/bin/python3 test_origref.py"""
import atexit
import os
import shutil
import sys
import tempfile
import time
import unittest
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fastorig as FO     # noqa: E402
import origref as OR      # noqa: E402

SR = 22050
TMP = tempfile.mkdtemp(prefix="origref_test.")
atexit.register(shutil.rmtree, TMP, True)


def song(seed=1, secs=30.0, sr=SR):
    """A synthetic track: kick / snare / hats, chords, bass and a lead, so it has transients,
    sustain, gaps and a full spectrum like music."""
    rng = np.random.default_rng(seed)
    n = int(secs * sr)
    x = np.zeros(n)
    beat = 60.0 / rng.uniform(85, 135)
    for i in range(int(secs / beat)):
        s = int(i * beat * sr)
        L = int(0.25 * sr)
        if s + L > n:
            break
        e = np.exp(-np.arange(L) / (0.05 * sr))
        x[s:s + L] += 0.6 * np.sin(2 * np.pi * (50 + 60 * e) * np.arange(L) / sr) * e
        if i % 2:
            x[s:s + L] += 0.25 * rng.standard_normal(L) * np.exp(-np.arange(L) / (0.03 * sr))
        hs = s + int(beat * sr / 2)
        if hs + 1000 < n:
            x[hs:hs + 1000] += 0.08 * rng.standard_normal(1000) * np.exp(-np.arange(1000) / 150.0)
    for j in range(int(secs / (beat * 4))):
        s = int(j * beat * 4 * sr)
        L = min(int(beat * 4 * sr), n - s)
        r = 110.0 * 2 ** (rng.choice([0, 3, 5, 7, 8, 10]) / 12.0)
        t = np.arange(L) / sr
        env = np.minimum(1, t / 0.02) * np.exp(-t / 1.5)
        for semi in (0, 4, 7, 12):
            for h in range(1, 6):
                x[s:s + L] += 0.03 / h * np.sin(2 * np.pi * 2 * r * 2 ** (semi / 12.0) * h * t) * env
        x[s:s + L] += 0.25 * np.sin(2 * np.pi * r / 2 * t) * np.exp(-t / 0.8)
        for q in range(8):
            ns, nl = s + int(q * beat / 2 * sr), int(beat / 2 * sr * 0.9)
            if ns + nl > n:
                break
            tn = np.arange(nl) / sr
            f = 4 * r * 2 ** (rng.choice([0, 2, 4, 7, 9, 12]) / 12.0)
            x[ns:ns + nl] += 0.06 * np.sin(2 * np.pi * f * tn) * np.minimum(1, tn / 0.01) * \
                np.exp(-tn / 0.25)
    return 0.5 * x / np.max(np.abs(x))


def _wav(x, path, sr=SR):
    y = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())


def _tools():
    return all(shutil.which(t) for t in ("ffmpeg", "fpcalc"))


@unittest.skipUnless(_tools(), "needs ffmpeg and fpcalc")
class NoAudioLeftD5(unittest.TestCase):
    def setUp(self):
        self.inputs = os.path.join(TMP, "in")
        os.makedirs(self.inputs, exist_ok=True)
        s = song(5, 40.0)
        self.clip = os.path.join(self.inputs, "clip.wav")
        self.pv = os.path.join(self.inputs, "preview.wav")
        _wav(s[int(6 * SR):int(26 * SR)], self.clip)
        _wav(s, self.pv)
        self.sys_tmp = tempfile.mkdtemp(dir=TMP, prefix="systmp.")
        self._env = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = self.sys_tmp      # what the child inherits as its system temp

    def tearDown(self):
        if self._env is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = self._env

    def _left(self, scan_tmp):
        return sorted(os.listdir(scan_tmp)) + sorted(os.listdir(self.sys_tmp))

    def test_unkilled_check_measures_and_leaves_nothing(self):
        scan_tmp = tempfile.mkdtemp(dir=TMP, prefix="scan.")
        t0 = time.time()
        out = OR.run(self.clip, self.pv, scan_tmp, "itunes", timeout=60)
        self.__class__.t_full = time.time() - t0
        self.assertTrue(out.get("aligned"), out)
        self.assertEqual(self._left(scan_tmp), [])

    def test_a_check_killed_at_any_point_leaves_no_audio(self):
        scan_tmp = tempfile.mkdtemp(dir=TMP, prefix="scan.")
        t0 = time.time()
        OR.run(self.clip, self.pv, scan_tmp, "itunes", timeout=60)
        full = max(0.3, time.time() - t0)
        timeouts = [full * f for f in np.linspace(0.02, 0.9, 24)]
        killed = 0
        for to in timeouts:
            out = OR.run(self.clip, self.pv, scan_tmp, "itunes", timeout=to)
            killed += out.get("why") == "worker_timeout"
            time.sleep(0.05)              # anything orphaned would still be writing now
            self.assertEqual(self._left(scan_tmp), [], ("timeout %.2f s" % to, out))
        self.assertGreaterEqual(killed, 6)  # the sweep really did kill it mid-measurement


if __name__ == "__main__":
    unittest.main(verbosity=1)
