#!/usr/bin/env node
/**
 * cert-pre-checks.mjs
 *
 * Unified deterministic pre-check runner for certify-story.
 * Runs all mechanical checks before LLM review and outputs a unified JSON findings array.
 *
 * Checks (in order):
 *   1. Test suite gate (scope-aware — only CRITICAL if failing tests match scope files)
 *   2. ESLint on scope files (graceful fallback if ESLint config not yet migrated)
 *   3. TypeScript type check (filtered to scope files only)
 *   4. Pattern-based static checks (delegates to cert-static-checks.mjs)
 *
 * Usage:
 *   node .claude/scripts/cert-pre-checks.mjs <file1> <file2> ...
 *
 * Output:
 *   JSON array of findings to stdout. Empty array ([]) if nothing found.
 *   Severity calibration:
 *     critical — test failure in scope files, static pattern violations
 *     high     — TypeScript errors in scope files, test failures outside scope
 *     medium   — ESLint errors (severity 2)
 *     low      — ESLint warnings (severity 1)
 *     info     — out-of-scope TS errors, ESLint config not available
 */

import { existsSync } from 'fs'
import { execSync } from 'child_process'
import { resolve, basename, dirname, join, relative } from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const ROOT = resolve(__dirname, '../..')       // DecisionGraph monorepo root
const PG_ROOT = join(ROOT, 'apps', 'web')

const scopeFiles = process.argv.slice(2).filter(f => existsSync(f))
const findings = []

// ── Helper ──────────────────────────────────────────────────────────────────

function run(cmd, opts = {}) {
  try {
    const output = execSync(cmd, {
      cwd: opts.cwd || ROOT,
      encoding: 'utf-8',
      timeout: opts.timeout || 60000,
      shell: true,
    })
    return { ok: true, output, code: 0 }
  } catch (e) {
    const output = (e.stdout || '') + (e.stderr || '')
    return { ok: false, output, code: e.status || 1 }
  }
}

// ── Check 1: Test suite gate (scope-aware) ──────────────────────────────────
//
// Runs the full test suite. If it passes, no findings.
// If it fails, checks whether the failing test files correspond to scope files:
//
//   - Match found  → CRITICAL (tests for THIS story are broken)
//   - No match     → HIGH (pre-existing failure unrelated to this story's scope)
//
// Matching rule: strip .test.(ts|tsx|js|jsx) from the failing test filename and
// compare against scope file basenames (without extension).
// Example: webhook-manager.test.ts matches webhook-manager.ts in scope.

// Capture full output without piping (pipes lose the exit code via tail/head).
// Slice output in JavaScript instead to get the relevant tail.
const testResult = run('npm run test:run 2>&1', {
  cwd: PG_ROOT,
  timeout: 120000,
})

if (!testResult.ok) {
  const output = testResult.output

  // Vitest format: " FAIL  lib/workspace/__tests__/foo.test.ts [ ... ]"
  const failPattern = /\bFAIL\s+([\w./-]+\.test\.[jt]sx?)/g
  const failingTestFiles = []
  let m
  while ((m = failPattern.exec(output)) !== null) {
    failingTestFiles.push(m[1])
  }

  if (failingTestFiles.length === 0) {
    findings.push({
      severity: 'high',
      category: 'correctness',
      title: 'Test suite is failing — could not parse failing test file names',
      description:
        'npm run test:run exited with a non-zero status code but no individual failing test ' +
        'files could be extracted from the output. This may be a compilation error or ' +
        'environment issue. The test gate is inconclusive — inspect manually.',
      file_path: scopeFiles[0] || 'apps/web',
      line_start: 1,
      line_end: 1,
      code_snippet: output.slice(-500).trim(),
      recommendation: 'Run `cd apps/web && npm run test:run` manually and resolve the failure.',
    })
  } else {
    // Derive expected test file basenames from scope files
    // e.g., "apps/web/lib/workspace/artifact-summarizer.ts" → "artifact-summarizer"
    const scopeBasenameMap = new Map(
      scopeFiles.map(sf => [basename(sf).replace(/\.tsx?$/, ''), sf])
    )

    const matchingPairs = failingTestFiles.flatMap(testFile => {
      // "lib/workspace/__tests__/artifact-summarizer.test.ts" → "artifact-summarizer"
      const testBase = basename(testFile).replace(/\.test\.[jt]sx?$/, '')
      const matchedScopeFile = scopeBasenameMap.get(testBase)
      return matchedScopeFile ? [{ testFile, scopeFile: matchedScopeFile }] : []
    })

    if (matchingPairs.length > 0) {
      // Failing tests belong to scope files — CRITICAL
      for (const { testFile, scopeFile } of matchingPairs) {
        const snippetMatch = output.match(new RegExp(`FAIL[^\n]*${basename(testFile)}[^\n]*`))
        findings.push({
          severity: 'critical',
          category: 'correctness',
          title: `Test suite failing — tests for ${basename(scopeFile)} are broken`,
          description:
            `The test file ${testFile} is failing. It corresponds to ${basename(scopeFile)} ` +
            `which is in this story's scope manifest. The story cannot be certified while ` +
            `its own tests are broken.`,
          file_path: scopeFile,
          line_start: 1,
          line_end: 1,
          code_snippet: snippetMatch ? snippetMatch[0] : testFile,
          recommendation: `Fix the failing tests in ${testFile} before certifying this story.`,
        })
      }
    } else {
      // Failing tests are NOT in scope — HIGH but not a blocker for this story
      findings.push({
        severity: 'high',
        category: 'correctness',
        title: "Test suite has pre-existing failures outside this story's scope",
        description:
          `The test suite is failing, but the failing test files do not correspond to files ` +
          `in this story's manifest. Failing: ${failingTestFiles.join(', ')}. ` +
          `These failures should be addressed, but they are not a blocker for certifying ` +
          `this specific story.`,
        file_path: scopeFiles[0] || 'apps/web',
        line_start: 1,
        line_end: 1,
        code_snippet: failingTestFiles.join('\n'),
        recommendation: `Address the pre-existing test failures: ${failingTestFiles.join(', ')}`,
      })
    }
  }
}

