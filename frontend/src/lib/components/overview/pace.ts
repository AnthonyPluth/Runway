/** GET /api/month_pace: this month's spending against the same point last month (ThisMonth's chart, SummaryTiles' pace). */
export interface Pace { month: string; prev_month: string; this: number[]; last: number[]; spent: number; last_same_point: number; last_total: number }
