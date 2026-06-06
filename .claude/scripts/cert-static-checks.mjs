#!/usr/bin/env node
/**
 * cert-static-checks.mjs
 *
 * Deterministic static analysis for the certify-story workflow.
 * Runs before LLM code review to catch known-critical patterns mechanically.
 *
 * Usage:
 *   node .claude/scripts/cert-static-checks.mjs <file1> <file2> ...
 *
 * Output:
 *   JSON array of findings to stdout. Empty array ([]) if nothing found.
 *   Each finding matches the certify-story finding schema.
 *
 * Checks:
 *   A. PostgREST subquery in .or() / .filter() — invalid syntax, silently broken
 *   B. Security header declared but never used in a conditional — control has no effect
 *   C. createAdminClient() without subaccount_id or primary_account_id ownership filter — RLS bypass
 */

import { readFileSync, existsSync } from 'fs'

const scopeFiles = process.argv.slice(2).filter(f => existsSync(f))
const findings = []

for (const filePath of scopeFiles) {
  let source
  try {
    source = readFileSync(filePath, 'utf-8')
  } catch {
    continue
  }

  const lines = source.split('\n')

  // ── Check A: PostgREST subquery in .or() / .filter() ──────────────────────
  //
  // PostgREST's .or() and .in() only accept literal value lists.
  // Embedded SQL like `in.(select id from ...)` is invalid PostgREST syntax —
  // it will produce a runtime error or silently return no rows.
  //
  // Pattern: a line that has .or( and also contains `in.(select`
  // OR: any line that contains `in.(select` (a standalone subquery in a filter)

  lines.forEach((line, i) => {
    const hasOrFilter = /\.or\(|\.filter\(/.test(line)
    const hasSubquery = /in\.\(\s*select\s/i.test(line)

    if (hasSubquery) {
      findings.push({
        severity: 'critical',
        category: 'correctness',
        title: 'PostgREST filter contains embedded SQL subquery — invalid syntax',
        description:
          'PostgREST .or() and .in() only accept literal value lists in their expressions, ' +
          'not SQL subqueries. The pattern `in.(select id from ...)` is not valid PostgREST ' +
          'syntax. This query will throw a runtime error or silently return no rows, ' +
          'making the feature non-functional for this code path.',
        file_path: filePath,
        line_start: i + 1,
        line_end: i + 1,
        code_snippet: line.trim(),
        recommendation:
          'Replace the subquery with a separate database call: first fetch the IDs you need ' +
          '(e.g. epic IDs for an initiative), then pass the resulting array as literals to ' +
          '.in([id1, id2, ...]). Use two sequential queries rather than an embedded subquery.',
      })
    } else if (hasOrFilter) {
      // Check if the next 5 lines contain a subquery (multi-line .or() call)
      const lookahead = lines.slice(i + 1, i + 6).join('\n')
      if (/in\.\(\s*select\s/i.test(lookahead)) {
        findings.push({
          severity: 'critical',
          category: 'correctness',
          title: 'PostgREST filter contains embedded SQL subquery — invalid syntax',
          description:
            'PostgREST .or() and .in() only accept literal value lists, not SQL subqueries. ' +
            'The multi-line .or() call starting here contains an embedded `select` expression ' +
            'which is invalid PostgREST syntax and will fail at runtime.',
          file_path: filePath,
          line_start: i + 1,
          line_end: Math.min(i + 6, lines.length),
          code_snippet: [line, ...lines.slice(i + 1, i + 4)].map(l => l.trim()).join('\n'),
          recommendation:
            'Fetch IDs in a separate query first, then pass the array as literals to .in([...]).',
        })
      }
    }
  })

  // ── Check B: Security header declared but never used in a conditional ──────
  //
  // Pattern: `const X = request.headers.get('...')` where the header name
  // contains 'token', 'nonce', or 'secret'.
  //
  // If variable X does not appear in any if/conditional/comparison in the file,
  // the security control is effectively a no-op.

  const sensitiveHeaderPattern =
    /const\s+(\w+)\s*=\s*(?:await\s+)?request\.headers\.get\(['"]([^'"]*)['"]\)/g

  let headerMatch
  while ((headerMatch = sensitiveHeaderPattern.exec(source)) !== null) {
    const varName = headerMatch[1]
    const headerName = headerMatch[2]

    const isSensitive = /token|nonce|secret/i.test(headerName)
    if (!isSensitive) continue

    // Check if varName is used in any conditional expression anywhere in the file
    const conditionalPatterns = [
      new RegExp(`if\\s*\\(.*\\b${varName}\\b`),
      new RegExp(`\\b${varName}\\b\\s*===`),
      new RegExp(`\\b${varName}\\b\\s*!==`),
      new RegExp(`\\b${varName}\\b\\s*==`),
      new RegExp(`!\\s*${varName}\\b`),
      new RegExp(`\\b${varName}\\b\\s*&&`),
      new RegExp(`&&\\s*\\b${varName}\\b`),
      new RegExp(`\\?.*\\b${varName}\\b`),
    ]

    const usedInConditional = lines.some(line =>
      conditionalPatterns.some(pattern => pattern.test(line))
    )

    if (!usedInConditional) {
      // Find the line number of the declaration
      const declLineIndex = lines.findIndex(l =>
        l.includes(varName) && l.includes('headers.get')
      )

      findings.push({
        severity: 'critical',
        category: 'security',
        title: `Security header '${varName}' read but never used in validation`,
        description:
          `The header '${headerName}' is read into '${varName}' but the variable is never ` +
          `used in a conditional check anywhere in this file. The security control has no ` +
          `effect — the header value is silently ignored after being read. Any caller can ` +
          `omit or forge this header without consequence.`,
        file_path: filePath,
        line_start: declLineIndex + 1,
        line_end: declLineIndex + 1,
        code_snippet: lines[declLineIndex]?.trim() ?? headerMatch[0],
        recommendation:
          `Validate '${varName}' against a stored expected value and return 401 or 403 if ` +
          `it does not match. If this header is no longer needed, remove the declaration entirely.`,
      })
    }
  }

  // ── Check C: createAdminClient() without subaccount_id ownership filter ───
  //
  // createAdminClient() bypasses Supabase Row Level Security entirely.
  // Every .from() query chain that uses an admin client must include an explicit
  // .eq('subaccount_id', ...) filter to prevent cross-tenant data access.
  //
  // Heuristic: for each .from( call in a file that uses createAdminClient(),
  // look ahead 15 lines for either:
  //   (a) .eq('subaccount_id', ...) — explicit ownership filter on SELECT/UPDATE
  //   (b) .insert( + subaccount_id: — INSERT with subaccount_id in the payload
  //       (inserts don't need a .eq() filter; the tenant is set via the data field)
  //
  // Note: cron jobs that intentionally iterate all tenants are a valid exception.
  // The LLM reviewer must cite this as mitigation_evidence or the finding remains CRITICAL.

  if (/createAdminClient\(\)/.test(source)) {
    const fromIndices = []
    lines.forEach((line, i) => {
      if (/\.from\(/.test(line)) fromIndices.push(i)
    })

    const unownedQueries = fromIndices.filter(fromIdx => {
      const lookahead = lines.slice(fromIdx, fromIdx + 15).join('\n')
      // Pass: explicit .eq('subaccount_id', ...) filter (SELECT/UPDATE ownership filter)
      if (/\.eq\(['"]subaccount_id['"]/.test(lookahead)) return false
      // Pass: explicit .eq('primary_account_id', ...) filter (account-level ownership filter)
      if (/\.eq\(['"]primary_account_id['"]/.test(lookahead)) return false
      // Pass: explicit .eq('id', primaryAccountId) on primary_accounts table (direct PK lookup)
      if (/\.from\(['"]primary_accounts['"]\)/.test(lines.slice(fromIdx, fromIdx + 3).join('\n')) &&
          /\.eq\(['"]id['"]/.test(lookahead)) return false
      // Pass: INSERT operation with subaccount_id or primary_account_id as a data field
      // (INSERT sets the owner via the payload, not via a WHERE filter)
      if (/\.insert\(/.test(lookahead) && /\b(subaccount_id|primary_account_id)\s*:/.test(lookahead)) return false
      return true
    })

    if (unownedQueries.length > 0) {
      const adminClientLine = lines.findIndex(l => /createAdminClient\(\)/.test(l))
      const firstUnowned = unownedQueries[0]

      findings.push({
        severity: 'critical',
        category: 'security',
        title: 'createAdminClient() used — .from() query lacks subaccount_id ownership filter',
        description:
          `This file uses createAdminClient() which bypasses Row Level Security. ` +
          `${unownedQueries.length} .from() query chain(s) do not have a ` +
          `.eq('subaccount_id', ...) filter within 15 lines. Without an explicit ` +
          `ownership filter, queries operate across all tenants and can read or write ` +
          `data belonging to other subaccounts. ` +
          `If this is intentional (e.g. a cron job iterating all tenants), the reviewer ` +
          `must document this as mitigation_evidence citing the specific code and rationale.`,
        file_path: filePath,
        line_start: firstUnowned + 1,
        line_end: Math.min(firstUnowned + 5, lines.length),
        code_snippet: lines.slice(firstUnowned, firstUnowned + 3).map(l => l.trim()).join('\n'),
        recommendation:
          `Add .eq('subaccount_id', userSubaccountId) to every supabase.from() query chain. ` +
          `Derive subaccountId from the authenticated user session, not from request input. ` +
          `If iterating all tenants is correct behavior, add a comment and cite it in ` +
          `mitigation_evidence when submitting certification.`,
      })
    }
  }
}

// Output findings as a JSON array
process.stdout.write(JSON.stringify(findings, null, 2) + '\n')
