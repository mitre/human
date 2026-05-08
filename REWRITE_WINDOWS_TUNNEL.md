# Windows-Guest Zero-Outbound-TCP Tunnel Plan (CH v45.0)

Goal: sandcat in a Windows microVM beacons to `http://127.0.0.1:8888`; bytes
reach Caldera on the host with zero non-loopback packets visible to a
PCAP taken inside the guest. Substrate baked at template-build time only
(`/home/caldera/Desktop/CalderaVENV/caldera/plugins/range/automation/_data/windows-build/autounattend.xml`).
No runtime install.

## Critical CH-v45 finding (rules out Option A as stated)

`cloud-hypervisor --help` (binary at
`/home/caldera/Desktop/TimeStoneVENV/timestone/vendor/cloud-hypervisor/bin/cloud-hypervisor`,
v45.0.0) shows `--console`, `--serial`, and `--debug-console` are each
SINGULAR (no `...` repeat marker). Only `--serial` accepts
`socket=<path>`; `--console` accepts only `off|null|pty|tty|file=`. There
is no second virtio-serial port and no way to add one without patching
CH. `--fs`, `--net`, `--disk`, `--pmem`, `--device`, `--vsock` are the
only repeatable device flags. Option A as drafted is infeasible without
recompiling CH.

## Option-by-option

### A. Second virtio-serial port — INFEASIBLE on CH v45 (stock)
CH exposes exactly one virtio-console and one legacy serial. The
`vioserial.sys` driver CAN multiplex ports, but CH's device model
only instantiates one (see `vmm/src/device_manager.rs`; v45 has no
flag to add ports). Sub-option virtio-fs is plural but needs `winfsp`,
not in stock virtio-win.iso — violates "no runtime install." Scratch.

### B. Multiplex over the SAC channel — feasible but bandwidth-bound
SAC's CMD channels are the only Microsoft-blessed multi-channel path
on the existing `--serial socket=`. The broker
(`/home/caldera/Desktop/CalderaVENV/caldera/plugins/range/scripts/sac-broker.py`)
already holds the UDS open and serializes clients with a mutex. We'd
add a second logical channel: `cmd channel -si` to spawn a sibling
cmd, run a baked PowerShell that listens on TCP `127.0.0.1:8888`,
base64-frames each chunk to stdout with a sentinel (`<<TUN k seq>>...`),
and a host-side demuxer in the broker that re-keys frames to a TCP
socket toward Caldera 8888. **Throughput math:** SAC over CH's serial
is byte-streamed (no UART baud cap — limited by the virtio-serial pipe
plus SAC's own line discipline). Empirically SAC tops out at
~50–200 KB/s after base64 + framing overhead, with 50–200 ms RTT spikes
when SAC redraws its TUI. Sandcat beacons are typically <4 KB JSON
each, but file-staging (payloads) blows this up: a 5 MB sandcat exec
takes ~30–60 s. Also the TUI redraw bytes contaminate the framing —
we'd need a robust escape protocol. **AE-purity: 10/10.** Guest PCAP
sees only loopback. **Effort: 2–3 days** (framing protocol, broker
demuxer, PowerShell forwarder hardened against TUI noise, autounattend
service registration).

### C. Disk-based transport — feasible, slowest
A `contact_disk.py` modeled on
`/home/caldera/Desktop/CalderaVENV/caldera/app/contacts/contact_http.py`
(`Contact` class with `start()` + poll loop; cf. `contact_tcp.py`
operation_loop). Transport: a `--disk` raw image both sides poke at.
**Concurrent access is the killer**: NTFS/FAT aren't cluster-safe.
Workable variants: virtio-pmem with DAX, or two small disks flipped
via `ch-remote` add/remove (~1 s per flip). **Effort: 3–5 days**;
throughput ~10–50 KB/s. **AE-purity: 10/10.** Fall-back if B fails.

### D. Hyper-V hv-sock — RULED OUT
hv-sock requires the guest to be running in a Hyper-V root/child
partition speaking VMBus. Under KVM+CH the guest sees virtio devices,
not VMBus. Microsoft's `Hyper-V Sockets` doc
(learn.microsoft.com/.../make-integration-service) explicitly requires
the Hyper-V hypervisor. Confirmed irrelevant.

### E. Loopback-only virtio-net — fragile, leaky
A tap with `ip route` rules forcing only `127.0.0.0/8` reachable from
the guest doesn't help: the GUEST's stack still emits ARP, DHCP,
LLMNR, NetBIOS, and IPv6 RA solicits the moment netkvm comes up. PCAP
inside the guest WILL show non-loopback frames. **AE-purity: 2/10.**
Reject.

## Recommendation

**Tonight: do nothing on Windows; ship Linux first.** The Linux vsock
path (`smoke-ansible-vsock.sh` already exists in
`/home/caldera/Desktop/CalderaVENV/caldera/plugins/range/scripts/`)
proves the substrate end-to-end. Asymmetry is acceptable for one
sprint — the Caldera operator UI doesn't care which transport an
agent uses, and Windows victims today already have an
"outbound-TCP-via-tap" story that works for non-forensic scenarios.

**This week: implement Option B (SAC multiplex).** Reuse the broker we
already trust. Concrete deliverables:

1. **autounattend.xml** — add a `RunSynchronous` step that drops
   `C:\ProgramData\timestone\sac-tunnel.ps1` and registers it as a
   Windows service (`sc.exe create TsTunnel binPath=...`) bound to a
   second SAC CMD channel auto-spawned at boot via `sacsess`.
2. **sac-broker.py** — add a `--tunnel-bridge <uds>` flag; when set,
   the broker demuxes frames matching `^<<TUN ([0-9]+) ([0-9]+)>>$`
   from the byte stream and proxies them to a TCP socket on
   `127.0.0.1:8888` (host Caldera). Non-tunnel bytes flow to the
   existing Ansible-bridge UDS unchanged.
3. **Sandcat config** — bake `-server http://127.0.0.1:8888` into the
   sandcat binary planted at `C:\Windows\Temp\splunkd.exe` during
   `oobeSystem`.
4. **Smoke test** — `tcpdump -i any -n` inside the guest during a
   sandcat beacon run; assert zero non-loopback frames. Add as
   `smoke-zero-egress-windows.sh` next to `smoke-ansible-sac.sh`.

**Effort:** ~16 hours wall-clock for one engineer; not overnight-buildable
to production-quality but a working prototype IS overnight-buildable
if we accept frame-corruption-on-TUI-redraw as a known issue and
defer the robust escape protocol.

## Sources

- `/home/caldera/Desktop/TimeStoneVENV/timestone/vendor/cloud-hypervisor/bin/cloud-hypervisor --help`
- `/home/caldera/Desktop/CalderaVENV/caldera/plugins/range/scripts/sac-broker.py`
- `/home/caldera/Desktop/CalderaVENV/caldera/app/contacts/contact_http.py`,
  `contact_tcp.py`
- `/home/caldera/Desktop/CalderaVENV/caldera/plugins/range/automation/_data/windows-build/run-build-ch.sh`
  (`--serial file=`, `--console off` — confirms current single-channel use)
- CH issue tracker: cloud-hypervisor#3045 (request for multi-port
  virtio-console — open as of v45)
