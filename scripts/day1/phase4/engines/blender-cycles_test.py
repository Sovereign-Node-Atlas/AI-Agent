#!/usr/bin/env python3
"""Blender Cycles: render the factory default cube at 64 samples with HIP, then with CPU (the mandatory fallback,
Section 15.2), recording which succeeded. Two modes in one file:

  outer (run by p4_run_test with the venv python): finds libamdhip64 inside the ROCm wheels (UNVERIFIED path
      _rocm_sdk_core/lib, research §3.14: it is searched for, not assumed). Blender's HIP loader (hipew) dlopens the
      UNVERSIONED name libamdhip64.so only, and the wheel is expected to ship libamdhip64.so.7 (research §1.2), so
      when only versioned files are found a directory under --out gets a `libamdhip64.so -> <that file>` symlink and
      goes first on LD_LIBRARY_PATH (fix round 3; the notes name the real file linked). Then runs
      `blender -b --offline-mode --factory-startup --python-exit-code 1 --python THIS_FILE -- --bpy --device HIP|CPU
      --out DIR` twice, parses the BLENDER_RESULT line each prints, and emits P4RESULT. --python-exit-code 1: without
      it Blender exits 0 after a Python exception in the script and the real reason would be lost. There is no weight
      load, so the load watchdog is disarmed as soon as the binary is found (a hung HIP render must NOT kill the
      process before the CPU fallback: each render has its own subprocess timeout, capped below the watchdog).
  --bpy (run inside Blender's own Python): sets Cycles, 64 samples, 640x480, the device (HIP through the preferences
      API: compute_device_type='HIP', get_devices(), enable HIP devices — exit 2 with a BLENDER_RESULT line when
      Blender lists none or refuses the HIP enum, which is what happens when libamdhip64 is not loadable), renders
      to <out>/cube_<device>.png, prints BLENDER_RESULT {json} including bpy.app.online_access (must be False:
      --offline-mode, Blender >= 4.2; rule §7.1).
Pass = CPU render produced a PNG; HIP result goes in the notes either way."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def bpy_mode(argv: list[str]) -> int:
    import bpy  # type: ignore[import-not-found]  # Blender's own interpreter

    device = argv[argv.index("--device") + 1]
    out = Path(argv[argv.index("--out") + 1])
    samples = int(argv[argv.index("--samples") + 1]) if "--samples" in argv else 64
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.render.resolution_x, scene.render.resolution_y = 640, 480
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(out / f"cube_{device.lower()}.png")
    online = bool(getattr(bpy.app, "online_access", False))
    info: dict[str, object] = {"device": device, "samples": samples, "online_access": online}
    if online:
        print("BLENDER_RESULT " + json.dumps({**info, "ok": False, "error": "bpy.app.online_access is True: "
              "--offline-mode was not honoured (Blender < 4.2?); rule §7.1"}), flush=True)
        return 3
    if device == "CPU":
        scene.cycles.device = "CPU"
    else:
        # `compute_device_type = "HIP"` raises TypeError when HIP is not an available enum item (no libamdhip64
        # loadable): report that as the reason instead of letting Blender exit on the traceback (fix round 3).
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            available = [item.identifier for item in prefs.bl_rna.properties["compute_device_type"].enum_items]
            info["compute_device_types"] = available
            prefs.compute_device_type = "HIP"
            prefs.get_devices()
            enabled = []
            for d in prefs.devices:
                d.use = d.type == "HIP"
                if d.use:
                    enabled.append(d.name)
        except Exception as exc:
            print("BLENDER_RESULT " + json.dumps({**info, "ok": False, "error": "HIP is not selectable in this "
                  f"Blender/runtime combination: {type(exc).__name__}: {exc} (libamdhip64 not loadable, or the HIP "
                  "fatbins do not cover gfx1151 on this runtime)"}), flush=True)
            return 2
        info["hip_devices"] = enabled
        if not enabled:
            print("BLENDER_RESULT " + json.dumps({**info, "ok": False, "error": "Blender lists no HIP device "
                  "(libamdhip64 not loadable, or the HIP fatbins do not cover gfx1151 on this runtime)"}), flush=True)
            return 2
        scene.cycles.device = "GPU"
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    ok = os.path.isfile(scene.render.filepath) and os.path.getsize(scene.render.filepath) > 0
    info.update({"ok": ok, "seconds": round(time.time() - t0, 1), "file": scene.render.filepath,
                 "blender": bpy.app.version_string})
    print("BLENDER_RESULT " + json.dumps(info), flush=True)
    return 0 if ok else 1


def _hip_lib_dirs(shim_dir: Path) -> tuple[list[str], str | None]:
    """Directories to put on LD_LIBRARY_PATH for Blender's HIP loader (research §3.14, UNVERIFIED wheel path): the
    directories inside the image's site-packages that hold libamdhip64.so*, preceded, when none of them has the
    unversioned libamdhip64.so that hipew dlopens, by `shim_dir` holding a symlink of that name to the newest
    versioned file found. Returns (dirs, linked-target or None)."""
    import re
    import site

    dirs: list[str] = []
    found: list[Path] = []
    roots = [Path(p) for p in site.getsitepackages()] + [Path(site.getusersitepackages())]
    for root in roots:
        if not root.is_dir():
            continue
        for lib in root.rglob("libamdhip64.so*"):
            found.append(lib)
            d = str(lib.parent)
            if d not in dirs:
                dirs.append(d)
    if not found or any(lib.name == "libamdhip64.so" for lib in found):
        return dirs, None

    def version_key(p: Path) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", p.name[len("libamdhip64.so"):]))

    target = sorted(found, key=version_key)[-1]
    shim_dir.mkdir(parents=True, exist_ok=True)
    link = shim_dir / "libamdhip64.so"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)
    return [str(shim_dir), *dirs], str(target)


def outer_mode() -> None:
    from p4common import Test

    def main(t: Test) -> None:
        blender = t.setting("blender", "/srv/atlas/engines/blender/blender")
        if not os.access(blender, os.X_OK):
            t.fail(f"{blender} is not executable (the build step unpacks the tarball there)")
        # No model load to watch: disarm the watchdog now so a hung HIP render never pre-empts the CPU fallback.
        t.loaded("blender binary present; no weight load")
        # Per-render cap: 1800 s, but never beyond the load-timeout budget the driver gave this test.
        render_timeout = max(120, min(1800, int(t.args.load_timeout) - 30))
        libdirs, linked = _hip_lib_dirs(t.out / "hip-shim")
        env = dict(os.environ)
        if libdirs:
            env["LD_LIBRARY_PATH"] = ":".join([*libdirs, env.get("LD_LIBRARY_PATH", "")]).rstrip(":")
            t.note(f"HIP runtime dirs on LD_LIBRARY_PATH: {libdirs}")
            if linked:
                t.note(f"unversioned libamdhip64.so symlinked to {linked} (hipew dlopens the unversioned name only)")
        else:
            t.note("no libamdhip64.so* found in site-packages: HIP will report no device (UNVERIFIED wheel layout)")
        results: dict[str, dict[str, object]] = {}
        for device in ("HIP", "CPU"):
            cmd = [blender, "-b", "--offline-mode", "--factory-startup", "--python-exit-code", "1",
                   "--python", os.path.abspath(__file__), "--",
                   "--bpy", "--device", device, "--out", str(t.out), "--samples", t.setting("samples", "64")]
            t.note(f"running {device} render (timeout {render_timeout} s)")
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=render_timeout, env=env,
                                      check=False)
                tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
                line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("BLENDER_RESULT ")), None)
                if line:
                    rec = json.loads(line[len("BLENDER_RESULT "):])
                else:
                    rec = {"ok": False, "error": f"exit {proc.returncode}: {tail[-600:]}"}
            except subprocess.TimeoutExpired:
                rec = {"ok": False, "error": f"render timed out after {render_timeout} s (mid-render hang, "
                                            "Section 15.2 note)"}
            results[device] = rec
            verdict = "ok" if rec.get("ok") else "FAILED"
            t.note(f"{device}: {verdict} {rec.get('seconds', '')}s {rec.get('error', '')}"[:400])
            if device == "HIP" and not rec.get("ok"):
                t.note("HIP failed; CPU fallback (mandatory) follows")
        (t.out / "blender_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        if not results["CPU"].get("ok"):
            t.fail("CPU fallback render failed too: " + str(results["CPU"].get("error")))
        hip_ok = bool(results["HIP"].get("ok"))
        out = results["HIP"]["file"] if hip_ok else results["CPU"]["file"]
        t.done(out, notes=f"HIP={'pass' if hip_ok else 'fail'} CPU=pass; blender {results['CPU'].get('blender')}; "
                          f"online_access={results['CPU'].get('online_access')}")

    Test("blender-cycles", no_mmap=False).run(main)


if __name__ == "__main__":
    if "--bpy" in sys.argv:
        sys.exit(bpy_mode(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    outer_mode()
