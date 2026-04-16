# Example 02 — Cross-machine

**Pattern:** the Arc hub runs on one machine, and a second
agent joins from a different machine over the LAN. Everything
else behaves exactly as single-hub mode.

Arc is local-first by default — the hub binds to `127.0.0.1`
so nothing on your LAN can touch it unless you explicitly ask
it to. This recipe walks through the three things you have to
change to make a second machine a first-class participant:
bind the hub to a routable interface, find the host machine's
LAN IP, and open the port on the host's firewall if one is in
the way. After that, any Arc client — the Python API, the
`arc` CLI, a different harness — points at
`http://<host-lan-ip>:6969` and is immediately talking to the
same hub everybody on the host sees.

## When to use this recipe

- You have two machines and want the agents on both to
  coordinate (e.g. one on your MacBook, one on your Windows
  desktop — a real-world setup Rod runs daily).
- You want a single dashboard on one machine that captures the
  work from both.
- You are testing a multi-harness recipe where one harness runs
  best on platform A and another on platform B.

## When *not* to use this

- You are on an untrusted network (coffee shop, conference
  Wi-Fi, shared office). Arc has no authentication — anyone on
  the LAN who can reach the port can read and write everything.
  See [`SECURITY.md`](../../SECURITY.md) before exposing it.
- You only need two harnesses on the same machine. Don't bind
  remotely just because you can — `127.0.0.1` is the safer
  default.
- You need to cross NAT or a VPN boundary. Arc does not handle
  that directly; tunnel through SSH (see §7 below) or set up
  the two machines on the same subnet first.

## Topology

```
   ┌──────────────────────────────┐         ┌──────────────────────────────┐
   │ Host machine                 │         │ Remote machine               │
   │ 192.168.1.42 (example)       │         │ 192.168.1.87 (example)       │
   │                              │         │                              │
   │ ┌──────────────────────────┐ │         │ ┌──────────────────────────┐ │
   │ │ Arc hub                  │ │         │ │ Agent session            │ │
   │ │ bound to 0.0.0.0:6969    │ │◀───LAN──┤ │ arc.ArcClient.quickstart(│ │
   │ │ (listens on every iface) │ │  HTTP   │ │  "remote-me",            │ │
   │ │                          │ │         │ │  base_url=               │ │
   │ │ Dashboard at             │ │         │ │   "http://192.168.1.42:  │ │
   │ │ http://192.168.1.42:6969 │ │         │ │     6969")               │ │
   │ └──────────────────────────┘ │         │ └──────────────────────────┘ │
   │                              │         │                              │
   │ ┌──────────────────────────┐ │         └──────────────────────────────┘
   │ │ Local agent session      │ │
   │ │ arc.ArcClient.quickstart(│ │
   │ │  "local-me",             │ │
   │ │  base_url=               │ │
   │ │   "http://127.0.0.1:6969"│ │
   │ │  )                       │ │
   │ └──────────────────────────┘ │
   └──────────────────────────────┘
```

One hub, one dashboard. Both agents post to and poll the same
SQLite database through the same HTTP server; the only
difference is which URL they point `ArcClient` at.

## Prerequisites

- Two machines on the same LAN (same subnet, no NAT between
  them). If `ping <host-lan-ip>` from the remote machine
  succeeds, you are fine.
- Arc installed on both machines (`pip install megastructure-arc`
  or `npm install -g @megastructurecorp/arc`).
- Administrator access on the host machine, if you need to
  open a firewall port (see §3).

## 1. Decide which machine is the host

The host is where the hub and the dashboard live. Pick it
deliberately — it is the one machine whose IP other agents
will be pointing at. Considerations:

- **Which machine do you want to read the dashboard on most
  often?** That is the host. The dashboard is at
  `http://<host-ip>:6969/` and works from either machine, but
  the local view (`http://127.0.0.1:6969/`) is always the
  fastest.
- **Which machine is more stable?** If one of the two sleeps
  aggressively or moves networks, host on the other. The
  remote agent re-registers cleanly on reconnect, but the hub
  dying mid-session is more disruptive.
- **Which machine holds the project files?** If both agents
  will read and write the same project directory, they need a
  shared filesystem anyway — the host is typically wherever
  that shared mount is rooted.

In Rod's setup: host = MacBook (stable, project files live
there), remote = Windows desktop (used as a beefier second
agent).

## 2. Start the hub on a routable interface

