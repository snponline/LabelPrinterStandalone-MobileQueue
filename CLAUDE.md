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

- **1.5.0** — A4 sheet printing, for shops without a thermal label printer. New "ประเภทกระดาษ" dropdown
  in settings (`app_settings.DEFAULTS["paper_mode"]`, `"thermal"` default or `"a4"`). When `"a4"`,
  `do_print`'s worker builds every physical label image first (print_qty duplicates included, all
  same rendered object reused per copy) into one flat list, then calls new `build_a4_pages()`
  (label_gui.py, near `print_image`) to tile them onto A4-sized (210x297mm) page images - up to
  `A4_COLUMNS = 2` per row, as many rows as fit given `A4_MARGIN_MM = 5` / `A4_GAP_MM = 3`, each
  label kept at its real configured physical size (not stretched) so a shop can cut them apart by
  hand. Falls back to 1 column automatically if the configured label is too wide for 2 side-by-side.
  Each label gets a dashed "รอยปรุ" cut-guide border (`_draw_dashed_rect()`) drawn around its edge
  directly on the page (not on the label image itself, so it never overlaps label content) - makes
  the cut/tear line obvious since there's no physical die-cut like a real label roll has.
  Each resulting page is sent through the existing `print_image()` unchanged - **no separate PDF
  export code was needed**: picking "Microsoft Print to PDF" as the `printer_name` in settings (a
  printer Windows ships with) already produces a real PDF through this same pipeline, since from
  this app's point of view it's just another printer. Thermal mode's behavior is 100% unchanged
  (still prints one label = one page, same code path as before this feature existed). Verified via
  isolated `build_a4_pages()` calls with 7 and 15 fake labels (1 page / 2 pages respectively,
  confirmed correct 2-column reading-order placement by rendering to PNG and inspecting visually) -
  not yet tested against a physical A4 printer or the "Microsoft Print to PDF" printer end-to-end.
  **MobileQueue-only per explicit user request — not ported to the HOPE build.**
- **1.4.0** — "📋 HN ทั้งหมด" button in "ประวัติผู้ป่วย" opens `open_all_hn_dialog()`: every patient
  record in one list, sortable by ชื่อ or HN (`storage.list_all_patients(order_by=...)`, column name
  whitelisted, never interpolated from caller input), double-click or "✓ เลือก" to load that patient
  into the parent dialog. New `storage.delete_patient(id)` (single record, with confirm) and
  `storage.delete_all_patients()` (double-confirmed, for a store that just started using patient
  profiles and wants to wipe test/dummy entries and restart HN numbering) — both remove the
  patient's uploaded document files + rows, and **unlink** (not delete) any `print_jobs.patient_id`
  pointing at them, so the print history text itself is never touched, only the FK link. Deleting
  everything naturally resets `_generate_hn_code()` back to `2026-00001` next time a patient is
  created, since it looks at what's actually in the (now empty) table rather than tracking a
  separate counter.
- **1.3.0** — `print_jobs`/`patients` are now linkable by id, not just fuzzy name/phone string
  matching. New `print_jobs.patient_id` column (nullable) is only ever set when a real `patients`
  row unambiguously exists for that print: either `find_or_create_patient()` just created/found one
  (save-to-patient-file was ticked), or `pick_name()` resolved one via the new `find_patient_id()`
  (exact name+phone match, **never auto-creates** — reprinting an old anonymous label must not spawn
  a junk patient profile). `list_print_jobs_for_patient()` now takes an optional `patient_id` and
  ORs it into the WHERE clause ahead of the legacy name/phone match, so old pre-migration rows (and
  prints never linked to a saved profile) still surface normally. New `patients.hn_code` column
  (`YYYY-NNNNN`, 5-digit running number reset per calendar year, generated by `_generate_hn_code()`
  off the max existing suffix for that year — never a row COUNT, so a deleted patient can't cause a
  code to be reused) — a customer-facing id for a planned future "online card" feature, deliberately
  kept separate from the internal autoincrement `id` used for the FK above so its format can change
  independently later. Shown as `[HN xxxx-xxxxx]` next to the name in "ประวัติผู้ป่วย". One-time
  (but idempotent/safe-to-rerun) `backfill_patient_hn_codes()` / `backfill_print_job_patient_ids()`
  were run once against this machine's real local `data.db` to retroactively fill both columns for
  pre-existing rows — ported identically to HOPE's SQL Server build in the same session.
- **1.2.0** — "👤 เลือกชื่อลูกค้า" button in the list header: opens the existing "แฟ้มประวัติการจ่ายยา"
  dialog in a `pick_mode` (extra "✓ เลือกชื่อนี้" button, dialog stays open after picking so the
  pharmacist can close it manually) and carries the picked name+phone into the next print via
  `_queue_patient_name`/`_queue_patient_phone`, auto-filled in `open_patient_dialog`. That carried
  name/phone is now only cleared once a print (or history-only save) actually *completes*
  (`on_print_done`/`on_history_saved`), not when the confirm dialog is merely opened/cancelled, so
  reopening it after going back to edit the drug list doesn't lose the picked name. Placeholder
  "รอชื่อลูกค้า (optional)" shown under the button when nothing's picked yet, styled 1.3x size /
  dark blue (`#0a3d7a`) so it reads clearly next to "ล้างทั้งหมด"/"แพ้ยา". New
  "📋 บันทึกประวัติ (ไม่พิมพ์)" button next to "พิมพ์ฉลาก" in the same dialog — saves the visit to
  print history (and the patient file, if that was checked) via `storage.add_print_job` without
  sending anything to the actual printer, for phone consults / "what did they buy last time"
  lookups that don't end in a real dispense. Ported 1:1 from the same batch of changes made to the
  HOPE build of this app (`HOPE\label_printer\label_gui.py`) in the same session — see that
  project's own notes for the original design back-and-forth with the user.
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
