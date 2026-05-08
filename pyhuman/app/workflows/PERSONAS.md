# Workflow Personas

This catalogue maps job personas to the benign desktop-activity workflows
they should be assigned. Operators can use it as a starting recipe when
building a Human in the GUI.

Each persona below lists a sensible default subset of workflows. Operators
are free to mix and match, but these groupings approximate what a real user
in the named role would actually do at a desktop on a typical workday.

## Personas

- **office_worker**: `browse_web`, `open_office_calc`, `open_office_writer`,
  `download_files`, `create_document`, `open_email`, `click_links`
- **developer**: `spawn_shell`, `execute_command`, `browse_web` (tech sites
  via `data/websites.txt`), `download_files`, `create_document`
- **executive_assistant**: `open_office_writer`, `browse_web`,
  `download_files`, `open_email`, `create_document`, `click_links`
- **researcher**: `google_search`, `browse_web`, `browse_youtube`,
  `download_files`, `click_links`, `create_document`
- **sales_rep**: `open_email`, `browse_web`, `click_links`,
  `open_office_writer`, `create_document`
- **analyst**: `open_office_calc`, `google_search`, `browse_web`,
  `download_files`, `create_document`

## Workflow inventory

| Workflow              | Description                                                  |
|-----------------------|--------------------------------------------------------------|
| `browse_web`          | Visit a random site from `data/websites.txt`, click around   |
| `browse_youtube`      | Open a random YouTube link from `data/browse_youtube.txt`    |
| `click_links`         | Visit a site, click 3 random links with realistic delays     |
| `create_document`     | Open Writer/Notepad, type lorem ipsum, save to Documents     |
| `download_files`      | Download files from a webpage                                |
| `execute_command`     | Run a benign shell command                                   |
| `google_search`       | Search Google with a random term, browse results             |
| `ms_paint`            | Open MS Paint and draw (Windows)                             |
| `open_email`          | Visit a webmail page (gmail/outlook), idle, no login         |
| `open_office_calc`    | Open Apache OpenOffice Calc                                  |
| `open_office_writer`  | Open Apache OpenOffice Writer, type and format text          |
| `spawn_shell`         | Open a shell session                                         |

## Notes

- All workflows are intentionally benign. They simulate the foreground
  activity of a logged-in desktop user; they do not perform credential
  use, exfiltration, or anything an adversary emulation framework would
  classify as malicious.
- The control-server in `pyhuman/control_server.py` discovers workflows
  by scanning this directory and importing every `*.py` module that
  exposes a `load()` factory and a `BaseWorkflow` subclass. Adding a new
  benign-activity workflow is a matter of dropping a file here.
