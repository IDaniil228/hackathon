export function radialDistance(cosine: number) {
  return (1 - Math.max(-1, Math.min(1, cosine))) / 2;
}
