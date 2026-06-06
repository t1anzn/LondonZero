# Certify Story

Perform a manifest-scoped certification review on a completed story. Reads the story's file manifest, reviews all changed files against a certification checklist, and submits a certification record to the DecisionGraph API.

## Arguments

- `$ARGUMENTS` — Space-separated: `<storyId> <clientId> <authToken>`

Example:
```
/certify-story 416e8585-4714-4b15-81bb-f58a66f55ad9 8726d3ac-fbf6-41fb-a63e-76697b3cb24e oos_tok_ssn_abc123
```

## Instructions

### 1. Parse Arguments

Extract three values from `$ARGUMENTS`:
- `STORY_ID` — first token
- `CLIENT_ID` — second token
- `AUTH_TOKEN` — third token
- `API_BASE` — always `https://decisiongraph.io`

If fewer than three arguments are provided, stop and ask the user for the missing values.

### 2. Fetch Story Details

Fetch the story to get its manifest and acceptance criteria:

```bash
curl -s "${API_BASE}/api/extension/clients/${CLIENT_ID}/stories/${STORY_ID}" \
  -H "Authorization: Bearer ${AUTH_TOKEN}"
```

From the response, extract:
- `title` — for logging
- `description` — what the story implements
- `acceptanceCriteria` — the done conditions to verify
- `technicalNotes` — implementation context
- `manifest` — the file manifest (`files_created`, `files_modified`, `files_deleted`)

**If no manifest is present**, the story was not completed with manifest submission. Stop and report:
> "No manifest found for this story. The story must be marked done with a manifest before certification. See the session file's completion workflow for manifest submission instructions."

### 3. Fetch Certification Type

Get the code review certification type:

```bash
curl -s "${API_BASE}/api/extension/clients/${CLIENT_ID}/certification-types" \
  -H "Authorization: Bearer ${AUTH_TOKEN}"
```

From the `certification_types` array, find the entry with `category: "code_review"` and save its `id` as `CERT_TYPE_ID`.

**If no code_review type exists**, stop and report:
> "No code_review certification type configured. Ask the project admin to create one in DecisionGraph settings."

### 4. Build Review Scope

Collect all file paths from the manifest:
- `files_created[].path` — new files to review in full
- `files_modified[].path` — changed files to review in full
- `files_deleted[].path` — note these but don't review (files no longer exist)

These are the ONLY files in scope. Do NOT review files outside the manifest.

### 5. Get Current Git Commit

```bash
git rev-parse HEAD
```

Save the first 40 characters as `SCOPE_COMMIT`.

### 5.5. Run Pre-Checks

Run the unified pre-check script against all scope files:

```bash
node .claude/scripts/cert-pre-checks.mjs <space-separated scope file paths>
```

This script runs four deterministic checks and outputs a single JSON findings array:
1. **Test suite gate** (scope-aware) — runs the full test suite; CRITICAL only if failing tests match scope files, HIGH if failures are in unrelated files
2. **ESLint** — lint errors on scope files (`medium`/`low`; produces an `info` finding if config migration needed)
3. **TypeScript** — `tsc --noEmit` errors in scope files (`high`); out-of-scope errors produce a single `info` finding
4. **Static pattern checks** — PostgREST subqueries, unused security headers, admin client RLS bypass (all `critical`)

Parse stdout as a JSON array. **Add all returned findings to your findings list.** Do not modify their severity — the script applies its own calibrated rules:
- test failures matching scope files → `critical`
- test failures in unrelated files → `high` (not a blocker for this story)
- ESLint errors → `medium`; ESLint warnings → `low`
- TypeScript errors in scope files → `high`
- PostgREST / security-header / admin-client patterns → `critical`

If the script produces invalid JSON or exits with an unexpected error, add a `critical` finding:
> title: "Pre-check script failed — certification cannot proceed without it"

Continue collecting other findings even if pre-checks find issues — do not abort.

### 6. Review Each File

Read every file in scope using the Read tool. For each file, evaluate it against the **Certification Checklist** below. Also cross-reference the story's acceptance criteria and description to verify the implementation actually addresses them.

If the scope contains more than 8 files, use the Task tool with `subagent_type="general-purpose"` to review files in parallel batches of 3-5. Each subagent prompt should include:
- The file path to review
- The story description and acceptance criteria
- The full Certification Checklist (copied below)
- Instructions to return findings in the exact JSON format specified in section 7

For 8 or fewer files, review sequentially — the overhead of subagents isn't worth it.

---

#### Certification Checklist

**A. Acceptance Criteria Verification**
- [ ] Each acceptance criterion from the story is addressed by the implementation
- [ ] No acceptance criterion is left unimplemented or partially implemented
- [ ] Implementation doesn't introduce behavior contradicting the acceptance criteria

**B. Correctness**
- [ ] Logic handles edge cases (null, undefined, empty arrays, zero values)
- [ ] Error paths return appropriate responses (not swallowed silently)
- [ ] Async operations use proper await / error handling
- [ ] Database queries filter correctly (no missing WHERE clauses)
- [ ] API responses match the expected shape / contract

**C. Security**
- [ ] No hardcoded secrets, API keys, or tokens in source
- [ ] User input is validated before use (API params, form fields)
- [ ] No SQL injection, XSS, or command injection vectors
- [ ] Auth checks are present where required — cite the specific `if (!user)` or equivalent line as evidence
- [ ] Sensitive data is not logged or exposed in error messages
- [ ] Security-sensitive headers (token, nonce, secret, key) are not only *read* but *validated* — cite the conditional that checks the value
- [ ] `createAdminClient()` calls are accompanied by explicit ownership filters (`.eq('subaccount_id', ...)`) on every query — cite them, or flag as CRITICAL if absent
- [ ] For any security check: if you cannot cite the specific line of code that enforces it, rate the finding CRITICAL and note the evidence is missing