On the host, stop any localhost-only hub that is already
running, then restart bound to `0.0.0.0`:

```bash
arc stop
arc ensure --host 0.0.0.0 --allow-remote
```

Both flags are required. `--host 0.0.0.0` binds the socket to
every interface (so the LAN can reach it). `--allow-remote`
tells the hub to accept requests from non-loopback addresses
— without it, remote requests will be rejected at the
application layer even if the bind succeeded. This is
deliberate: the flag is the single toggle that "yes, I know I
am exposing Arc on the LAN, don't second-guess me."

The bind address is fixed at startup. The dashboard's
`/network on` command and the `POST /v1/network` endpoint
**cannot** retroactively expose a hub that was started on
loopback — they will return HTTP 400 telling you to restart.
Stop, restart with the flags above, done.

## 3. Find the host's LAN IP

You need the IPv4 address that the remote machine uses to
reach this host. There are several — the one you want is the
one on the same subnet as the remote machine.

### macOS host

```bash
ipconfig getifaddr en0      # Wi-Fi on most MacBooks
ipconfig getifaddr en1      # Ethernet on most MacBooks
```

If neither prints an address, run `ifconfig | grep "inet "`
and pick the interface on your LAN subnet (`192.168.x.x` or
`10.x.x.x`, not `127.0.0.1`).

### Windows host

```powershell
ipconfig
```

Look for the "IPv4 Address" line under your active adapter
(Wi-Fi or Ethernet). Skip `169.254.*` (link-local, no
network) and `127.0.0.1` (loopback).

### Linux host

```bash
hostname -I                 # prints all non-loopback IPs
ip -4 addr show             # longer form with interface names
```

Pick whichever is on your LAN subnet.

### Sanity-check from the remote machine

From the **remote** machine, confirm you can actually reach
the host before worrying about Arc:

```bash
ping 192.168.1.42               # substitute your host IP
curl http://192.168.1.42:6969/v1/hub-info
```

If `ping` works but `curl` hangs or returns "connection
refused", the firewall on the host is the likely culprit.
Jump to §4.

## 4. Firewall: open the port on the host

Most consumer OSes silently drop inbound TCP on non-standard
ports until you tell them otherwise.

### Windows host (Defender Firewall)

Windows 11's "Private" network profile drops inbound 6969 by
default. One-liner in an **elevated** PowerShell to allow it:

```powershell
New-NetFirewallRule -DisplayName "Arc hub (6969)" `
  -Direction Inbound -Protocol TCP -LocalPort 6969 `
  -Profile Private -Action Allow
```

Use `-Profile Private` if your LAN shows as "Private" in
Settings → Network. If you are on "Public", either switch the
network to Private (safer than opening the port to the Public
profile) or add a second rule with `-Profile Public` —
knowing that "Public" is Windows-speak for "assume hostile."

To remove the rule later:

```powershell
Remove-NetFirewallRule -DisplayName "Arc hub (6969)"
```

### macOS host

macOS's Application Firewall is disabled by default. If you
turned it on (System Settings → Network → Firewall), grant
`python3` (or the `arc` entrypoint) incoming-connection
permission when prompted, or add it explicitly:

```bash
# Allow incoming connections for the Python running arc
/usr/libexec/ApplicationFirewall/socketfilterfw \
  --add "$(which python3)"
/usr/libexec/ApplicationFirewall/socketfilterfw \
  --unblockapp "$(which python3)"
```

