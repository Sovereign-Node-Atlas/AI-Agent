#!/usr/bin/env python3
"""Blender Cycles: render the factory default cube at 64 samples with HIP, then with CPU (the mandatory fallback,
Section 15.2), recording which succeeded. Two modes in one file:

  outer (run by p4_run_test with the venv python): finds libamdhip64 inside the ROCm wheels (UNVERIFIED path
      _rocm_sdk_core/lib, research §3.14: it is searched for, not assumed), runs
      `blender -b --factory-startup --python THIS_FILE -- --bpy --device HIP|CPU --out DIR` twice, parses the
      BLENDER_RESULT line each prints, and emits P4RESULT.
  --bpy (run inside Blender's own Python): sets Cycles, 64 samples, 640x480, the device (HIP through the preferences
      API: compute_device_type='HIP', get_devices(), enable HIP devices — exit 2 when Blender lists none), renders
      to <out>/cube_<device>.png, prints BLENDER_RESULT {json}.
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
    info: dict[str, object] = {"device": device, "samples": samples}
    if device == "CPU":
        scene.cycles.device = "CPU"
    else:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = "HIP"
        prefs.get_devices()
        enabled = []
        for d in prefs.devices:
            d.use = d.type == "HIP"
            if d.use:
                enabled.append(d.name)
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


def _hip_lib_dirs() -> list[str]:
    """Directories inside the image's site-packages that hold libamdhip64.so* (research §3.14, UNVERIFIED path)."""
    import site

    dirs: list[str] = []
    roots = [Path(p) for p in site.getsitepackages()] + [Path(site.getusersitepackages())]
    for root in roots:
        if not root.is_dir():
            continue
        for lib in root.rglob("libamdhip64.so*"):
            d = str(lib.parent)
            if d not in dirs:
                dirs.append(d)
    return dirs


def outer_mode() -> None:
    from p4common import Test

    def main(t: Test) -> None:
        blender = t.setting("blender", "/srv/atlas/engines/blender/blender")
        if not os.access(blender, os.X_OK):
            t.fail(f"{blender} is not executable (the build step unpacks the tarball there)")
        libdirs = _hip_lib_dirs()
        env = dict(os.environ)
        if libdirs:
            env["LD_LIBRARY_PATH"] = ":".join([*libdirs, env.get("LD_LIBRARY_PATH", "")]).rstrip(":")
            t.note(f"HIP runtime dirs on LD_LIBRARY_PATH: {libdirs}")
        else:
            t.note("no libamdhip64.so found in site-packages: HIP will report no device (UNVERIFIED wheel layout)")
        results: dict[str, dict[str, object]] = {}
        for device in ("HIP", "CPU"):
            cmd = [blender, "-b", "--factory-startup", "--python", os.path.abspath(__file__), "--",
                   "--bpy", "--device", device, "--out", str(t.out), "--samples", t.setting("samples", "64")]
            t.note(f"running {device} render")
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env, check=False)
                tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
                line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("BLENDER_RESULT ")), None)
                if line:
                    rec = json.loads(line[len("BLENDER_RESULT "):])
                else:
                    rec = {"ok": False, "error": f"exit {proc.returncode}: {tail[-600:]}"}
            except subprocess.TimeoutExpired:
                rec = {"ok": False, "error": "render timed out after 1800 s (mid-render hang, Section 15.2 note)"}
            results[device] = rec
            verdict = "ok" if rec.get("ok") else "FAILED"
            t.note(f"{device}: {verdict} {rec.get('seconds', '')}s {rec.get('error', '')}"[:400])
            if device == "HIP":
                t.loaded(None if rec.get("ok") else "HIP failed; CPU fallback (mandatory) follows")
        (t.out / "blender_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        if not results["CPU"].get("ok"):
            t.fail("CPU fallback render failed too: " + str(results["CPU"].get("error")))
        hip_ok = bool(results["HIP"].get("ok"))
        out = results["HIP"]["file"] if hip_ok else results["CPU"]["file"]
        t.done(out, notes=f"HIP={'pass' if hip_ok else 'fail'} CPU=pass; blender {results['CPU'].get('blender')}")

    Test("blender-cycles", no_mmap=False).run(main)


if __name__ == "__main__":
    if "--bpy" in sys.argv:
        sys.exit(bpy_mode(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    outer_mode()
