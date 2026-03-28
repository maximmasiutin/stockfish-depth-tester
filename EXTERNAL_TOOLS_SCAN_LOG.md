# External Tools Scan Log

## 2026-03-28

### bandit 1.9.2
- Start: 2026-03-28 ~18:58 UTC
- Status: Completed
- Summary: 8 findings in measure_depth_at_tc.py. LOW: 1x B404 (subprocess import), 2x B603 (subprocess.run/Popen without shell=True), 2x B607 (partial executable path: wmic, sysctl), 1x B311 (standard PRNG random.Random). No HIGH or MEDIUM findings.

### gitleaks 8.30.0
- Start: 2026-03-28 ~19:02 UTC
- Status: Completed
- Summary: 0 findings. 18.56 MB scanned (filesystem), 13 commits scanned (git history). No secrets detected.

### trivy 0.69.3 (fs scan)
- Start: 2026-03-28 ~19:02 UTC
- Status: Completed
- Summary: 0 findings. No secrets, vulnerabilities, or misconfigurations detected.

### snyk 1.1301.2 (code test)
- Start: 2026-03-28 ~19:03 UTC
- Status: Completed
- Summary: 9 MEDIUM findings in measure_depth_at_tc.py. 6 Command Injection (lines 181, 182, 215, 216, 270, 536 -- unsanitized CLI args into subprocess.Popen), 3 Path Traversal (lines 367, 368, 477 -- unsanitized CLI args into open() for write).
