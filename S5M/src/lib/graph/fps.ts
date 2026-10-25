export interface FpsSample {
  frames: number;
  dropped: number;
  /** Mean frame interval, ms. */
  meanInterval: number;
  p50Interval: number;
  p95Interval: number;
  worstInterval: number;
  /** Derived from the median interval, which is what a reader perceives as smoothness. */
  fps: number;
}

/**
 * requestAnimationFrame frame sampler for the UM-CAP-002 thresholds ("≥ 30 fps").
 *
 * Measured as frame intervals rather than a rAF counter: 30 fps on a 60 Hz display means a
 * presented frame every other vsync, and dropping below 16.7 ms of work per frame is exactly what
 * the requirement is about. The median is reported (not the mean) because one long frame during a
 * gesture is a hitch, and an average hides it.
 */
export function startFrameSampler(): { stop: () => FpsSample } {
  const intervals: number[] = [];
  let last = performance.now();
  let raf = 0;
  let stopped = false;

  const tick = (now: number) => {
    if (stopped) return;
    intervals.push(now - last);
    last = now;
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);

  return {
    stop: () => {
      stopped = true;
      cancelAnimationFrame(raf);
      const sorted = [...intervals].sort((a, b) => a - b);
      const at = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))] ?? 0;
      const sum = intervals.reduce((s, v) => s + v, 0);
      const median = at(0.5);
      return {
        frames: intervals.length,
        dropped: intervals.filter((v) => v > 33).length,
        meanInterval: intervals.length ? sum / intervals.length : 0,
        p50Interval: median,
        p95Interval: at(0.95),
        worstInterval: sorted[sorted.length - 1] ?? 0,
        fps: median > 0 ? Math.round(1000 / median) : 0,
      };
    },
  };
}

declare global {
  interface Window {
    __s5mBench?: { start: typeof startFrameSampler };
  }
}

if (typeof window !== 'undefined') window.__s5mBench = { start: startFrameSampler };
