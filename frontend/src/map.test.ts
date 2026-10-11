import { expect, it } from "vitest";
import { radialDistance } from "./map";

it("сохраняет точное расстояние к запросу на общей шкале", () => {
  expect(radialDistance(1)).toBe(0);
  expect(radialDistance(0)).toBe(0.5);
  expect(radialDistance(-1)).toBe(1);
});