// ── Check 2: ESLint on scope files ──────────────────────────────────────────
//
// Runs ESLint with JSON output on scope files that live under apps/web.
// Groups findings by ruleId per file (one finding per unique rule per file)
// to avoid flooding the report when one file has many of the same violation.
//
// Note: Next.js v16 removed `next lint`, and ESLint v9 requires eslint.config.mjs.
// If the project uses the old .eslintrc.json format with ESLint v9, the check
// falls back to an info finding prompting the config migration.

const eslintFiles = scopeFiles
  .filter(f => /\.(ts|tsx|js|jsx)$/.test(f))
  .map(f => {
    const abs = resolve(ROOT, f)
    return relative(PG_ROOT, abs)
  })
  .filter(f => !f.startsWith('..')) // only files inside apps/web

if (eslintFiles.length > 0) {
  const quotedFiles = eslintFiles.map(f => `"${f}"`).join(' ')
  const eslintResult = run(
    `npx eslint --format json ${quotedFiles} 2>/dev/null || true`,
    { cwd: PG_ROOT, timeout: 60000 }
  )

  let eslintData = null
  try {
    const stdout = eslintResult.output.trim()
    if (stdout.startsWith('[')) {
      eslintData = JSON.parse(stdout)
    }
  } catch {
    // parse failed — non-JSON output
  }

  if (eslintData === null) {
    findings.push({
      severity: 'info',
      category: 'code_quality',
      title: 'ESLint check skipped — config needs migration to ESLint v9 format',
      description:
        'ESLint v9 requires a flat config file (eslint.config.mjs) but this project uses ' +
        '.eslintrc.json (legacy format). Additionally, `next lint` was removed in Next.js v16. ' +
        'The ESLint check was skipped. Once the config is migrated, this check will ' +
        'automatically produce lint findings.',
      file_path: 'apps/web/.eslintrc.json',
      line_start: 1,
      line_end: 1,
      code_snippet: eslintResult.output.slice(0, 300).trim(),
      recommendation:
        'Migrate apps/web/.eslintrc.json to apps/web/eslint.config.mjs for ESLint v9. ' +
        'See https://eslint.org/docs/latest/use/configure/migration-guide for the guide.',
    })
  } else {
    // Group by ruleId per file (first occurrence only)
    for (const fileResult of eslintData) {
      const byRule = new Map()
      for (const msg of fileResult.messages || []) {
        const ruleId = msg.ruleId || 'parse-error'
        if (!byRule.has(ruleId)) {
          byRule.set(ruleId, msg)
        }
      }

      const relFilePath = fileResult.filePath
        ? fileResult.filePath.replace(ROOT + '/', '')
        : 'unknown'

      for (const [ruleId, msg] of byRule) {
        findings.push({
          severity: msg.severity === 2 ? 'medium' : 'low',
          category: 'code_quality',
          title: `ESLint: ${ruleId}`,
          description: `${msg.message} (rule: ${ruleId})`,
          file_path: relFilePath,
          line_start: msg.line || 1,
          line_end: msg.endLine || msg.line || 1,
          code_snippet: msg.source || '',
          recommendation:
            `Fix the ${ruleId} lint issue. Run: cd apps/web && npx eslint "${relFilePath}" ` +
            `to see all occurrences in this file.`,
        })
      }
    }
  }
}

// ── Check 3: TypeScript type check ──────────────────────────────────────────
//
// Runs tsc --noEmit and filters output to errors in scope files only.
// Out-of-scope errors produce a single info finding rather than polluting
// the story's findings with unrelated type errors.

// tsc --noEmit always exits non-zero when there are errors, so ok:false is expected.
// Capture full output (no pipe) and slice in JavaScript.
const tscResult = run('npx tsc --noEmit 2>&1', {
  cwd: PG_ROOT,
  timeout: 120000,
})
const tscOutput = tscResult.output.trim().split('\n').slice(0, 100).join('\n')

