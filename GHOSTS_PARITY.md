# GHOSTS Parity & Roadmap

Branch: `timestone-human-rewrite` | Path: `/home/caldera/Desktop/HumanVENV/human/`
Sources: https://github.com/cmu-sei/GHOSTS, https://cmu-sei.github.io/GHOSTS/,
https://github.com/cmu-sei/GHOSTS/tree/master/src/Ghosts.Client.Universal/Handlers

## 1. Current state

Abilities (6): `open-email-webmail`, `create-document-notepad`,
`browse-web-news`, `click-random-links`, `idle-think-time`,
`open-file-explorer`. Adversaries (1): `office-worker`.

## 2. GHOSTS handler catalog (Universal `Handlers/` + docs)

`Bash, Cmd, PowerShell, ExecuteFile, Curl, Ftp, Sftp, Ssh, Rdp, Reboot, Wmi,
Notepad, Print, Clicks, Watcher, Aws, Azure, BrowserChrome, BrowserFirefox,
BrowserCrawl, BlogHelperDrupal, SharepointHelper, SocialHelper, Pidgin,
Outlook(+v2), Word, Excel, PowerPoint, NpcSystem`. ~38 handlers total.

### Gap analysis vs. our 6 abilities

| Category | GHOSTS has | We have | Gap |
|---|---|---|---|
| Web/browse | tabs, dwell, scroll, multi-tab, downloads, crawl | news + click | dwell/tab/download |
| Email | read, reply, attach, send-coworkers (Outlook v2) | webmail open | reply/send/attach |
| Office docs | Word, Excel, PPT, OneNote-via-Word | Notepad only | full Office suite |
| File ops | watcher, copy, ExecuteFile | open Explorer | copy/move/delete/search |
| Comms | Pidgin IM, Outlook calendar | none | Slack/Teams/calendar |
| Social | SocialHelper, Blog/Drupal, SharePoint | none | low priority for AE |
| Network | SSH, SFTP, FTP, RDP, Curl, AWS, Azure | none | SSH/RDP/curl |
| System | Print, Reboot, WMI, Clicks | idle | print/lock/screenshot |
| Schedule | per-handler hour-of-day, idle range, loop | none | needs glue |

## 3. Recommended next 10 abilities (priority order)

All implementable in pure shell/PowerShell using stock OS tools. Names follow
`<verb>-<noun>` pattern.

