# Dashboard and witness exports

The dashboard uses sidebar navigation, workspace metrics and the existing Settings color palettes. Palette choices still preview, save and restore normally. Buttons use Title Case; witness names, filenames and source citations keep their original casing. Ollama is the default provider in the toolbar and API requests for new runs. Existing runs retain their recorded provider and model.

## Less scrolling

- Workspace Readiness and Analysis And Generation expand independently.
- Settings groups, Add Evidence, bulk privacy controls and processing history are collapsible.
- Witness cards are shown 12 per page. Search covers the entire witness list, not only the displayed page.
- Opening a witness renders that card's content on demand. Profile, relationships, chronology, supporting findings and questions expand separately.
- Expand All and Collapse All operate on cards on the displayed page. Opening Prepare Questions expands the intended witness and question section automatically.
- Disclosure state, witness export selections and unsaved examination settings survive application refreshes within the current browser session. Selections and drafts remain isolated by workspace. A full browser reload starts a fresh presentation session.

## Selected and all witness exports

1. Check the witnesses in the witness list, then choose **Export Selected**. **Select All** selects every witness across all pages; **Clear Selection** clears those checks.
2. Use **Export All** to open the export dialog with all witnesses selected.
3. Choose witness names, output sections and review statuses. All sections and statuses are checked initially, with draft/approved/rejected state labelled per item.
4. Choose **Download CSV** or **Download Word**. Word can be one combined document or separate documents packaged in a ZIP.

Exports include all saved questions, including those on other question pages, and all saved profile/outline versions within the selected scope. Sections cover profiles or source-derived digests, possible issues, relationships, dated and undated events, supporting findings, questions, and saved generation/review metadata. Saved question histories and examination settings are retained. A source appendix in Word provides each full source reference once per witness; short S-number references in the body point to that appendix. CSV carries full source references and a structured `record_details` column for saved item metadata.

The backend takes a consistent workspace-scoped snapshot. Unknown witness selections are rejected, trashed workspaces are blocked and citations outside the selected workspace are labelled unavailable. Rejected or obsolete citation support is labelled for review. Exporting does not generate, approve, edit or delete evidence. CSV cells that could be interpreted as spreadsheet formulas are escaped. Exports can contain sensitive evidence and retain the selected review statuses; they are not certifications of legal accuracy.

The per-outline CSV action and Audit workspace JSON export remain available.
