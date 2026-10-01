import { fmt, fmt0 } from "$lib/format";

/** "about $42 a day" (cents under $10, where they matter). */
export const perDay = (v: number) => `about ${v >= 10 ? fmt0(v) : fmt(v)} a day`;
