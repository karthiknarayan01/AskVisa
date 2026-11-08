// The fine, interlaced rosette printed on passport and visa pages to make them hard to copy:
// hypotrochoid curves, each drawn several times with a small rotation.

import { useMemo } from "react";

function hypotrochoid(R: number, r: number, d: number, steps = 1400): string {
  const turns = r / gcd(R, r);
  const points: string[] = [];
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * Math.PI * 2 * turns;
    const x = (R - r) * Math.cos(t) + d * Math.cos(((R - r) / r) * t);
    const y = (R - r) * Math.sin(t) - d * Math.sin(((R - r) / r) * t);
    points.push(`${x.toFixed(2)},${y.toFixed(2)}`);
  }
  return `M${points.join("L")}`;
}

function gcd(a: number, b: number): number {
  return b ? gcd(b, a % b) : a;
}

export function Guilloche({ className }: { className?: string }) {
  const rings = useMemo(
    () => [hypotrochoid(200, 56, 90), hypotrochoid(150, 46, 64), hypotrochoid(96, 34, 30)],
    [],
  );
  return (
    <svg className={className} viewBox="-260 -260 520 520" aria-hidden="true">
      {rings.map((d, ring) =>
        [0, 4, 8].map((angle) => (
          <path key={`${ring}-${angle}`} d={d} transform={`rotate(${angle + ring * 7})`} />
        )),
      )}
    </svg>
  );
}
