import { describe, expect, it } from 'vitest';
import { filesUnder, read } from './walk';

// UM-TECH-003 requires the tech-selection record to live in the repository. A record nobody can
// violate is a convention, not a rule — this test is what makes docs/adr/ a convention.

const SECTIONS = ['状态', '日期', '决策者', '背景', '备选方案', '决策', '验收对应', '后果', '复审触发'];
const STATUSES = ['提议', '通过', '已废弃', '被'];

const adrFiles = filesUnder('docs/adr', (p) => /^docs\/adr\/\d{3}-.+\.md$/.test(p) && !p.includes('000-template'));

describe('UM-TECH-003 · 架构决策记录', () => {
  it('the directory exists and is indexed', () => {
    expect(filesUnder('docs/adr').includes('docs/adr/README.md')).toBe(true);
    expect(filesUnder('docs/adr').includes('docs/adr/000-template.md')).toBe(true);
  });

  it('at least one decision is recorded', () => {
    expect(adrFiles.length).toBeGreaterThan(0);
  });

  it.each(adrFiles)('%s carries the nine mandatory sections', (file) => {
    const body = read(file);
    for (const s of SECTIONS) {
      expect(body, `${file} missing section 「${s}」`).toContain(s);
    }
  });

  it.each(adrFiles)('%s has a valid status token', (file) => {
    const body = read(file);
    const line = body.split(/\r?\n/).find((_, i, a) => a[i - 1]?.trim() === '状态');
    expect(line, `${file}: 「状态」 must be a definition line following the 状态 label`).toBeTruthy();
    expect(STATUSES.some((s) => line!.includes(s)), `${file}: unknown status in 「${line}」`).toBe(true);
  });

  it.each(adrFiles)('%s cites at least one SRS clause', (file) => {
    expect(read(file)).toMatch(/UM-|CP-|BP-|§/);
  });

  it('every ADR appears in the README index table', () => {
    const index = read('docs/adr/README.md');
    for (const f of adrFiles) {
      expect(index, `${f} is not listed in docs/adr/README.md`).toContain(f.split('/').pop()!.replace(/\.md$/, ''));
    }
  });
});
