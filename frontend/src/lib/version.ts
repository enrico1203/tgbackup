/** The version of the interface, and the comparison the update banner runs on.
 *
 *  The value is baked into the bundle by the frontend Dockerfile, out of the git tag
 *  being released. A build that does not set it is "dev": a working copy has nothing
 *  to compare itself against and must never claim to be out of date. */
export const APP_VERSION: string = import.meta.env.VITE_APP_VERSION ?? "dev";

const SEMVER = /^(\d+)\.(\d+)\.(\d+)$/;

function parse(value: string | null | undefined): number[] | null {
  const match = SEMVER.exec(value ?? "");
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

/** True only when both versions are readable and the published one is higher. Anything
 *  unknown, unparsable or ahead of what is published answers false: the banner exists
 *  to report a certainty, and a doubt shown as a warning is a warning nobody trusts. */
export function isOutdated(
  current: string | null | undefined,
  latest: string | null | undefined,
): boolean {
  const mine = parse(current);
  const theirs = parse(latest);
  if (!mine || !theirs) return false;
  for (let i = 0; i < 3; i += 1) {
    if (theirs[i] !== mine[i]) return theirs[i] > mine[i];
  }
  return false;
}

/** The higher of the two published versions, which is what the banner announces. The
 *  two images are published by the same tag, so they only differ if one half of a
 *  release failed to build. */
export function highest(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return isOutdated(a, b) ? b : a;
}
