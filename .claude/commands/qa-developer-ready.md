# QA: Developer Ready Judgment

This skill is injected at the start of every QA session. It establishes the interpretive
framework for deciding whether a story is ready for developer handoff — the judgment layer
that mechanical checks cannot apply on their own.

---

## What "Developer Ready" Means

A story is Developer Ready when a developer can pick it up, read the code, and build on it
without running into surprises that we could have caught. The standard is not "passes cert" —
it is "does not mask problems from the developer."

Before marking any story Developer Ready, answer this question:

> **Will a developer encounter this problem without our privileged context?**

If yes, the story is not ready. Fix the problem or document it where the developer will
actually see it — in `technicalNotes`, in source comments, or in the code itself.

---

## Interpretive Rules by Finding Type

### Check C — createAdminClient() without subaccount_id filter

The mechanical check fires whenever a `.from()` query in an admin-client file lacks an
explicit `.eq('subaccount_id', ...)` filter. Three patterns require judgment:

**Genuine bug** — An admin client query that could read or write another tenant's data.
This is always BLOCKED. Fix it before handoff.

**Bootstrap lookup** — A primary-key SELECT whose *purpose* is to retrieve the
subaccount_id (you don't have it yet). This is safe if: the caller has already validated
access via RLS or a trusted internal key, and the lookup is the first query in the chain.
Acceptable as CERTIFIED if a source comment explains the intent.

**Intentional cross-tenant** — A cron job or webhook receiver that must operate across all
tenants by design (e.g., a renewal job, a status write-back, a webhook lookup by
Google-issued channel ID). Acceptable as BLOCKED-with-documentation — not a bug, but
requires: (1) a source comment explaining why cross-tenant access is intentional,
(2) evidence that per-tenant scoping is applied as soon as the subaccount_id is known,
(3) the rationale written into `technicalNotes` so the developer understands the design.

**Mixed-client trap** — A file that imports both `createClient()` (user-scoped, RLS) and
`createAdminClient()` causes Check C to flag all `.from()` calls in the file, including
user-scoped ones. This is a false positive on the user-scoped calls, but the correct fix is
still to remove the mix: use only `createAdminClient()` with explicit filters throughout.
Using RLS as a silent safety net in a file that also has admin access is fragile.

---

### Test Gate — failing test suite

**CRITICAL (scope match)** — A failing test file whose base name matches a scope file.
This is a real blocker. The story's own tests are broken.

**HIGH (outside scope)** — A failing test unrelated to this story's files.
Before treating as CONDITIONAL, verify: Is this a pre-existing failure that predates
this story? Is the failure in a timing-sensitive performance test that flakes under load?
A confirmed pre-existing, out-of-scope failure is not a blocker for this story —
but if it's a test this story could have caused (e.g., a test for a shared dependency
that was modified), treat it as CRITICAL.

Known flaky test in this project: `components/roadmap/__tests__/roadmap-performance.test.ts`
uses sub-5ms timing assertions. It fails intermittently when the full test suite runs under
concurrent load. This is not a story-level issue.

---

### API Contract Changes

Any change to a public endpoint's request shape, response shape, required parameters, or
error codes is a developer-visible change. The developer receiving this story will write
code against this contract.

Before marking CERTIFIED, check: does the implementation match the contract described in
the story's acceptance criteria and description? If an implementation decision changed the
contract — even as a deliberate improvement — document it in `technicalNotes`.

Common contract changes to catch:
- A previously optional request field is now required
- A response field was renamed or removed
- Error status codes changed
- A new required environment variable was introduced

---

### TypeScript Errors

In-scope TypeScript errors are always HIGH — they reflect real type mismatches, not style.
Out-of-scope TypeScript errors produce an INFO finding and do not affect the story's tier.

Do not accept a story with in-scope TS errors as CERTIFIED even if the story "works" at
runtime. Type errors in strict mode indicate a correctness gap the developer will encounter.

---

## Tier Decisions

| Tier | Condition | Action |
|------|-----------|--------|
| **CERTIFIED** | No critical or high findings | Ready for handoff |
| **CONDITIONAL** | No critical, has high | Handoff allowed if high findings are documented in `technicalNotes` and developer is explicitly informed |
| **BLOCKED** | Any critical finding | Fix before handoff, or document as intentional with full rationale |

A CONDITIONAL story is not secretly CERTIFIED. When a story ships CONDITIONAL, the
`technicalNotes` must say what the high findings are and what the developer should know
about them.

---

## What Must Travel With the Story

Before submitting certification, verify that `technicalNotes` covers:

1. **Any API contract changes** — what changed from the original spec and why
2. **Any intentional BLOCKED patterns** — what is cross-tenant, why it must be, and what
   mitigations are in place (e.g., "per-tenant scoping applied as soon as subaccount_id
   is known from the initial lookup")
3. **Any known limitations** — things the code does not do that the developer might
   reasonably expect it to do
4. **Non-obvious implementation decisions** — anything a developer would likely question
   or change if they didn't understand the reasoning

If `technicalNotes` is empty on a story with CONDITIONAL or BLOCKED findings, the story
is not ready regardless of tier.

---

## The Masking Test (apply before every certification)

Read the `technicalNotes` as if you are the developer receiving this story for the first time.

Ask: *If I had only the code, the story description, and these technicalNotes, would I
know everything I need to build on this safely?*

If the answer is no — because a judgment call lives only in this session's context, in
MEMORY.md, or in an internal tool the developer cannot access — then the story is not
ready. Surface that knowledge to the developer before certifying.
