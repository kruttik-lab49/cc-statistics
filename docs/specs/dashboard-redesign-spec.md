# Dashboard Redesign Spec

## Goal

Redesign the web dashboard (`cc_stats_web/web/index.html`) to surface the most relevant information first, support multi-project selection, and provide session-level drill-down. Remove low-value metrics (instructions, tool calls) from prominent positions.

---

## 1. Controls

### Project Selector — replace `<select>` with checkbox dropdown
- Custom dropdown component showing all projects as checkbox rows
- Label shows selection state: "All Projects" / "2 projects selected" / project name (when exactly 1)
- "Select All" / "Clear" affordances at the top of the dropdown
- Source filter (`<select>`: All / Claude Code / Gemini CLI / Codex) remains unchanged

### Time Filter
- Keep existing pill buttons: Today / 7d / 30d / All

---

## 2. Summary Metrics Strip

Replace the current 4-card row with 3 cards:

| Card | Primary value | Subtext |
|------|--------------|---------|
| Active Time | formatted duration | AI % of active time |
| Est. Cost | `$X.XX` | total tokens |
| Projects | count of selected projects | total sessions across selected |

**Remove entirely:** Instructions card, Tool Calls card.

When exactly 1 project is selected, "Projects" card becomes "Sessions" (showing session count for that project, subtext: avg cost per session).

---

## 3. Page Body — Layout & Order

### 3a. Project Breakdown Table
- **Visible only when 2+ projects are selected**
- Columns: Project Name | Sessions | Active Time | Est. Cost | Lines +/- | Tokens
- Default sort: Est. Cost descending
- Sortable by any column (click header)

### 3b. Daily Trend Chart
- Keep as-is (cost bars over time)
- Scopes to selected projects + time filter

### 3c. Token Usage + Skill Usage
- Visible by default, side-by-side in a 2-column grid (same as current `.grid`)
- Token Usage: keep current model breakdown with stacked bar
- Skill Usage: keep current skill rows

### 3d. Collapsed "Details" Section
- A toggle button/disclosure: **"Details ▸"**
- Hidden by default, expands to reveal:
  - Dev Time (ring + time rows)
  - Code Changes (lines by language)
  - Tool Calls breakdown

### 3e. Sessions Table
- Always visible at the bottom
- **Columns:** Date | Project *(hidden when 1 project selected)* | Duration | Cost | Tokens | Lines +/-
- **Default sort:** Cost descending
- **Controls above the table:**
  - Search input (filters across date, project name)
  - Pagination (25 rows per page)
- Flat rows — no expansion

---

## 4. Context-Aware Behavior

| Selection | Project Breakdown Table | Sessions Table "Project" column |
|-----------|------------------------|--------------------------------|
| All / 2+ projects | Visible | Visible |
| Exactly 1 project | Hidden | Hidden |

Single-project selection effectively turns the dashboard into a project detail view — no separate page or URL needed.

---

## 5. API Changes Required

No new backend endpoints needed. Existing endpoints cover all data:
- `/api/projects` — project list
- `/api/stats` — aggregate metrics
- `/api/daily_stats` — trend chart
- `/api/skills` — skill usage
- Sessions data is already included in `/api/stats` response (or needs `session_list` added — see §5a below)

### 5a. Session List
The backend needs to expose per-session rows for the sessions table. Options:
- **Option A (preferred):** Add `session_list` array to `/api/stats` response — each item: `{ date, project, duration_fmt, estimated_cost, total_tokens, lines_added, lines_removed }`
- Option B: New endpoint `/api/sessions`

Use Option A to avoid an extra round-trip.

Multi-project filtering: when multiple projects are selected, the frontend must pass them as a comma-separated `project` param (or repeated params). Backend must handle the multi-value case.

---

## 6. Implementation Sequence

1. **Backend** — add `session_list` to `/api/stats` response; support multi-project param in all `/api/*` endpoints
2. **Frontend: controls** — replace project `<select>` with checkbox dropdown component
3. **Frontend: metrics strip** — update to 3 cards, wire up context-aware "Projects vs Sessions" card
4. **Frontend: project breakdown table** — conditional render for 2+ project selections
5. **Frontend: details collapse** — wrap Dev Time, Code Changes, Tool Calls in disclosure
6. **Frontend: sessions table** — add search, pagination, sortable columns, hide Project column for single-project view
7. **Testing** — verify all 3 selection modes (all, multi, single) render correctly; verify pagination and search

---

## 7. Documentation Updates (after implementation)

- **CLAUDE.md** — update Architecture section to note `session_list` in `/api/stats`, multi-project param support, and new dashboard sections
- **README.md** — update Web Dashboard feature description to reflect multi-project filtering, session table, and reorganized layout; update screenshot if one exists
