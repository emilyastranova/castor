# DOCX report

Writes a project's findings as a Word document and attaches it to a card.

- **Write DOCX report** on a project (it adds a card "Report: <date>" for it) or on a card (attached
  there). A title page; a summary with the project's description and findings by severity; every
  finding with its severity, what it affects, CVEs, the tool that found it and its detail; the
  hosts with their names, OS and services; the pinned notes.
- **Settings:** the title, the author (empty: whoever writes it; an admin can make it each
  person's own), the lowest severity included (low by default), and whether hosts and notes go in.

**Runs sandboxed**, with no network. It asks Castor for `projects:read`, `projects:write` (to add
the card and attach the file) and `assets:read`. It writes the `.docx` itself (`docx.ts`: the
document's XML, zipped), so it needs no library; Word, LibreOffice and Google Docs open it.

```bash
python3 plugins/pack.py plugins/docx-report      # → plugins/dist/docx-report-1.0.0.zip
cd plugins/docx-report && deno task test         # writes samples/sample-report.docx to look at
```
