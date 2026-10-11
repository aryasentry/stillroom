export class FrameMeter {
  frames = 0; samples: number[] = [];
  private previous: number | null = null;
  tick(now: number, rendered: boolean) {
    if (rendered) {
      this.frames++;
      if (this.previous !== null) { this.samples.push(now - this.previous); if (this.samples.length > 240) this.samples.shift(); }
      this.previous = now;
    } else this.previous = null;
  }
}
