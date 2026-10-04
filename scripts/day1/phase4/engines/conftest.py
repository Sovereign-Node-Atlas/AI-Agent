"""pytest never collects this directory's `<key>_test.py` files: they are the Phase 4 engine tests that run INSIDE the
ROCm container (phase4/lib-engine.sh p4_run_test), not pytest tests, and the dotted names (wan2.2_test.py) are not
importable modules. pytest's default `python_files` matches *_test.py, so `pytest scripts/day1` from the repository
root would otherwise fail collection here (fix round 2). The unit tests for p4common.py live in phase4/tests/."""

collect_ignore_glob = ["*_test.py"]
