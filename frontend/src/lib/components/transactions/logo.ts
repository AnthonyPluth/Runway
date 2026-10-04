import type { Brand } from "$lib/types";
import type { Tx } from "./types";

// A transaction's logo: its merchant's (`t.logo`), else, for a card's payment, which has no merchant, the logo of the
// bank of the card it pays (`t.logo_account`: the paying account's own when Runway can't tell which card). `bank` is
// that account's brand (for its letter when it has no logo); undefined for anything else.
export function txLogo(t: Pick<Tx, "logo" | "logo_account">, brands: Record<string, Brand> | undefined):
  { src: string | null; bank: Brand | undefined } {
  const bank = t.logo_account ? brands?.[t.logo_account] : undefined;
  return { src: t.logo || bank?.src || null, bank };
}
