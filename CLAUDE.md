# LabelPrinterStandalone_MobileQueue

Fork of `LabelPrinterStandalone` (SQLite-only, no shop POS/DB dependency) — kept as a **separate
project folder on purpose** so experimenting with the mobile-queue feature could never touch the
original working app or its data. Both projects still share the exact same
`build_label_image()`/label layout logic; a fix made in one should usually be considered for the
other too (see `[[project-label-printer-hybrid]]` memory for the broader multi-project picture).

## What this adds over the original standalone

Staff can key in a customer's drugs on their own phone (same WiFi as this PC) and submit to a
queue; this PC (the one with the physical printer) claims and prints from it. No cloud, no
internet dependency — everything is local-LAN only, matching the "one PC, one printer" assumption
of a small single-station shop.

- **`local_server.py`** — stdlib-only `http.server` (no new pip dependency to bundle), started as
  a background thread from `LabelApp.__init__`. Binds the first free port in `PORT_RANGE`
  (8869-8879) on `0.0.0.0`, auto-detects the LAN IP via a UDP-connect trick (`get_lan_ip()`).
  Serves the mobile page at `/` plus a small JSON API: `/api/search`, `/api/submit`,
  `/api/favorites`, `/api/settings`, `/api/staff`, `/api/staff_add`, `/api/staff_delete`.
- **`storage.py`** — new tables: `print_queue` (the actual queue — claim-then-delete pattern, same
  principle as the shop POS version's mobile queue) and `staff_names` (who's allowed to appear in
  the phone-side staff picker).
- **Favorites are NOT duplicated into a new table** — deliberately reused: `local_server.py` reads
  `favorites.json` directly (same file `label_gui.py`'s `load_favorites()`/`save_favorites()`
  already use, via `storage.APP_DATA_DIR`). Single-PC app, single writer (desktop), so there's no
  BeeStation-style multi-station sync race to worry about here — importing `label_gui` from
  `local_server` would also create a circular import (`label_gui` imports `local_server` to start
  it), so the read logic is duplicated as `local_server.read_favorites()` instead.
- **`has_dosing_data()` moved to `storage.py`** so both `label_gui.py` and `local_server.py` agree
  on what counts as "has real dosing info" (a `drug_templates` row can exist with only `drug1`
  filled in — e.g. from a bulk Excel import, or from typing just a name and hitting "บันทึกในเครื่อง"
  without filling anything else in — that must never show green/hasInfo=true). Found and fixed
  two real bugs from this exact gap: `local_server.build_search_results()` was checking
  `bool(info.get("drug1"))` instead, and `open_edit_dialog`'s `on_save_to_db()` was unconditionally
  setting `status = "db"` after any successful save regardless of content.
- **Staff picker on the mobile page** — shown once per phone (persisted in that phone's
  `localStorage`, key `lp_staff_name`), editable in-page (add via a text input, delete via a ✕
  toggled by "แก้ไขรายชื่อ"). The picked name is sent as `patient_name` on submit (the field is
  otherwise unused now that the patient-name/phone fields were removed from the mobile page per
  user request) and shown in the desktop queue dialog's listbox as `<name> (<N> รายการ) - <time>`.
- Native `confirm()` was deliberately replaced everywhere (`removeStaff`, `clearAllSelected`) with
  a custom in-page modal (`showConfirm()`/`#confirm-overlay`) — some mobile in-app browsers handle
  `window.confirm()` unreliably; ruled out as the likely cause of a "add stopped working after
  edit" report that turned out to be unreproducible at the code level (verified via a live headless
  browser test — every function call worked correctly when invoked directly; the small delete-✕
  touch target was the more likely real culprit, since enlarged 22px→30px alongside this).

## Build / deploy

Same as the original standalone: `python build_exe.py` from this folder → `dist\LabelPrinter.exe`.
Always `tasklist` for a running `LabelPrinter.exe` and get **fresh** confirmation before closing it
to rebuild — this project has repeatedly had a test instance left running from a prior turn, and
each close-to-rebuild needs its own explicit go-ahead (auto-mode gates it per-action, not
per-conversation).

## Local-LAN networking notes

- `PORT_RANGE = range(8869, 8879)` in `local_server.py` — almost always resolves to 8869 in
  practice.
- The LAN IP shown in the app (📱 คิวจากมือถือ button → dialog) is whatever DHCP currently hands
  this PC — it can change on router reboot/lease renewal unless pinned. See the supplementary PDF
  guide (`คู่มือเสริม - ระบบคิวมือถือ.pdf`) for how to pin it via a **Windows-side static IP**
  (Control Panel network adapter settings) rather than a router-side DHCP reservation — the user
  explicitly chose the Windows-side route as simpler for their setup.
- **Windows quirk hit while testing**: `SO_REUSEADDR` (the default for Python's `http.server`) lets
  a *second* process bind the same port without erroring even while a first process still holds it
  — unlike the stricter same-port protection on Linux. Caused confusing test results once (a fresh
  test script's server calls appeared to "succeed" on a port a stale exe was still listening on,
  serving stale content). If a live test ever looks inexplicably stale, check `tasklist` for a
  leftover `LabelPrinter.exe` before assuming the code is wrong.

## Version history

`APP_VERSION` in `label_gui.py` (shown in the window title bar) — bump it whenever a commit ships
a user-visible feature/fix batch, since there's no other version indicator in this app.

- **1.1.0** — patient profile feature (`patients`/`patient_documents` tables; search by
  name/phone; allergy notes; upload/view/delete document photos, desktop + mobile); "แพ้ยา"
  allergy checkbox (main screen + mobile page) that switches the label's warning-line suffix
  between underlined "ไม่แพ้ยา" and "แพ้ยา:ดูแฟ้ม", flows through the mobile print queue so the
  desktop auto-ticks it on claim; pharmacist name moved to its own label line aligned under
  "ให้นมบุตร" for longer names; assorted UX fixes (prefix-priority patient search, bigger
  upload-note dialog, inline per-row view/delete on document lists, resized patient dialog to fit
  the screen). PDF guides updated with a summary page each.
- **(unversioned baseline)** — everything in the initial commit (`fec6136`): mobile print queue,
  usage-mode label rendering (กิน/ทา/หยอด), print-history dialog, Excel import, Export/Import
  backup.

## Testing pattern used throughout this project

Prefer testing via **direct function calls** (`storage.*`, `local_server.build_search_results()`,
etc.) over spinning up a real HTTP server + browser, except when specifically verifying
browser/DOM behavior (e.g. the staff-edit-mode UI flow) — faster, avoids the port-collision quirk
above, and Windows console `print()` can't encode Thai text by default (`UnicodeEncodeError` on
`cp1252`) so test scripts write results to a UTF-8 file instead of printing directly.
