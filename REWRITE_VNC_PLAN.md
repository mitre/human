# Remote-Display Plan for TimeStone Victim VMs

The user's question: *"is VNC best, or is there anything more lightweight? will it work with our shims and socket connections?"* Below are three concrete approaches with a recommendation.

---

## Approach A — Stock RDP on Windows + VNC on Linux  (RECOMMENDED)

**How.**
- **Windows:** No third-party install. Add an `Order=N` `RegEdit` + `RunSynchronous` block to `/home/caldera/Desktop/CalderaVENV/caldera/plugins/range/automation/_data/windows-build/autounattend.xml` that runs:
  - `Set-ItemProperty 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0`
  - `Enable-NetFirewallRule -DisplayGroup 'Remote Desktop'`
- **Linux:** Bake `tigervnc-standalone-server`, `x11vnc`, `openbox`, and `websockify` into the rootfs build under `/home/caldera/Desktop/TimeStoneVENV/timestone/scenarios/images/` (the rootfs build script writes `victim-rootfs.ext4`). Add a systemd unit that starts Xvnc on `:0` plus websockify on `:6080`.
- **Browser side:** noVNC for the Linux VM; FreeRDP-WASM, Apache Guacamole, or an iframed `mstsc-web` for the Windows VM.
- **Transport:** Standard TCP over `br-timestone`. Does **not** touch our timestone shims.

**Cost.** Windows: ~0 MB disk (RDP is already on disk), ~30 MB RAM when idle. Linux: ~25 MB disk added to rootfs, ~60 MB RAM when a session is connected.

**Pros.** Fastest to a working demo. AE-clean on Windows — RDP is a stock Windows component an AE would absolutely expect to find. Standard protocols, off-the-shelf JS clients, no custom code to maintain.

**Cons.** Requires the VM's TCP networking to be reachable from the host browser path; introduces an extra listening port on each guest. Linux side needs a small package set baked into the rootfs.

**Where it lives in the build pipeline.** `autounattend.xml` `RunSynchronous` block for Windows enable; `timestone/scenarios/images/` rootfs build for Linux daemons + websockify; host-side reverse proxy under the Caldera web UI.

---

## Approach B — Streaming over our existing shims (vsock + SAC broker)

**How.** A tiny in-VM screen-grab daemon JPEG-encodes frames and writes them to the same control plane our shims already carry:
- **Linux:** ~50 lines of Python using `mss` + `Pillow`, frames pushed over vsock — transported by `/home/caldera/Desktop/CalderaVENV/caldera/plugins/range/automation/lib/microvm_vsock.py`.
- **Windows:** ~80 lines of C# / PowerShell using `System.Drawing.Bitmap.Save(jpeg)`, base64-chunked through SAC — transported by `/home/caldera/Desktop/CalderaVENV/caldera/plugins/range/automation/lib/microvm_sac.py`.
- **Input injection:** Linux uses `xdotool` (baked in); Windows uses `[System.Windows.Forms.Cursor]::Position` plus `mouse_event` P/Invoke — i.e., the raw input path, not a synthesized protocol.
- **Browser side:** A small custom canvas viewer that decodes the JPEG stream coming back through the shim WebSocket. ~5–10 fps, 200–500 ms latency.

**Cost.** ~5 MB disk per guest (Pillow + mss on Linux; nothing extra on Windows — it's all in `System.Drawing`). ~40 MB RAM while streaming.

**Pros.** AE-pure: uses **only** the control plane we already trust. Zero new listeners on the guest, no new firewall holes. Works identically whether the VM has a NIC or not.

**Cons.** We own the streamer + viewer code. Lower fidelity than RDP/VNC. No off-the-shelf JS client — we write our own. Throughput is bounded by SAC framing on the Windows side.

**Where it lives in the build pipeline.** New daemon under `timestone/scenarios/images/` (Linux) and a new tracked stage in `autounattend.xml` (Windows). Viewer ships in the Caldera web UI alongside the existing shim WebSocket.

---

## Approach C — Pure VNC for both OSes (the original plan)

**How.** Bake TigerVNC into the Linux rootfs as in Approach A, and install **TightVNC** or **UltraVNC** on Windows via `autounattend.xml` `RunSynchronous` (silent MSI).

**Cost.** Linux same as A. Windows: ~10 MB disk for the installer, ~40 MB RAM, plus a third-party MSI on disk that an AE could fingerprint.

**Pros.** One protocol, one client (noVNC), symmetric across guests.

**Cons.** Adds a non-stock binary to Windows — slight AE-hygiene regression vs. Approach A's stock RDP. No real upside over A once the host already speaks RDP natively.

**Where it lives in the build pipeline.** Same files as A, but with an extra MSI side-loaded into the Windows `autounattend.iso`.

---

## Recommended Path

**Do Approach A first.** It is the fastest route to a working demo: stock RDP on Windows is essentially free (registry flip + firewall rule from our existing `autounattend.xml`), and the Linux side is a well-trodden noVNC + websockify recipe that drops cleanly into the rootfs build at `timestone/scenarios/images/`. Both pieces use the VM's normal TCP networking, leaving our shims (`microvm_vsock.py`, `microvm_sac.py`) untouched for their actual job — control-plane traffic.

**Keep Approach B as the AE-pure fallback.** If Approach A's extra listening ports or bandwidth ever bother us in a constrained scenario, we already know exactly where the streamer would slot in (`microvm_vsock.py` / `microvm_sac.py`), and the prototype is small enough to build in a day. This is the path to productionize if hygiene or stealth ever outranks fidelity.

**Skip Approach C.** It is strictly worse than A on Windows and identical to A on Linux.
