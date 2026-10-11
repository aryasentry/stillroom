import { expect, it } from 'vitest';
import { FrameMeter } from './frameMeter';
it('counts actual rendered frames and excludes intentional idle gaps from cadence samples', () => {
  const meter = new FrameMeter();
  meter.tick(0, true); meter.tick(16, true); meter.tick(32, false); meter.tick(5000, false);
  meter.tick(5016, true); meter.tick(5036, true);
  expect(meter.frames).toBe(4); expect(meter.samples).toEqual([16, 20]);
});