1. **`reply-email-outlook`** - `Start-Process "outlook:"` then PowerShell
   `SendKeys` Ctrl+R, type a canned line, Ctrl+Enter; Linux fallback
   `xdg-open mailto:coworker@corp.local?subject=...&body=...`. Highest-value
   gap (GHOSTS' Outlook handler is its flagship).
2. **`open-document-word`** - Win: `Start-Process winword` or
   `Start-Process "ms-word:"`; Linux: `libreoffice --writer` if present, else
   `xdg-open` of a `.docx` template under `%TEMP%`.
3. **`open-spreadsheet-excel`** - Same pattern as Word; `Start-Process excel`
   or `libreoffice --calc`. Drops file under TEMP, opens it, sleeps.
4. **`copy-file-documents`** - Win: `Copy-Item` between user's Documents and
   a temp folder; Linux: `cp` between `~/Documents` and `/tmp`. Pure shell.
5. **`search-files-explorer`** - Win: `Get-ChildItem -Recurse -Filter *.docx
   $env:USERPROFILE\Documents | Select -First 20`; Linux: `find ~ -name '*.pdf'
   2>/dev/null | head`. Generates filesystem read noise.
6. **`download-file-curl`** - `Invoke-WebRequest` / `curl -o` a small static
   asset (favicon, robots.txt) from a benign list. Models GHOSTS' `Curl`
   handler and creates HTTP egress.
7. **`open-multiple-tabs-browser`** - Loops `Start-Process msedge $url` 3-5
   times with sleeps; Linux uses `xdg-open`. Closest to GHOSTS multi-tab
   browsing. Extends our `browse-web-news`.
8. **`lock-screen-idle`** - Win: `rundll32.exe user32.dll,LockWorkStation`;
   Linux: `loginctl lock-session` or `xdg-screensaver lock`. Models
   lunch/away behavior.
9. **`print-document-spooler`** - Win: `Start-Process -FilePath file.txt
   -Verb Print` (uses default printer/PDF); Linux: `lp file.txt` if `cups`
   present, else skip cleanly. Mirrors GHOSTS `Print`.
10. **`ssh-connect-jumpbox`** - Win: `ssh -o BatchMode=yes -o
    ConnectTimeout=5 user@host exit`; Linux: same. Targets a configurable
    internal host; produces SSH auth-fail/connect noise. Mirrors GHOSTS
    `Ssh`. (`scp`/`sftp` variant trivially derivable later.)

Stretch: `open-pdf-reader`, `take-screenshot`, `mount-share-smb`,
`open-calendar-outlook`.

## 4. Four additional personas

Each bundles 4-8 abilities into a believable timeline.

- **`developer.yml`** - `idle-think-time`, `open-file-explorer`,
  `search-files-explorer`, `ssh-connect-jumpbox`, `download-file-curl`,
  `create-document-notepad`, `idle-think-time`. Heavy on shell + network.
- **`executive.yml`** - `open-email-webmail`, `reply-email-outlook`,
  `browse-web-news`, `open-document-word`, `print-document-spooler`,
  `lock-screen-idle`. Email + docs + meeting prep.
- **`support-agent.yml`** - `open-email-webmail`, `reply-email-outlook`,
  `open-multiple-tabs-browser`, `click-random-links`, `copy-file-documents`,
  `idle-think-time`. Lots of context-switching.
- **`sales-rep.yml`** - `open-email-webmail`, `open-spreadsheet-excel`,
  `browse-web-news`, `download-file-curl`, `reply-email-outlook`,
  `lock-screen-idle`. Spreadsheet-heavy + outbound mail.

## 5. Schedule / timing - approximate, don't extend yet

GHOSTS bakes hour-of-day and idle ranges into each `TimelineHandler`. Caldera
adversary YAMLs have no time-of-day field, but the **operation** layer gives
us enough to fake it without forking core:

- `jitter` (min/max wait between abilities) -> idle-range equivalent.
- `auto_close` + scheduled operation start -> work-hours window.
- Multiple operations chained via the REST API (`/api/v2/operations`) cron'd
  on the C2 host -> 9am office-worker, 12pm lunch (lock-screen only),
  2pm support-agent, etc.
- `idle-think-time` ability already absorbs intra-timeline pauses.

Recommendation: ship a small **wrapper script on the C2** (cron + curl to
Caldera REST) rather than patching adversary YAML schema. Keeps our diff
against upstream Caldera near-zero, which was the whole point.

## 6. What GHOSTS gets right - keep these

- **Persona depth**: NPC identity (name, dept, timezone) feeds content
  (recipients, search terms). Add `additional_info.persona_hints` to
  abilities for future templating instead of `cnn.com` for everyone.
- **Network-noise modeling**: Curl/SSH/RDP/SFTP generate L4/L7 traffic AE
  sensors (Zeek, Suricata) catch. Items 6 and 10 are non-negotiable.
- **Looping & weighted next-step**: GHOSTS handlers loop. Caldera
  operations don't natively; roadmap item.
- **Watcher pattern**: react to file/registry changes. Future Caldera
  fact-source experiment.

## NEXT 10 TO WRITE (priority-ordered, copy into tracking issue)

1. `reply-email-outlook`
2. `open-document-word`
3. `open-spreadsheet-excel`
4. `copy-file-documents`
5. `search-files-explorer`
6. `download-file-curl`
7. `open-multiple-tabs-browser`
8. `lock-screen-idle`
9. `print-document-spooler`
10. `ssh-connect-jumpbox`
