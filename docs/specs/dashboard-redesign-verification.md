# Dashboard Redesign — Verification Contract

Independent checklist for verifying implementation completeness. Each item is a concrete, observable test. The verifier should run `cc-stats-web` and perform each check in a browser.

---

## Setup

```bash
cc-stats-web
# Open http://localhost:57384 (or whatever port is printed)
```

Have at least 2 distinct projects with session data available to test multi-project behavior.

---

## 1. Project Selector

| # | Check | Pass Condition |
|---|-------|---------------|
| 1.1 | Project selector is a checkbox dropdown, not a `<select>` | Clicking the control opens a dropdown with checkboxes, not a native OS select menu |
| 1.2 | Dropdown shows all projects as checkboxable rows | Each project appears with a checkbox |
| 1.3 | "Select All" affordance present | Clicking it checks all projects |
| 1.4 | "Clear" affordance present | Clicking it unchecks all projects |
| 1.5 | Label updates to reflect selection state | 0 selected → "All Projects"; 1 selected → project name; 2+ selected → "N projects selected" |
| 1.6 | Source filter (`<select>`) still present and functional | Selecting "Claude Code" filters the project list |

---

## 2. Summary Metrics Strip

| # | Check | Pass Condition |
|---|-------|---------------|
| 2.1 | Exactly 3 metric cards visible | Count the cards — must be 3, not 4 |
| 2.2 | "Instructions" card absent | No card with label "Instructions" anywhere on the page |
| 2.3 | "Tool Calls" card absent from the metrics strip | No card with label "Tool Calls" in the top strip |
| 2.4 | Card 1: Active Time | Card shows formatted duration (e.g. "2h 14m") with AI % subtext |
| 2.5 | Card 2: Est. Cost | Card shows dollar amount with total tokens subtext |
| 2.6 | Card 3 (All / 2+ projects): "Projects" | Shows count of selected projects; subtext shows total sessions |
| 2.7 | Card 3 (exactly 1 project selected): "Sessions" | Label changes to "Sessions"; shows session count for that project; subtext shows avg cost per session |

---

## 3. Project Breakdown Table

| # | Check | Pass Condition |
|---|-------|---------------|
| 3.1 | Table hidden when "All Projects" selected | No project breakdown table visible on initial load |
| 3.2 | Table hidden when exactly 1 project selected | Select one project — table disappears |
| 3.3 | Table visible when 2+ projects selected | Select 2 projects — table appears |
| 3.4 | Table columns: Project Name, Sessions, Active Time, Est. Cost, Lines +/-, Tokens | All 6 columns present |
| 3.5 | Default sort is Est. Cost descending | On first render with 2+ projects, highest-cost project is in row 1 |
| 3.6 | Clicking a column header sorts by that column | Click "Sessions" header — rows reorder by session count |
| 3.7 | Clicking a sorted column header reverses sort direction | Click same header twice — order inverts |

---

## 4. Daily Trend Chart

| # | Check | Pass Condition |
|---|-------|---------------|
| 4.1 | Chart still present on the page | Cost bar chart visible below the metrics strip |
| 4.2 | Chart updates when project selection changes | Select a single project — bars reflect only that project's data |

---

## 5. Token Usage & Skill Usage

| # | Check | Pass Condition |
|---|-------|---------------|
| 5.1 | Token Usage card visible by default (no interaction needed) | Card is present and populated without clicking anything |
| 5.2 | Skill Usage card visible by default when skills exist | If skills data is present, card is shown without clicking anything |
| 5.3 | Token Usage shows model breakdown with stacked bars | Each model row has a colored stacked bar |

---

## 6. Collapsed "Details" Section

| # | Check | Pass Condition |
|---|-------|---------------|
| 6.1 | A "Details" toggle button is present | Button/disclosure labeled "Details" (or "Details ▸") exists on the page |
| 6.2 | Dev Time, Code Changes, and Tool Calls are hidden by default | On page load, none of these 3 sections are visible |
| 6.3 | Clicking "Details" reveals all 3 sections | Dev Time, Code Changes, Tool Calls all appear |
| 6.4 | Clicking "Details" again collapses them | All 3 sections hidden again |

---

## 7. Sessions Table

| # | Check | Pass Condition |
|---|-------|---------------|
| 7.1 | Sessions table always visible at the bottom of the page | Table present on initial load (All Projects, Today) |
| 7.2 | Columns present: Date, Project, Duration, Cost, Tokens, Lines +/- | All 6 columns visible when 2+ projects selected |
| 7.3 | "Project" column hidden when exactly 1 project selected | Select 1 project — Project column disappears from table |
| 7.4 | Default sort is Cost descending | Highest-cost session appears in row 1 on initial load |
| 7.5 | Search input present above table | Text input visible above the sessions table |
| 7.6 | Search filters rows | Type a project name or date fragment — table rows filter in real time |
| 7.7 | Pagination controls present | Page indicators and next/prev controls visible when sessions exceed 25 |
| 7.8 | Page size is 25 rows | Count rows on page 1 — must be ≤ 25 |
| 7.9 | Pagination navigates correctly | Click "Next" — rows change to next 25 sessions |
| 7.10 | Search resets to page 1 | Enter a search term — pagination resets to page 1 |

---

## 8. Backend — Multi-Project Filtering

| # | Check | Pass Condition |
|---|-------|---------------|
| 8.1 | Selecting 2 projects sends both to the API | Open browser DevTools → Network tab → select 2 projects → confirm `/api/stats` request includes both project names as params |
| 8.2 | Stats aggregate across selected projects only | Select 2 known projects → Est. Cost equals sum of their individual costs |
| 8.3 | `/api/stats` response includes `session_list` array | In DevTools, inspect the `/api/stats` JSON response — `session_list` key is present and is an array of objects |
| 8.4 | Each `session_list` item has required fields | Inspect first item: `date`, `project`, `duration_fmt`, `estimated_cost`, `total_tokens`, `lines_added`, `lines_removed` all present |

---

## 9. Documentation

| # | Check | Pass Condition |
|---|-------|---------------|
| 9.1 | `CLAUDE.md` updated | Architecture section mentions `session_list` in `/api/stats` and multi-project param support |
| 9.2 | `README.md` updated | Web Dashboard feature description mentions multi-project filtering and session table |

---

## Pass Criteria

Implementation is **complete** when all 44 checks pass with no exceptions. Any single failing check is a blocking defect.
