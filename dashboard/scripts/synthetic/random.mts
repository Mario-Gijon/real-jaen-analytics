/** Seeded pseudo-random numbers (mulberry32): same seed, same season. */
export type Rng = {
  /** Uniform in [0, 1). */
  next(): number;
  /** Integer in [min, max]. */
  int(min: number, max: number): number;
  chance(probability: number): boolean;
  pick<T>(items: readonly T[]): T;
  /** Picks one item with probability proportional to its weight. */
  weighted<T>(items: readonly T[], weight: (item: T) => number): T;
  shuffle<T>(items: readonly T[]): T[];
  poisson(mean: number): number;
  binomial(trials: number, probability: number): number;
};

export function createRng(seed: number): Rng {
  let state = seed >>> 0;
  const next = () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const int = (min: number, max: number) => min + Math.floor(next() * (max - min + 1));
  return {
    next,
    int,
    chance: (probability) => next() < probability,
    pick: (items) => items[int(0, items.length - 1)],
    weighted(items, weight) {
      const total = items.reduce((sum, item) => sum + weight(item), 0);
      let target = next() * total;
      for (const item of items) {
        target -= weight(item);
        if (target < 0) return item;
      }
      return items[items.length - 1];
    },
    shuffle(items) {
      const copy = [...items];
      for (let i = copy.length - 1; i > 0; i--) {
        const j = int(0, i);
        [copy[i], copy[j]] = [copy[j], copy[i]];
      }
      return copy;
    },
    poisson(mean) {
      const limit = Math.exp(-mean);
      let k = 0;
      let product = next();
      while (product > limit) {
        k++;
        product *= next();
      }
      return k;
    },
    binomial(trials, probability) {
      let k = 0;
      for (let i = 0; i < trials; i++) if (next() < probability) k++;
      return k;
    },
  };
}
