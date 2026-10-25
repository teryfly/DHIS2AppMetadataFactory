import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

/** Every file under `sub` (repo-relative), as repo-relative posix paths. */
export function filesUnder(sub: string, filter?: (p: string) => boolean): string[] {
  const out: string[] = [];
  const stack = [join(ROOT, sub)];
  while (stack.length) {
    const dir = stack.pop()!;
    for (const name of readdirSync(dir)) {
      const abs = join(dir, name);
      if (statSync(abs).isDirectory()) stack.push(abs);
      else if (!filter || filter(relative(ROOT, abs).split('\\').join('/'))) out.push(relative(ROOT, abs).split('\\').join('/'));
    }
  }
  return out.sort();
}

export function read(pathFromRoot: string): string {
  return readFileSync(join(ROOT, pathFromRoot), 'utf8');
}

/** Files whose source matches `re`, as `{ path, line, text }`. */
export function matches(paths: string[], re: RegExp): { path: string; line: number; text: string }[] {
  const hits: { path: string; line: number; text: string }[] = [];
  for (const p of paths) {
    const body = read(p);
    // A fresh RegExp per line: a /g regex would advance lastIndex across lines and skip hits.
    const perLine = new RegExp(re.source, re.flags.replace('g', ''));
    body.split(/\r?\n/).forEach((text, i) => {
      if (perLine.test(text)) hits.push({ path: p, line: i + 1, text: text.trim() });
    });
  }
  return hits;
}
