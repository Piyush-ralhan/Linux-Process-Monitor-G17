# ProcVision Pro — G17 / B10 / P05

An interactive **Linux `/proc` process monitor** with two live interfaces: a `curses` terminal dashboard and a responsive read-only browser dashboard. Uses Python standard library only; no `psutil`.

## Start in Ubuntu WSL2

```bash
cd ~/ProcVision_G17
python3 procvision.py
```

Alternatively, from the folder where this downloaded file was extracted:

```bash
python3 procvision.py
```

Terminal shortcuts: **C** CPU sort, **M** memory sort, **P** PID sort, **/** search, **X** clear, **Space** pause, **Up/Down** scroll, **Q** quit. Resize the terminal to 72x18 or larger.

## Browser mode (recommended for the live visual demonstration)

```bash
python3 procvision.py --web
```

Open **http://localhost:8765** in Windows Chrome/Edge while the Ubuntu terminal is running. WSL2 usually forwards localhost automatically. Search/filter/sort update immediately; the page polls real Linux data once per second. Use Ctrl+C to stop.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Engineering notes

- CPU percentage is based on deltas of `/proc/stat` counters.
- Per-process CPU is calculated from deltas of `utime + stime` divided by `SC_CLK_TCK` and elapsed wall time; 100% represents approximately one fully utilized CPU core, so a multithreaded process can exceed 100%.
- PID reuse is handled by including process `starttime` in the sampling identity.
- Process RAM is resident set size from `/proc/[pid]/status` (VmRSS).
- Processes may exit between listing and reading; these races are handled.
- Browser mode binds to `127.0.0.1` and exposes **read-only** data; it cannot kill or alter processes.
- CPU values on the first sample are zero because there is no prior sample.
- WSL2 shows Linux/WSL processes, **not** all native Windows processes.

## Faculty demo

1. Explain the `/proc` pseudo-filesystem and process states.
2. Open terminal mode and demonstrate sorting, filtering, scrolling and pause.
3. Start web mode in a second terminal, open `http://localhost:8765` and demonstrate live charts and filtering.
4. Generate CPU activity with a safe controlled workload, compare before/after, and record actual measurements.
5. Compare the displayed statistics with Linux `top` / `ps` while discussing sampling differences.
6. Capture original screenshots for the report. Record each member's own GitHub contributions.

**This package is a development upgrade, not a complete faculty submission.** Report, citations, final experiments, signed contributions, presentation and group video must still be completed using verified evidence.