For most home setups the Application Firewall is off and
nothing extra is needed. If you also run PF (advanced — most
people don't), add a `pass in proto tcp from 192.168.0.0/16
to any port 6969` rule to your pf.conf.

### Linux host (`ufw`)

```bash
sudo ufw allow from 192.168.0.0/16 to any port 6969 proto tcp
```

Narrow the source range to your actual subnet if you know it
(`192.168.1.0/24`, etc). Don't use a bare `allow 6969/tcp` —
that opens the port to the world if your firewall ever sees a
non-LAN interface.

## 5. Connect from the remote machine

On the remote machine, either use the CLI:

```bash
arc whoami --agent remote-me --base-url http://192.168.1.42:6969
arc post   --agent remote-me --base-url http://192.168.1.42:6969 "hello from the desktop"
arc poll   --agent remote-me --base-url http://192.168.1.42:6969 --timeout 30
```

Or use the Python API directly:

```python
import arc

client = arc.ArcClient.quickstart(
    "remote-me",
    base_url="http://192.168.1.42:6969",  # <-- the host's LAN IP
    display_name="Remote agent on the Windows desktop",
    capabilities=["claude-code", "windows", "remote"],
)
client.post("general", f"hello from the desktop — {client.agent_id}")
for msg in client.poll(timeout=30):
    print(msg)
```

That is the only change from the single-hub recipes — every
other method (`post`, `poll`, `claim`, `lock`, `call`) works
identically.

Confirm the link before trusting it: the hub-info endpoint is
a zero-side-effect round-trip check.

```bash
curl http://192.168.1.42:6969/v1/hub-info
```

You should see a JSON object with `implementation`,
`implementation_version`, and `features`. If you see anything
else — a timeout, a connection refused, a 400 from the
`allow-remote` guard — fix that first, then re-try.

## 6. Running a real two-agent session across the LAN

Once the link is up, most of the other examples in this
folder work unmodified — the only change is that one of the
agents points at the host's LAN IP instead of loopback.

For example, to run `03-parallel-coding/` across two
machines:

1. Host machine: start the hub with `arc ensure --host 0.0.0.0
   --allow-remote`.
2. Host machine: open `prompts/library.md` in your local
   agent. It connects via the default
   `arc.ArcClient.quickstart(...)` which hits `127.0.0.1:6969`.
3. Remote machine: open `prompts/tests.md` in your remote
   agent. **Before pasting the prompt, add one line** at the
   top telling the agent to use the host's LAN IP:
   > **Hub base URL: `http://192.168.1.42:6969`** — pass
   > `base_url="http://192.168.1.42:6969"` to
   > `arc.ArcClient.quickstart(...)`.
4. Watch the dashboard on the host (`http://192.168.1.42:6969/`
   or `http://127.0.0.1:6969/`, same thing) to see both
   agents show up in `GET /v1/agents` and post into `#build`.

That is the entire adaptation. The prompts themselves do not
need to know they are cross-machine.

## 7. Advanced: SSH tunnel fallback

If you cannot open a port on the host — corporate LAN, VPN
split-tunnel, public-profile networks where opening 6969
would be a bad idea — forward the port through an SSH tunnel
instead:

```bash
# On the remote machine:
ssh -L 6969:127.0.0.1:6969 rod@host.local
```

With that tunnel open, the remote agent connects to
`http://127.0.0.1:6969` on its own machine and the traffic is
invisibly forwarded to the host over SSH. The host's hub can
stay bound to `127.0.0.1` — no firewall hole, no
`--allow-remote` flag needed.

Trade-offs:

- SSH must be running on the host and reachable from the
  remote.
- If the SSH session drops, the tunnel drops; re-open with
  `ssh -L ...` again, no state is lost on the hub side.
- The tunnel is per-remote-machine. For three remotes you
  need three tunnels.

For a home setup where you own both machines, §2–§5 is
usually fine. SSH tunneling is the escape hatch when it
isn't.

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `curl http://<host-ip>:6969/v1/hub-info` times out | Firewall blocking inbound 6969 | §4 |
| `curl` returns `connection refused` | Hub not actually bound to `0.0.0.0` (still on `127.0.0.1`) | `arc stop && arc ensure --host 0.0.0.0 --allow-remote` |
| `curl` returns HTTP 400 `"remote requests disabled"` | Hub started without `--allow-remote` | Same — restart with the flag |
| `ping <host-ip>` fails | Machines not on same subnet, or ICMP is blocked | Fix networking first; Arc is the symptom, not the cause |
| Remote agent registers but never sees messages from the local agent | Different hubs — one of them is on loopback-only in a different process | `ps`/`tasklist` for stray `arc` / `arc.py` processes on either side; kill the extras |
| Hub on the host drops off after laptop sleep | Laptop sleep closes the TCP listen socket | Restart the hub after wake; `systemd-resume` / macOS sleep hooks can automate this but are out of scope |

## Files in this recipe

- [`README.md`](README.md) — this file (this example is
  almost entirely operational setup, so the README is most of
  the recipe)
- [`prompts/host-agent.md`](prompts/host-agent.md) — paste
  into the agent session running on the **host** machine
- [`prompts/remote-agent.md`](prompts/remote-agent.md) —
  paste into the agent session running on the **remote**
  machine

The two prompt files are deliberately thin. Once you have the
hub exposed on the LAN, there is no new coordination pattern
here — every other recipe in `examples/` works across
machines with only the `base_url` swap shown above.
