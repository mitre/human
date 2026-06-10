# Design rule: no hardcoded environment assumptions

**Features must be environment-agnostic.** This plugin is cloned and run on
machines, hypervisors, networks, and accounts that differ from wherever it was
developed. A value that "works here" — an IP, hostname, path, node name, port,
username, credential, or product-specific label — will silently break or mislead
somewhere else. Never bake the dev environment into a feature.

## Why

The same code runs against different hypervisors/hosts, different LANs, different
operator accounts, and different filesystem layouts. Hardcoding a deployment-
specific value turns "configurable" into "only works on one box."

## How to apply

- **Derive / discover from the live environment** instead of hardcoding. Query
  the target's own API/host for the real values (nodes, storages, bridges,
  templates, next id, the host's SSH key, the server's own URL) rather than
  assuming them.
- **Require only what cannot be derived** — typically the remote *target* and its
  *credential* (e.g. an endpoint + an API token). Everything else should be
  discovered from those credentials or defaulted with a sensible fallback. Ask:
  *"can the credentials we already have find this for us?"* — usually yes.
- **Defaults live in exactly one place** (the backend), and a feature **fills
  only absent values** — an explicit user/config value always wins (the override
  escape hatch). Don't restate a default in three places (value + placeholder +
  comparison); they drift.
- **In UIs, show defaults as placeholders (hints), never as pre-filled values.**
  A filled-in value reads as "this is correct for you"; a placeholder reads as
  "leave blank for this default." Send only operator-typed overrides.
- **Keep copy and examples generic.** No instance-specific names (a particular
  IP, token, realm, or hostname) and no product-menu breadcrumbs. Prefer
  `<user>@<realm>!<tokenid>`, `host or https://host:8006`, `My Profile`.
- **Tolerate input variation.** Normalize what the operator pastes (missing
  scheme, missing port, extra whitespace) instead of failing on a strict format.

## Before you merge — checklist

- [ ] No literal IPs, hostnames, FQDNs, MACs, or absolute paths tied to one host.
- [ ] No hardcoded credentials, tokens, realms, node/cluster/datastore names.
- [ ] Every default is derived, discovered, or a clearly-overridable fallback.
- [ ] UI shows defaults as placeholders, not asserted values; only overrides are sent.
- [ ] Copy/examples are generic (no instance names, no product breadcrumbs).
- [ ] Inputs are normalized (scheme/port/whitespace) rather than strictly required.

> If a value genuinely must be supplied, make it a profile/config field with a
> documented default and a clear error when it's missing — never a constant in code.