**D. Code Quality**
- [ ] No debug code left behind (console.log, debugger, TODO/FIXME)
- [ ] No commented-out code blocks
- [ ] Follows existing patterns in the codebase (naming, structure, error handling)
- [ ] No duplicate logic that should be extracted
- [ ] TypeScript types are correct (no unsafe `any` casts)

**E. Integration**
- [ ] New API endpoints match existing route conventions
- [ ] Database changes have corresponding migrations
- [ ] No breaking changes to existing interfaces or contracts
- [ ] Imports resolve correctly (no missing dependencies)

---

### 7. Collect Findings

Build a JSON array of findings from your LLM review. Start with the pre-check findings from Step 5.5 already in the list.

Each finding must match this schema:

```json
{
  "severity": "critical | high | medium | low | info",
  "category": "acceptance_criteria | correctness | security | code_quality | integration",
  "title": "Short description of the issue",
  "description": "Detailed explanation of what's wrong and why it matters",
  "file_path": "relative/path/to/file.ts",
  "line_start": 42,
  "line_end": 45,
  "code_snippet": "the problematic code",
  "recommendation": "How to fix it",
  "mitigation_evidence": {
    "file_path": "path/to/file.ts",
    "line_start": 88,
    "code_snippet": "the specific code that proves the control is in place"
  }
}
```

`mitigation_evidence` is **required** for every `security` finding rated `high` or lower. It must cite the exact code that demonstrates the control works. If you cannot find that code, rate the finding `critical` instead and omit `mitigation_evidence`.

**Severity guide:**
- **critical** — Must fix before handoff: security vulnerability, data loss, broken core functionality, acceptance criterion not met. Also critical: **any security finding where you cannot point to specific code that proves the control works.** Uncertainty about whether a security check is present or effective is a critical finding — the burden of proof is on demonstrating the control works, not on proving it is broken.
- **high** — Should fix before handoff: missing error handling, logic bug in non-critical path, incomplete validation, TypeScript type errors. Security findings at HIGH require `mitigation_evidence`.
- **medium** — Recommended cleanup: lint issues, code quality problems, missing edge case handling, weak patterns
- **low** — Nice to have: style inconsistencies, minor optimizations, documentation gaps
- **info** — Observation: notable patterns, architectural notes, things to watch, out-of-scope issues flagged for awareness

### 7.5. Apply Security Promotion Rule

After collecting all findings from your review, apply this rule mechanically:

**For every finding where:**
- `category` is `"security"`, AND
- `severity` is not `"critical"`, AND
- `mitigation_evidence` is absent or empty

**→ Set `severity` to `"critical"`.**

This is a mechanical step — do not apply judgment. If evidence of the mitigation cannot be cited in code, the finding is critical.

### 8. Determine Certification Tier

Evaluate all findings (pre-check + LLM review + promotion rule applied) and assign a tier:

| Tier | API `status` | Condition |
|------|-------------|-----------|
| **BLOCKED** | `"failed"` | Any `critical` finding |
| **CONDITIONAL** | `"passed"` | No `critical`, but any `high` finding |
| **CERTIFIED** | `"passed"` | No `critical` or `high` findings |

The tier is encoded in the `summary` field for reporting. The API `status` field stays binary for compatibility (`BLOCKED` → `"failed"`, `CONDITIONAL`/`CERTIFIED` → `"passed"`).

### 9. Submit Certification

Build and submit the certification payload:

```bash
curl -s -X POST "${API_BASE}/api/extension/clients/${CLIENT_ID}/stories/${STORY_ID}/certifications" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "certification_type_id": "${CERT_TYPE_ID}",
    "status": "passed | failed",
    "scope_files": ["file1.ts", "file2.ts"],
    "scope_commit": "${SCOPE_COMMIT}",
    "summary": "Code review certification for story: ${STORY_TITLE}. Tier: BLOCKED|CONDITIONAL|CERTIFIED. Reviewed N files from manifest. Found X critical, Y high, Z medium, W low, V info findings.",
    "findings": [...]
  }'
```

**Important:** The `findings` array and `scope_files` array must be valid JSON. Use a heredoc or temp file approach if the payload is large.

### 10. Report Results

After submission, print a summary:

```
## Certification Result

**Story:** {title}
**Tier:** BLOCKED / CONDITIONAL / CERTIFIED
**API Status:** PASSED / FAILED
**Commit:** {scope_commit}
**Files Reviewed:** {count}
**Certification ID:** {id from response}

{Tier explanation — print one of:}
  BLOCKED: Critical findings must be resolved. This story is not ready for handoff to developers.
  CONDITIONAL: No blocking bugs found. High findings should be cleaned up before handing to developers.
  CERTIFIED: No critical or high findings. This story is clean and ready for handoff.

### Findings Summary
- Critical: {n}   [blocks handoff]
- High: {n}       [should fix before handoff]
- Medium: {n}     [recommended cleanup]
- Low: {n}        [nice to have]
- Info: {n}

### Blocking Findings (Critical)
{List each: [file:line] title — recommendation}

### Pre-Handoff Cleanup (High)
{List each: [file:line] title — recommendation}

### Recommended Cleanup (Medium / Low)
{List each if any, grouped by severity}
```

If tier is **BLOCKED**, clearly list the critical findings that must be resolved before handoff.

If tier is **CONDITIONAL**, list the high findings with the expectation they will be addressed before the code reaches human developers.

If tier is **CERTIFIED**, note any medium/low findings as optional cleanup.