if (tscOutput) {
  // Parse TS error lines: "path/to/file.ts(10,5): error TS2345: message"
  const tsErrorPattern = /^(.+\.tsx?)\((\d+),\d+\): error (TS\d+): (.+)$/gm
  let tsMatch
  const inScopeErrors = []
  let outOfScopeCount = 0

  while ((tsMatch = tsErrorPattern.exec(tscOutput)) !== null) {
    const [, rawPath, lineStr, code, message] = tsMatch
    const normalizedPath = rawPath.replace(/\\/g, '/')

    const matchedScope = scopeFiles.find(sf => {
      const sfNorm = sf.replace(/\\/g, '/')
      // Match by full path suffix or by basename
      return (
        normalizedPath.endsWith(sfNorm) ||
        sfNorm.endsWith(normalizedPath) ||
        basename(normalizedPath) === basename(sfNorm)
      )
    })

    if (matchedScope) {
      inScopeErrors.push({
        file_path: matchedScope,
        line: parseInt(lineStr),
        code,
        message,
      })
    } else {
      outOfScopeCount++
    }
  }

  for (const err of inScopeErrors) {
    findings.push({
      severity: 'high',
      category: 'correctness',
      title: `TypeScript ${err.code} — ${err.message.slice(0, 80)}`,
      description:
        `TypeScript strict mode reports: ${err.message} (${err.code}). ` +
        `This indicates a real type mismatch or missing property that could cause a runtime error.`,
      file_path: err.file_path,
      line_start: err.line,
      line_end: err.line,
      code_snippet: `${err.code}: ${err.message}`,
      recommendation:
        'Fix the TypeScript type error. Strict mode is enabled in this project — ' +
        'type errors reflect real correctness issues, not just style preferences.',
    })
  }

  if (outOfScopeCount > 0 && inScopeErrors.length === 0) {
    findings.push({
      severity: 'info',
      category: 'correctness',
      title: `TypeScript has ${outOfScopeCount} error(s) outside certification scope`,
      description:
        `tsc --noEmit reports ${outOfScopeCount} TypeScript error(s) in files not in this ` +
        `story's manifest. These are not counted toward this story's certification status.`,
      file_path: scopeFiles[0] || 'apps/web',
      line_start: 1,
      line_end: 1,
      code_snippet: tscOutput.slice(0, 300),
      recommendation: 'Address TypeScript errors in the broader codebase when time allows.',
    })
  }
}

// ── Check 4: Pattern-based static checks ────────────────────────────────────
//
// Delegates to cert-static-checks.mjs which checks for:
//   - PostgREST .or()/.filter() with embedded SQL subqueries
//   - Security headers (token/nonce/secret) declared but never used in a conditional
//   - createAdminClient() without subaccount_id ownership filter
//
// All findings from this script are pre-confirmed CRITICAL.

const staticScript = join(__dirname, 'cert-static-checks.mjs')
if (existsSync(staticScript)) {
  const scopeArg = scopeFiles.map(f => `"${f}"`).join(' ')
  const staticResult = run(`node "${staticScript}" ${scopeArg}`, {
    cwd: ROOT,
    timeout: 30000,
  })

  let staticFindings = []
  try {
    const stdout = staticResult.output.trim()
    if (stdout.startsWith('[')) {
      staticFindings = JSON.parse(stdout)
    } else {
      throw new Error('Non-JSON output: ' + stdout.slice(0, 100))
    }
  } catch (e) {
    staticFindings = [
      {
        severity: 'critical',
        category: 'correctness',
        title: 'Static check script failed to run',
        description:
          `cert-static-checks.mjs produced invalid output or threw an error. ` +
          `Error: ${e.message}. Pattern checks for PostgREST, security headers, ` +
          `and admin client bypass were NOT performed.`,
        file_path: staticScript.replace(ROOT + '/', ''),
        line_start: 1,
        line_end: 1,
        code_snippet: staticResult.output.slice(0, 200),
        recommendation:
          'Investigate the cert-static-checks.mjs error before proceeding with certification.',
      },
    ]
  }

  findings.push(...staticFindings)
} else {
  findings.push({
    severity: 'critical',
    category: 'correctness',
    title: 'cert-static-checks.mjs not found — static pattern checks skipped',
    description:
      `Expected script at ${staticScript.replace(ROOT + '/', '')} but it does not exist. ` +
      `Static pattern checks (PostgREST subqueries, unused security headers, admin client ` +
      `RLS bypass) were not run.`,
    file_path: '.claude/scripts/cert-static-checks.mjs',
    line_start: 1,
    line_end: 1,
    code_snippet: '',
    recommendation: 'Ensure .claude/scripts/cert-static-checks.mjs exists in the repository.',
  })
}

// ── Output ───────────────────────────────────────────────────────────────────

process.stdout.write(JSON.stringify(findings, null, 2) + '\n')
